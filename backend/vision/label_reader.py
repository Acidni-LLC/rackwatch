"""Muse Spark label reading (DESIGN.md §8.2).

One rack-frame call per image, then batched crop reads (≤6 crops per call,
each padded 8% plus a full-image thumbnail). Devices below 0.6 confidence
are re-read once at high effort. U positions come from deterministic math;
Spark's own U read wins when the two agree within 1, else needs_review.
"""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Any

from PIL import Image, ImageDraw

from vision import prompts as P
from vision.sam import Box, grid_to_pixels

BATCH_SIZE = 6
CROP_PAD = 0.08
CONFIDENCE_THRESHOLD = 0.6
SERIAL_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,19}$")

VALID_TYPES = {"switch", "server", "firewall", "router", "ups", "pdu",
               "patch_panel", "storage", "kvm", "blank", "other"}


def encode_image(image: Image.Image, fmt: str = "JPEG") -> str:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, fmt, quality=88)
    return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"


def crop_box(image: Image.Image, box: Box, pad: float = CROP_PAD) -> Image.Image:
    w, h = image.size
    x1, y1, x2, y2 = box
    dx, dy = (x2 - x1) * pad, (y2 - y1) * pad
    return image.crop((max(0, int(x1 - dx)), max(0, int(y1 - dy)),
                       min(w, int(x2 + dx)), min(h, int(y2 + dy))))


def thumbnail_with_boxes(image: Image.Image, boxes: list[Box], max_dim: int = 768) -> Image.Image:
    thumb = image.copy()
    thumb.thumbnail((max_dim, max_dim))
    sx, sy = thumb.size[0] / image.size[0], thumb.size[1] / image.size[1]
    draw = ImageDraw.Draw(thumb)
    for (x1, y1, x2, y2) in boxes:
        draw.rectangle([x1 * sx, y1 * sy, x2 * sx, y2 * sy], outline="red", width=2)
    return thumb


def y_to_u(y: float, top: float, bottom: float, total_u: int) -> int:
    """Image y (pixels, down-positive) to rack U (1 is the bottom)."""
    if bottom <= top:
        return 1
    frac = min(0.9999, max(0.0, (y - top) / (bottom - top)))
    return max(1, min(total_u, total_u - int(frac * total_u)))


def box_to_u(box: Box, top: float, bottom: float, total_u: int) -> tuple[int, int]:
    """(u_start, u_end) from the box edges: bottom edge is u_start."""
    u_bottom = y_to_u(box[3], top, bottom, total_u)
    u_top = y_to_u(box[1], top, bottom, total_u)
    return (min(u_bottom, u_top), max(u_bottom, u_top))


def u_from_pixels(y_grid: float, image_h: int, top_grid: float,
                  bottom_grid: float, total_u: int) -> int:
    top = top_grid * image_h / 1000
    bottom = bottom_grid * image_h / 1000
    return y_to_u(y_grid * image_h / 1000, top, bottom, total_u)


def _text_format(schema: dict) -> dict:
    return {"format": {"type": "json_schema", "name": "vision", "strict": True, "schema": schema}}


def _loads_with_repair(client: Any, resp: Any, schema: dict, purpose: str) -> tuple[dict, list[str]]:
    """Parse a Spark JSON response; one verbatim-rewrite retry when truncated."""
    try:
        return json.loads(resp.output_text), []
    except json.JSONDecodeError:
        pass
    repair = client.responses(
        purpose=purpose,
        model=client.settings.spark_model,
        instructions="Return JSON only.",
        input=[{"role": "user", "content": [
            {"type": "input_text",
             "text": "Repeat your previous answer verbatim as one valid JSON object: "},
            {"type": "input_text", "text": resp.output_text[:6000]},
        ]}],
        effort="low",
        max_output_tokens=4000,
        text=_text_format(schema),
    )
    return json.loads(repair.output_text), [repair.call_id]


def read_rack_frame(client: Any, image_url: str) -> tuple[dict, str]:
    resp = client.responses(
        purpose="rack_frame",
        model=client.settings.spark_model,
        instructions=P.RACK_FRAME_SYSTEM,
        input=[{"role": "user", "content": [
            {"type": "input_image", "image_url": image_url},
            {"type": "input_text", "text": P.RACK_FRAME_PROMPT},
        ]}],
        effort="low",
        max_output_tokens=2000,
        text=_text_format(P.FRAME_JSON_SCHEMA),
    )
    return json.loads(resp.output_text), resp.call_id


