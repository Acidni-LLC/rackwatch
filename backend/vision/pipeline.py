"""Rack photo to CMDB pipeline (DESIGN.md §8.1-8.2, §16).

``run_rack_pipeline(image_path, client, rack_id="RACK-A", seed_path=None)``
returns a validated ``RackScanResult``. Flow: SAM sticker pass (one tight box
per asset sticker) seeding per-row SAM box prompts, with the §8.1 concept
prompt and Spark-grounding + SAM box-prompt fallback when the rack carries no
stickers; sticker containment, NMS and overlap resolve; rail-anchored frame
call; batched label reads; one robust rail line fit from all rail reads so a
single bad read cannot shift a device; at most one batch of high-effort
re-reads for fit-doubtful or serial-less crops; U assignment from the single
line (half-coverage per unit); implausible boxes take their span from their
own label read; decided bboxes snap to the fitted grid inside the rails
(SAM still finds the devices, the boxes are only snapped afterwards);
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
    coerce_device_type,
    combine_confidence,
    encode_image,
    ground_devices,
    read_initial,
    read_rack_frame,
    reread_selected,
    serial_ok,
)
from vision.overlay import render_overlay
from vision.railfit import (
    Fit,
    assign_units,
    fit_rail,
    frame_fit,
    label_pairs,
    snap_box,
)
from vision.sam import (
    MIN_STICKER_ROWS,
    SAM_MAX_OBJECTS,
    attribute_to_seeds,
    box_prompt_input,
    cluster_rows,
    concept_input,
    contains,
    fallback_row_boxes,
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
                    total_u: int, rail_left_px: float | None = None,
                    rail_right_px: float | None = None) -> tuple[list, list[str], list[bool]]:
    """Sticker rows seed tight SAM prompts; wide inter-row gaps get seeds too
    in case a sticker was missed. Multi-row boxes are split, same-sticker
    splits merged, stickerless row boxes dropped. Rows the segmenter still
    misses get a last-resort fallback box, so a scan never silently loses a
    stickered device. Returns (boxes, call_ids, is_gap), sorted top-to-bottom
    but with overlaps unresolved: the caller drops unreadable gap boxes first
    (after the label reads), then resolves, so a box that will be dropped can
    never clip a real neighbor. Concept + grounding fallbacks cover
    stickerless racks.
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
            boxes.extend(fallback_row_boxes(rows, boxes, image_w, image_h,
                                            rail_left_px, rail_right_px,
                                            rail_top_px, rail_bottom_px, total_u))
            is_gap.extend([False] * (len(boxes) - len(is_gap)))
            order = sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][0]))
            return [boxes[i] for i in order], calls, [is_gap[i] for i in order]
    boxes = filter_narrow(_sam_concept(client, image_url, image_w, image_h,
                                       P.CONCEPT_PROMPTS[0], calls), image_w)
    if len(boxes) < 4:
        boxes = filter_narrow(_grounded_boxes(client, image_url, image_w, image_h, calls),
                              image_w)
    boxes = nms(boxes)
    return boxes, calls, [False] * len(boxes)


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


MAX_REREADS = 6  # at most one batch of slow high-effort re-reads per scan


def select_rereads(boxes: list, reads: dict[str, dict], fit: Fit | None,
                   max_n: int = MAX_REREADS) -> list[int]:
    """Box indices worth one high-effort re-read: crops the batch dropped,
    reads whose own label falls outside the fit span, null/invalid serials.
    Fit-doubt first, bounded so a scan stays fast."""
    missing = [i for i in range(len(boxes)) if f"b{i}" not in reads]
    doubtful, nullser = [], []
    for i, box in enumerate(boxes):
        item = reads.get(f"b{i}", {})
        label = item.get("u_label")
        if (fit is not None and isinstance(label, (int, float))
                and not isinstance(label, bool)):
            lo, hi = assign_units(fit, box[1], box[3])
            if label < lo or label > hi:
                doubtful.append(i)
        serial = item.get("serial")
        if serial is None or not serial_ok(serial):
            nullser.append(i)
    ordered: list[int] = []
    for i in missing + doubtful + nullser:
        if i not in ordered:
            ordered.append(i)
    return ordered[:max_n]


