"""Rack photo to CMDB pipeline (DESIGN.md §8.1-8.2, §16).

``run_rack_pipeline(image_path, client, rack_id="RACK-A", seed_path=None)``
returns a validated ``RackScanResult``. Flow: SAM concept prompt, width
filter, Spark-grounding + SAM box-prompt fallback when fewer than 4 boxes
survive, NMS, top-to-bottom sort, rack-frame call, batched label reads with
high-effort re-reads, deterministic U mapping, overlay, optional accuracy
diff against ``cmdb_seed.json``.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

from app.schemas import BBox, Device, RackScanResult, device_id
from PIL import Image

from vision import prompts as P
from vision.label_reader import (
    box_to_u,
    coerce_device_type,
    encode_image,
    ground_devices,
    read_labels,
    read_rack_frame,
    serial_ok,
)
from vision.overlay import render_overlay
from vision.sam import (
    box_prompt_input,
    concept_input,
    filter_narrow,
    nms,
    parse_sam_boxes,
    sort_top_to_bottom,
)

MAX_SIDE = 2048


def load_image(image_path: str) -> tuple[Image.Image, int, int]:
    image = Image.open(image_path).convert("RGB")
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image, image.size[0], image.size[1]


def image_data_url(image_path: str, image: Image.Image) -> str:
    suffix = Path(image_path).suffix.lower()
    if suffix == ".png":
        buf = io.BytesIO()
        image.save(buf, "PNG")
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
    return encode_image(image)


def segment_devices(client: Any, image_url: str, image_w: int, image_h: int) -> tuple[list, list[str]]:
    """Concept sweep, then grounding + box-prompt fallback. Returns (boxes, call_ids)."""
    calls: list[str] = []
    boxes = filter_narrow(_sam_concept(client, image_url, image_w, image_h, calls), image_w)
    if len(boxes) < 4:
        grounded, grounding_calls = ground_devices(client, image_url, image_w, image_h)
        calls.extend(grounding_calls)
        if grounded:
            boxes = filter_narrow(
                _sam_boxes(client, image_url, image_w, image_h, grounded, calls), image_w)
    return sort_top_to_bottom(nms(boxes)), calls


def _sam_concept(client: Any, image_url: str, image_w: int, image_h: int, calls: list[str]):
    resp = client.responses(
        purpose="sam_segment",
        model=client.settings.sam_model,
        input=concept_input(P.CONCEPT_PROMPTS[0], image_url),
    )
    calls.append(resp.call_id)
    return parse_sam_boxes(resp.output_text, image_w, image_h)


def _sam_boxes(client: Any, image_url: str, image_w: int, image_h: int,
               hint_boxes: list, calls: list[str]):
    resp = client.responses(
        purpose="sam_segment",
        model=client.settings.sam_model,
        input=box_prompt_input(hint_boxes, image_w, image_h, image_url),
    )
    calls.append(resp.call_id)
    return parse_sam_boxes(resp.output_text, image_w, image_h)


def build_devices(rack_id: str, image_path: str, boxes: list, frame: dict,
                  reads: dict[str, dict], image_h: int) -> list[Device]:
    total_u = int(frame.get("total_u") or 42)
    top = float(frame.get("rack_top_y", 0)) * image_h / 1000
    bottom = float(frame.get("rack_bottom_y", 1000)) * image_h / 1000
    devices: list[Device] = []
    for i, box in enumerate(boxes):
        item = reads.get(f"b{i}", {})
        computed_start, computed_end = box_to_u(box, top, bottom, total_u)
        needs_review = False
        try:
            u_label = int(item.get("u_label")) if item.get("u_label") is not None else None
        except (TypeError, ValueError):
            u_label = None
        try:
            u_height = max(1, int(item.get("u_height") or 1))
        except (TypeError, ValueError):
            u_height = 1
        if u_label is not None and abs(u_label - computed_start) <= 1:
            u_start, u_end = u_label, u_label + u_height - 1
        else:
            u_start, u_end = computed_start, computed_end
            if u_label is not None:
                needs_review = True
        fields = item.get("field_confidence") or {}
        confs = [float(v) for v in fields.values() if isinstance(v, (int, float))]
        confidence = sum(confs) / len(confs) if confs else (
            1.0 if any(item.get(k) for k in ("make", "model", "serial", "hostname")) else 0.0)
        serial = item.get("serial")
        if confidence < 0.6 or not serial_ok(serial):
            needs_review = True
        devices.append(Device(
            id=device_id(rack_id, u_start),
            rack_id=rack_id,
            u_start=u_start,
            u_end=u_end,
            device_type=coerce_device_type(item.get("device_type")),
            make=item.get("make"),
            model=item.get("model"),
            serial=serial,
            hostname=item.get("hostname"),
            mgmt_ip=None,
            confidence=round(min(1.0, max(0.0, confidence)), 3),
            field_confidence={k: float(v) for k, v in fields.items()
                              if isinstance(v, (int, float))},
            bbox=BBox(x1=box[0], y1=box[1], x2=box[2], y2=box[3]),
            source_image=image_path,
            source="vision",
            needs_review=needs_review,
        ))
    return devices


def diff_accuracy(devices: list[Device], seed_path: str) -> dict:
    seed = {d["id"]: d for d in json.loads(Path(seed_path).read_text(encoding="utf-8"))}
    matched = [d for d in devices if d.id in seed]
    serial_ok_count = sum(1 for d in matched
                          if d.serial and d.serial == (seed[d.id].get("serial")))
    u_ok = sum(1 for d in matched if d.u_start == seed[d.id].get("u_start"))
    return {
        "devices_matched": len(matched),
        "devices_total": len(seed),
        "serial_matched": serial_ok_count,
        "u_matched": u_ok,
        "extra_devices": sorted(d.id for d in devices if d.id not in seed),
        "missing_devices": sorted(s for s in seed if s not in {d.id for d in devices}),
    }


def run_rack_pipeline(image_path: str, client: Any, rack_id: str = "RACK-A",
                      seed_path: str | None = None,
                      overlay_path: str | None = None) -> RackScanResult:
    image, w, h = load_image(image_path)
    image_url = image_data_url(image_path, image)
    calls: list[str] = []

    boxes, seg_calls = segment_devices(client, image_url, w, h)
    calls.extend(seg_calls)

    frame, frame_call = read_rack_frame(client, image_url)
    calls.append(frame_call)

    reads, label_calls = read_labels(client, image, boxes)
    calls.extend(label_calls)

    devices = build_devices(rack_id, image_path, boxes, frame, reads, h)
    accuracy = diff_accuracy(devices, seed_path) if seed_path else None

    out_overlay = overlay_path or str(Path(image_path).with_name(Path(image_path).stem + "_overlay.png"))
    render_overlay(image_path, devices, out_overlay)

    return RackScanResult(
        rack_id=rack_id,
        image_path=image_path,
        image_w=w,
        image_h=h,
        total_u=int(frame.get("total_u") or 42),
        devices=devices,
        overlay_path=out_overlay,
        calls=calls,
        accuracy=accuracy,
    )
