"""Rack photo to CMDB pipeline (DESIGN.md §8.1-8.2, §16).

``run_rack_pipeline(image_path, client, rack_id="RACK-A", seed_path=None)``
returns a validated ``RackScanResult``. Flow: SAM sticker pass (one tight box
per asset sticker) seeding per-row SAM box prompts, with the §8.1 concept
prompt and Spark-grounding + SAM box-prompt fallback when the rack carries no
stickers; sticker containment, NMS and overlap resolve; rail-anchored frame
call; batched label reads with high-effort re-reads; deterministic U mapping;
overlay; optional accuracy diff against ``cmdb_seed.json``.
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
    combine_confidence,
    encode_image,
    ground_devices,
    read_labels,
    read_rack_frame,
    serial_ok,
)
from vision.overlay import render_overlay
from vision.sam import (
    MIN_STICKER_ROWS,
    SAM_MAX_OBJECTS,
    attribute_to_seeds,
    box_prompt_input,
    cluster_rows,
    concept_input,
    contains,
    filter_narrow,
    gap_seed_boxes,
    keep_sticker_boxes,
    nms,
    nms_with_flags,
    parse_sam_boxes,
    resolve_overlaps,
    row_seed_boxes,
    split_multi_row_boxes,
    union_by_sticker,
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


def _row_centroid(row: list) -> tuple[float, float]:
    return (sum((b[0] + b[2]) / 2 for b in row) / len(row),
            sum((b[1] + b[3]) / 2 for b in row) / len(row))


def segment_devices(client: Any, image_url: str, image_w: int, image_h: int,
                    rail_top_px: float, rail_bottom_px: float,
                    total_u: int) -> tuple[list, list[str], list[bool]]:
    """Sticker rows seed tight SAM prompts; wide inter-row gaps get seeds too
    in case a sticker was missed. Multi-row boxes are split, same-sticker
    splits merged, stickerless row boxes dropped. Returns (boxes, call_ids,
    is_gap), sorted top-to-bottom with overlaps resolved. Concept + grounding
    fallbacks cover stickerless racks.
    """
    calls: list[str] = []
    stickers = _sam_concept(client, image_url, image_w, image_h, P.STICKER_PROMPT, calls)
    rows = [row for row in cluster_rows(stickers, image_h)
            if _row_centroid(row)[1] >= rail_top_px]
    flat = [s for row in rows for s in row]
    if len(rows) >= MIN_STICKER_ROWS:
        row_seeds = row_seed_boxes(rows, image_w, image_h)
        gap_seeds = gap_seed_boxes(rows, image_w, image_h, rail_top_px, rail_bottom_px,
                                   total_u, max_seeds=max(0, SAM_MAX_OBJECTS - len(row_seeds)))
        seeds = row_seeds + gap_seeds
        seed_cys = [_row_centroid([s])[1] for s in seeds]
        boxes = _sam_boxes(client, image_url, image_w, image_h, seeds, calls)
        boxes, is_gap = attribute_to_seeds(boxes, seed_cys,
                                           [False] * len(row_seeds) + [True] * len(gap_seeds))
        boxes = split_multi_row_boxes(boxes, rows)
        boxes, is_gap = attribute_to_seeds(boxes, seed_cys,
                                           [False] * len(row_seeds) + [True] * len(gap_seeds))
        row_boxes = union_by_sticker(
            keep_sticker_boxes([b for b, g in zip(boxes, is_gap, strict=True) if not g],
                               flat),
            flat,
        )
        boxes = row_boxes + [b for b, g in zip(boxes, is_gap, strict=True) if g]
        is_gap = [False] * len(row_boxes) + [True] * (len(boxes) - len(row_boxes))
        boxes.extend(_rescue_orphans(client, image_url, image_w, image_h, boxes, rows, calls))
        is_gap.extend([False] * (len(boxes) - len(is_gap)))
        if boxes:
            boxes, is_gap = nms_with_flags(boxes, is_gap)
            order = sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][0]))
            boxes, is_gap = [boxes[i] for i in order], [is_gap[i] for i in order]
            boxes = resolve_overlaps(boxes)
            return boxes, calls, is_gap
    boxes = filter_narrow(_sam_concept(client, image_url, image_w, image_h,
                                       P.CONCEPT_PROMPTS[0], calls), image_w)
    if len(boxes) < 4:
        boxes = filter_narrow(_grounded_boxes(client, image_url, image_w, image_h, calls),
                              image_w)
    return resolve_overlaps(nms(boxes)), calls, [False] * len(boxes)


def _grounded_boxes(client: Any, image_url: str, image_w: int, image_h: int,
                    calls: list[str]) -> list:
    grounded, grounding_calls = ground_devices(client, image_url, image_w, image_h)
    calls.extend(grounding_calls)
    if not grounded:
        return []
    return _sam_boxes(client, image_url, image_w, image_h, grounded, calls)


def _rescue_orphans(client: Any, image_url: str, image_w: int, image_h: int,
                    boxes: list, rows: list[list], calls: list[str]) -> list:
    """One tight SAM seed per sticker row that ended up with no box."""
    orphans: list[list] = []
    for row in rows:
        cx = sum((b[0] + b[2]) / 2 for b in row) / len(row)
        cy = sum((b[1] + b[3]) / 2 for b in row) / len(row)
        if not any(contains(b, cx, cy) for b in boxes):
            orphans.append(row)
    if not orphans:
        return []
    seeds = row_seed_boxes(orphans, image_w, image_h)
    rescued = _sam_boxes(client, image_url, image_w, image_h, seeds, calls)
    return keep_sticker_boxes(rescued, [s for row in orphans for s in row])


def _sam_concept(client: Any, image_url: str, image_w: int, image_h: int,
                 prompt: str, calls: list[str]):
    resp = client.responses(
        purpose="sam_segment",
        model=client.settings.sam_model,
        input=concept_input(prompt, image_url),
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


def rail_edges_px(frame: dict, image_h: int) -> tuple[float, float]:
    """Rail-anchored vertical extent in pixels, with the old outer-frame keys
    as fallback."""
    top_key = "rail_top_y" if "rail_top_y" in frame else "rack_top_y"
    bottom_key = "rail_bottom_y" if "rail_bottom_y" in frame else "rack_bottom_y"
    top = float(frame.get(top_key, 0)) * image_h / 1000
    bottom = float(frame.get(bottom_key, 1000)) * image_h / 1000
    return top, bottom


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def build_devices(rack_id: str, image_path: str, boxes: list, frame: dict,
                  reads: dict[str, dict], image_h: int) -> list[Device]:
    """Bottom-up U assignment. Devices stack: a box floating exactly 1U above
    a confident neighbor below, reading 2U tall, snaps down onto the stack.
    Giant single boxes re-anchor to their rail label. Gaps fall back to the
    label-then-geometry rules."""
    total_u = int(frame.get("total_u") or 42)
    top, bottom = rail_edges_px(frame, image_h)
    unit_px = (bottom - top) / total_u if total_u else 1
    parsed = []
    for i, box in enumerate(boxes):
        item = reads.get(f"b{i}", {})
        computed = box_to_u(box, top, bottom, total_u)
        u_label = _int_or_none(item.get("u_label"))
        try:
            u_height = max(1, int(item.get("u_height") or 0)
                           or max(1, round((box[3] - box[1]) / unit_px)))
        except (TypeError, ValueError):
            u_height = 1
        parsed.append({"box": box, "item": item, "computed": computed,
                       "u_label": u_label, "u_height": u_height,
                       "tall": (box[3] - box[1]) / unit_px > 2.5 if unit_px > 0 else False,
                       "confidence": combine_confidence(item)})
    fixed: list[tuple[int, int] | None] = [None] * len(boxes)
    devices: list[Device] = [None] * len(boxes)  # type: ignore[list-item]
    for i in reversed(range(len(boxes))):
        p = parsed[i]
        below = fixed[i + 1] if i + 1 < len(boxes) and parsed[i + 1]["confidence"] > 0 else None
        u_start, u_end, needs_review = _assign_u(p, below)
        fixed[i] = (u_start, u_end)
        confidence = p["confidence"]
        serial = p["item"].get("serial")
        if confidence < 0.6 or not serial_ok(serial):
            needs_review = True
        devices[i] = _make_device(rack_id, image_path, p, u_start, u_end,
                                  confidence, needs_review)
    return devices


def _assign_u(p: dict, below: tuple[int, int] | None) -> tuple[int, int, bool]:
    """Returns (u_start, u_end, needs_review). u_start is the lowest U.

    Labels beat geometry on disagreement: a rail number read off the image is
    more reliable than a drifted box edge, and a 2U-tall read means the label
    sits on the top unit. The stack anchor only confirms an agreeing box."""
    computed_start, _computed_end = p["computed"]
    u_label, u_height = p["u_label"], p["u_height"]
    if below is not None:
        anchor = below[1] + 1
        if anchor == computed_start and (u_label is None or u_label == computed_start):
            return computed_start, computed_start + u_height - 1, False
        if u_height == 2 and anchor == computed_start - 1:
            return computed_start - 1, computed_start, True
    if p["tall"] and u_label is not None and u_height <= 2:
        if u_height == 2:
            return u_label, u_label + 1, True
        return u_label - 1, u_label, True
    if u_label is not None and u_label == computed_start:
        return u_label, u_label + u_height - 1, False
    if u_height == 2 and u_label is not None:
        if below is not None and below[1] + 1 == u_label:
            return u_label, u_label + 1, True
        return u_label - 1, u_label, True
    if u_label is not None:
        return u_label, u_label + u_height - 1, True
    return computed_start, p["computed"][1], True


def _make_device(rack_id: str, image_path: str, p: dict, u_start: int, u_end: int,
                 confidence: float, needs_review: bool) -> Device:
    item, box = p["item"], p["box"]
    serial = item.get("serial")
    return Device(
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
        field_confidence={k: float(v) for k, v in (item.get("field_confidence") or {}).items()
                          if isinstance(v, (int, float))},
        bbox=BBox(x1=box[0], y1=box[1], x2=box[2], y2=box[3]),
        source_image=image_path,
        source="vision",
        needs_review=needs_review,
    )


def drop_tied_gaps(reads: dict[str, dict], is_gap: list[bool]) -> dict[str, dict]:
    """Drop gap-seeded reads that repeat a row box hostname: the gap crop
    caught a neighbor's sticker, and the row box owns that name."""
    row_names = {item.get("hostname") for i, item in
                 ((i, reads.get(f"b{i}", {})) for i in range(len(is_gap)))
                 if not is_gap[i] and item.get("hostname")}
    out = dict(reads)
    seen = set(row_names)
    for i, gap in enumerate(is_gap):
        if not gap:
            continue
        name = out.get(f"b{i}", {}).get("hostname")
        if name is not None and name in seen:
            out[f"b{i}"] = {}
        elif name is not None:
            seen.add(name)
    return out


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

    frame, frame_call = read_rack_frame(client, image_url)
    calls.append(frame_call)
    rail_top, rail_bottom = rail_edges_px(frame, h)

    boxes, seg_calls, is_gap = segment_devices(
        client, image_url, w, h, rail_top, rail_bottom, int(frame.get("total_u") or 42))
    calls.extend(seg_calls)

    reads, label_calls = read_labels(client, image, boxes)
    calls.extend(label_calls)
    reads = drop_tied_gaps(reads, is_gap)

    devices = [d for d in build_devices(rack_id, image_path, boxes, frame, reads, h)
               if d.confidence > 0.0]
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