def rail_x_px(frame: dict, image_w: int) -> tuple[float, float] | None:
    """Rail-anchored horizontal extent in pixels; None when the frame call
    did not return rail x positions (older prompts)."""
    try:
        left = float(frame["rail_left_x"]) * image_w / 1000
        right = float(frame["rail_right_x"]) * image_w / 1000
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 <= left < right <= image_w):
        return None
    return left, right


def box_height_u(box: list, fit: Fit) -> float:
    """Box height measured in rack units on the fitted line."""
    return (box[3] - box[1]) / abs(fit.b) if fit.b else 0.0


def is_implausible_box(box: list, fit: Fit, median_h_u: float,
                       rails_x: tuple[float, float] | None,
                       rail_top_px: float, rail_bottom_px: float) -> bool:
    """A box SAM got plainly wrong: past the rack frame, wider than the rack,
    or far taller than its neighbors. Thresholds are relative to this scan's
    own rails and boxes, never to a fixed layout."""
    unit = abs(fit.b)
    if unit <= 0:
        return False
    if rails_x is not None:
        left, right = rails_x
        interior = right - left
        if interior > 0:
            if box[0] < left - 0.05 * interior or box[2] > right + 0.05 * interior:
                return True
            if (box[2] - box[0]) > 1.15 * interior:
                return True
    if box[1] < rail_top_px - 0.5 * unit or box[3] > rail_bottom_px + 0.5 * unit:
        return True
    return box_height_u(box, fit) > max(3.5, 2.5 * median_h_u)


def _valid_height(raw: Any) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return int(raw) if 1 <= int(raw) <= 8 else None


def _clamp_box(box: list, rails_x: tuple[float, float] | None,
               rail_top_px: float, rail_bottom_px: float,
               image_w: int, image_h: int) -> list:
    """Hard frame clamp: no box may extend outside the rack frame. Clamping
    only pulls edges back inside; it never reshapes a box to look right."""
    x1, y1, x2, y2 = (float(v) for v in box)
    if rails_x is not None:
        x1 = max(x1, rails_x[0])
        x2 = min(x2, rails_x[1])
    x1 = max(0.0, min(float(image_w - 1), x1))
    x2 = max(x1 + 1, min(float(image_w), x2))
    y1 = max(max(0.0, rail_top_px), min(float(image_h - 1), y1))
    y2 = max(y1 + 1, min(min(float(image_h), rail_bottom_px), y2))
    return [int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))]