def ground_devices(client: Any, image_url: str, image_w: int, image_h: int) -> tuple[list[Box], str]:
    """Ask Spark to ground every device on the 0-1000 grid; convert to pixels."""
    content = [
        {"type": "input_image", "image_url": image_url},
        {"type": "input_text", "text": P.GROUNDING_PROMPT},
    ]
    resp = client.responses(
        purpose="spark_grounding",
        model=client.settings.spark_model,
        instructions=P.GROUNDING_SYSTEM,
        input=[{"role": "user", "content": content}],
        effort="low",
        max_output_tokens=4000,
        text=_text_format(P.GROUNDING_JSON_SCHEMA),
    )
    parsed, repair_calls = _loads_with_repair(client, resp, P.GROUNDING_JSON_SCHEMA,
                                                  "spark_grounding_repair")
    calls = [resp.call_id, *repair_calls]
    boxes = [grid_to_pixels(item["bbox"], image_w, image_h)
             for item in parsed.get("devices", []) if "bbox" in item]
    return boxes, calls


def _label_call(client: Any, image: Image.Image, batch: list[tuple[str, Box]],
                prompt: str, system: str, effort: str) -> tuple[list[dict], str]:
    boxes = [box for _, box in batch]
    thumb_url = encode_image(thumbnail_with_boxes(image, boxes))
    content: list[dict] = [{"type": "input_text", "text": prompt}]
    for crop_id, box in batch:
        content.append({"type": "input_text", "text": f"crop {crop_id}:"})
        content.append({"type": "input_image", "image_url": encode_image(crop_box(image, box))})
    content.append({"type": "input_text", "text": "full-rack thumbnail with boxes:"})
    content.append({"type": "input_image", "image_url": thumb_url})
    resp = client.responses(
        purpose="label_read",
        model=client.settings.spark_model,
        instructions=system,
        input=[{"role": "user", "content": content}],
        effort=effort,
        max_output_tokens=4000,
        text=_text_format(P.LABEL_JSON_SCHEMA),
    )
    parsed, repair_calls = _loads_with_repair(client, resp, P.LABEL_JSON_SCHEMA,
                                              "label_read_repair")
    return parsed.get("devices", []), [resp.call_id, *repair_calls]


def read_labels(client: Any, image: Image.Image, boxes: list[Box]) -> tuple[dict[str, dict], list[str]]:
    """Batched reads at low effort; one high-effort re-read per weak device."""
    calls: list[str] = []
    crop_ids = [f"b{i}" for i in range(len(boxes))]
    merged: dict[str, dict] = {}
    for start in range(0, len(boxes), BATCH_SIZE):
        batch = list(zip(crop_ids[start:start + BATCH_SIZE], boxes[start:start + BATCH_SIZE],
                         strict=True))
        items, batch_calls = _label_call(client, image, batch, P.CROP_PROMPT, P.LABEL_SYSTEM, "low")
        calls.extend(batch_calls)
        for item in items:
            if item.get("crop_id") in crop_ids:
                merged[item["crop_id"]] = item
    for crop_id, box in zip(crop_ids, boxes, strict=True):
        item = merged.get(crop_id, {"crop_id": crop_id, "device_type": "other"})
        if _confidence(item) < CONFIDENCE_THRESHOLD:
            items, reread_calls = _label_call(client, image, [(crop_id, box)],
                                              P.REREAD_PROMPT, P.REREAD_SYSTEM, "high")
            calls.extend(reread_calls)
            for fresh in items:
                if fresh.get("crop_id") == crop_id:
                    merged[crop_id] = fresh
    return merged, calls


def _confidence(item: dict) -> float:
    fields = item.get("field_confidence") or {}
    vals = [v for v in fields.values() if isinstance(v, (int, float))]
    if vals:
        return sum(vals) / len(vals)
    return 1.0 if any(item.get(k) for k in ("make", "model", "serial", "hostname")) else 0.0


def serial_ok(serial: str | None) -> bool:
    return serial is None or bool(SERIAL_RE.match(serial))


def coerce_device_type(raw: Any) -> str:
    return raw if raw in VALID_TYPES else "other"