def build_devices(rack_id: str, image_path: str, boxes: list, frame: dict,
                  reads: dict[str, dict], image_h: int, fit: Fit | None = None,
                  image_w: int | None = None) -> list[Device]:
    """U assignment from one global rail line: every device's top and bottom
    edges map through the same fit, so one bad read cannot shift its device
    (or any other). Each unit counts only past half coverage, so a loose edge
    crossing a rail line by a sliver adds no U. Without a fit the frame rails
    serve as the line. A box the segmenter got plainly wrong (past the frame
    or far taller than its neighbors) takes its span from its own u_label
    read instead and is flagged. Decided boxes are snapped to the grid: y
    from the fitted line, x across the rail interior. A box whose U
    disagrees with its label keeps its SAM box (clamped inside the frame) so
    a wrong U stays visibly wrong."""
    total_u = int(frame.get("total_u") or 42)
    if fit is None:
        top, bottom = rail_edges_px(frame, image_h)
        fit = frame_fit(top, bottom, total_u)
    rail_top, rail_bottom = rail_edges_px(frame, image_h)
    rails_x = rail_x_px(frame, image_w) if image_w else None
    heights = sorted(box_height_u(b, fit) for b in boxes)
    median_h = float(heights[len(heights) // 2]) if heights else 1.0
    if median_h <= 0:
        median_h = 1.0
    devices: list[Device] = []
    for i, box in enumerate(boxes):
        item = reads.get(f"b{i}", {})
        u_start, u_end = assign_units(fit, box[1], box[3])
        u_label = _int_or_none(item.get("u_label"))
        implausible = is_implausible_box(box, fit, median_h, rails_x,
                                         rail_top, rail_bottom)
        disagrees = u_label is not None and (u_label < u_start or u_label > u_end)
        needs_review = fit.large_residuals
        if implausible and u_label is not None and 1 <= u_label <= total_u:
            height = _valid_height(item.get("u_height")) or 1
            u_start, u_end = u_label, min(total_u, u_label + height - 1)
            disagrees = False
            needs_review = True
        elif implausible or disagrees:
            needs_review = True
        out_box: list = list(box)
        if rails_x is not None and image_w:
            if disagrees:
                out_box = _clamp_box(box, rails_x, rail_top, rail_bottom,
                                     image_w, image_h)
            else:
                snapped = list(snap_box(fit, u_start, u_end, rails_x[0], rails_x[1],
                                        image_w, image_h))
                out_box = _clamp_box(snapped, rails_x, rail_top, rail_bottom,
                                     image_w, image_h)
        confidence = combine_confidence(item)
        serial = item.get("serial")
        if confidence < 0.6 or not serial_ok(serial):
            needs_review = True
        devices.append(_make_device(rack_id, image_path, item, out_box, u_start, u_end,
                                    confidence, needs_review))
    return devices


def _make_device(rack_id: str, image_path: str, item: dict, box: list, u_start: int,
                 u_end: int, confidence: float, needs_review: bool) -> Device:
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


def _has_identifying_read(item: dict) -> bool:
    return any(item.get(key) is not None
               for key in ("hostname", "u_label", "serial"))


def drop_dead_gaps(boxes: list, is_gap: list[bool],
                   reads: dict[str, dict]) -> tuple[list, list[bool], dict[str, dict]]:
    """Remove gap-seeded boxes whose read came back empty: no sticker was ever
    there. They must go before overlap resolve, which would otherwise let a
    doomed box permanently clip a real neighbor. Surviving boxes are
    reindexed with their reads remapped to match; row boxes always survive."""
    keep = [i for i, gap in enumerate(is_gap)
            if not gap or _has_identifying_read(reads.get(f"b{i}", {}))]
    if len(keep) == len(boxes):
        return boxes, is_gap, reads
    remapped = {f"b{n}": reads.get(f"b{i}", {}) for n, i in enumerate(keep)}
    return [boxes[i] for i in keep], [is_gap[i] for i in keep], remapped


def resolve_with_flags(boxes: list, is_gap: list[bool]) -> tuple[list, list[bool]]:
    """Top-to-bottom sort plus overlap resolve, keeping gap flags aligned."""
    order = sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][0]))
    return (resolve_overlaps([boxes[i] for i in order]),
            [is_gap[i] for i in order])


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
    rails_x = rail_x_px(frame, w)

    boxes, seg_calls, is_gap = segment_devices(
        client, image_url, w, h, rail_top, rail_bottom, int(frame.get("total_u") or 42),
        *(rails_x or (None, None)))
    calls.extend(seg_calls)

    reads, batch_calls = read_initial(client, image, boxes)
    calls.extend(batch_calls)
    reads = drop_tied_gaps(reads, is_gap)
    boxes, is_gap, reads = drop_dead_gaps(boxes, is_gap, reads)
    boxes, is_gap = resolve_with_flags(boxes, is_gap)
    total_u = int(frame.get("total_u") or 42)
    rails = (rail_top, rail_bottom)
    fit = fit_rail(label_pairs(boxes, reads), total_u, frame=rails)
    jobs = select_rereads(boxes, reads, fit)
    if jobs:
        updates, recall_calls = reread_selected(
            client, image, [(f"b{i}", boxes[i]) for i in jobs])
        calls.extend(recall_calls)
        reads.update(updates)
        fit = fit_rail(label_pairs(boxes, reads), total_u, frame=rails) or fit

    devices = [d for d in build_devices(rack_id, image_path, boxes, frame, reads, h, fit, w)
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
