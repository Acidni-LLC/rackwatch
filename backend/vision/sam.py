"""SAM 3.1 device segmentation (DESIGN.md §5, §8.1).

Concept prompts run first; when fewer than 4 boxes survive the width
filter, Spark grounding boxes are sent back as one SAM box-prompt request.
Output boxes are in original image pixels; the token regex carries an
optional ``;c=`` confidence group (docs/preflight.json).
"""

from __future__ import annotations

import re
from typing import Any

BOX_RE = re.compile(
    r"(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=[\d.]+)?\|>"
)

Box = tuple[int, int, int, int]  # x1, y1, x2, y2 in image pixels


def parse_sam_boxes(text: str, image_w: int, image_h: int) -> list[Box]:
    """Parse SAM special-token text. Coordinates are rescaled from the token's
    own w/h to the image size, degenerate boxes are dropped."""
    boxes: list[Box] = []
    for match in BOX_RE.finditer(text or ""):
        _, x1, y1, x2, y2, w, h = (int(v) for v in match.groups())
        if w <= 0 or h <= 0:
            continue
        x1 = round(x1 * image_w / w)
        y1 = round(y1 * image_h / h)
        x2 = round(x2 * image_w / w)
        y2 = round(y2 * image_h / h)
        if x2 > x1 and y2 > y1:
            boxes.append((x1, y1, x2, y2))
    return boxes


def concept_input(prompt: str, image_url: str) -> list[dict[str, Any]]:
    return [{
        "role": "user",
        "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": image_url},
        ],
    }]


def box_prompt_text(boxes: list[Box], image_w: int, image_h: int) -> str:
    """One SAM box-prompt request: ``<0f>0<|box;...|>,1<|box;...|>``."""
    parts = [
        f"{i}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={image_w};h={image_h}|>"
        for i, (x1, y1, x2, y2) in enumerate(boxes)
    ]
    return "<0f>" + ",".join(parts)


def box_prompt_input(boxes: list[Box], image_w: int, image_h: int, image_url: str) -> list[dict]:
    return [{
        "role": "user",
        "content": [
            {"type": "input_text", "text": box_prompt_text(boxes, image_w, image_h)},
            {"type": "input_image", "image_url": image_url},
        ],
    }]


def filter_narrow(boxes: list[Box], image_w: int, frac: float = 0.4) -> list[Box]:
    """Discard boxes narrower than ``frac`` of the rack width (§8.1)."""
    return [b for b in boxes if (b[2] - b[0]) >= frac * image_w]


def iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def nms(boxes: list[Box], thresh: float = 0.5) -> list[Box]:
    """Greedy NMS, largest area wins; ties keep input order."""
    order = sorted(range(len(boxes)),
                   key=lambda i: (-((boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1])), i))
    kept_idx: list[int] = []
    for i in order:
        if all(iou(boxes[i], boxes[j]) <= thresh for j in kept_idx):
            kept_idx.append(i)
    return [boxes[i] for i in kept_idx]


def sort_top_to_bottom(boxes: list[Box]) -> list[Box]:
    return sorted(boxes, key=lambda b: (b[1], b[0]))


ROW_TOL_FRAC = 0.015  # row clustering tolerance, as a fraction of image height
MIN_STICKER_ROWS = 4  # fewer sticker rows than this falls back to concept + grounding


def cluster_rows(stickers: list[Box], image_h: int, tol_frac: float = ROW_TOL_FRAC) -> list[list[Box]]:
    """Group sticker boxes into one row per device by y-center."""
    tol = image_h * tol_frac
    rows: list[list[Box]] = []
    for box in sorted(stickers, key=lambda b: (b[1] + b[3]) / 2):
        center = (box[1] + box[3]) / 2
        if rows and abs(center - sum((b[1] + b[3]) / 2 for b in rows[-1]) / len(rows[-1])) <= tol:
            rows[-1].append(box)
        else:
            rows.append([box])
    return rows


def row_seed_boxes(rows: list[list[Box]], image_w: int, image_h: int) -> list[Box]:
    """One narrow seed per sticker row, centered on the row: wide enough to
    overlap the device face, thin enough that SAM cannot merge adjacent rows.
    Full-width strips merge rows; sticker-sized boxes come back sticker-sized."""
    seeds: list[Box] = []
    for row in rows:
        cx = sum((b[0] + b[2]) / 2 for b in row) / len(row)
        cy = sum((b[1] + b[3]) / 2 for b in row) / len(row)
        half_w = max((max(b[2] for b in row) - min(b[0] for b in row)) * 1.5, 120)
        half_h = max((max(b[3] for b in row) - min(b[1] for b in row)) * 0.75, 15)
        seeds.append((int(max(0, cx - half_w)), int(max(0, cy - half_h)),
                      int(min(image_w - 1, cx + half_w)),
                      int(min(image_h - 1, cy + half_h))))
    return seeds


def contains(container: Box, x: float, y: float) -> bool:
    return container[0] <= x <= container[2] and container[1] <= y <= container[3]


def keep_sticker_boxes(boxes: list[Box], stickers: list[Box]) -> list[Box]:
    """Drop device boxes whose interior holds no sticker center."""
    centers = [((s[0] + s[2]) / 2, (s[1] + s[3]) / 2) for s in stickers]
    return [b for b in boxes if any(contains(b, x, y) for x, y in centers)]


SAM_MAX_OBJECTS = 16  # box-prompt requests beyond this are refused live


def gap_seed_boxes(rows: list[list[Box]], image_w: int, image_h: int,
                   rail_top_px: float, rail_bottom_px: float, total_u: int,
                   max_seeds: int = SAM_MAX_OBJECTS) -> list[Box]:
    """Narrow seeds for inter-row gaps wider than 1.75U: a sticker the concept
    pass missed may still hide a device there. Gaps near 2U go first (most
    likely exactly one hidden device); empty gaps come back as unreadable
    boxes and are dropped after the label read."""
    unit = (rail_bottom_px - rail_top_px) / total_u if total_u else 0
    if unit <= 0 or max_seeds <= 0:
        return []
    cys = sorted(sum((b[1] + b[3]) / 2 for b in row) / len(row) for row in rows)
    cx, half_w = image_w / 2, min(300, image_w * 0.25)
    candidates: list[tuple[float, Box]] = []
    for upper_cy, lower_cy in zip(cys, cys[1:], strict=False):
        width_u = (lower_cy - upper_cy) / unit
        if width_u > 1.75:
            mid = (upper_cy + lower_cy) / 2
            if rail_top_px <= mid <= rail_bottom_px:
                half_h = min(0.45 * unit, 25)
                candidates.append((abs(width_u - 2.0),
                                   (int(max(0, cx - half_w)), int(max(0, mid - half_h)),
                                    int(min(image_w - 1, cx + half_w)),
                                    int(min(image_h - 1, mid + half_h)))))
    candidates.sort(key=lambda c: c[0])
    seeds = [box for _, box in candidates[:max_seeds]]
    return sorted(seeds, key=lambda b: (b[1], b[0]))


def attribute_to_seeds(boxes: list[Box], seed_cys: list[float],
                       seed_is_gap: list[bool]) -> tuple[list[Box], list[bool]]:
    """Attribute each box to its nearest seed row. Returns (boxes, is_gap)."""
    flags: list[bool] = []
    for box in boxes:
        cy = (box[1] + box[3]) / 2
        nearest = min(range(len(seed_cys)), key=lambda i: abs(seed_cys[i] - cy))
        flags.append(seed_is_gap[nearest])
    return list(boxes), flags


def split_multi_row_boxes(boxes: list[Box], rows: list[list[Box]]) -> list[Box]:
    """Cut boxes spanning several sticker rows at the midpoints between the
    contained row centroids. Single-row boxes pass through untouched."""
    out: list[Box] = []
    for box in boxes:
        inside = sorted(
            sum((b[1] + b[3]) / 2 for b in row) / len(row) for row in rows
            if any(contains(box, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in row)
        )
        if len(inside) < 2:
            out.append(box)
            continue
        bounds = [box[1]] + [(a + b) / 2 for a, b in zip(inside, inside[1:], strict=False)]
        bounds += [box[3]]
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            if hi > lo:
                out.append((box[0], int(lo), box[2], int(hi)))
    return out


def nms_with_flags(boxes: list[Box], flags: list[bool],
                   thresh: float = 0.5) -> tuple[list[Box], list[bool]]:
    """NMS keeping a parallel per-box flag list aligned."""
    order = sorted(range(len(boxes)),
                   key=lambda i: (-((boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1])), i))
    kept_idx: list[int] = []
    for i in order:
        if all(iou(boxes[i], boxes[j]) <= thresh for j in kept_idx):
            kept_idx.append(i)
    return [boxes[i] for i in kept_idx], [flags[i] for i in kept_idx]


def union_by_sticker(boxes: list[Box], stickers: list[Box]) -> list[Box]:
    """Merge split boxes that share exactly one sticker (two halves of one
    device face). Boxes spanning several stickers are left alone."""
    if len(boxes) < 2 or not stickers:
        return list(boxes)
    centers = [((s[0] + s[2]) / 2, (s[1] + s[3]) / 2) for s in stickers]
    containing = [[i for i, b in enumerate(boxes) if contains(b, x, y)] for x, y in centers]
    parent = list(range(len(boxes)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for holders in containing:
        exclusive = [i for i in holders if sum(i in other for other in containing) == 1]
        for i in exclusive[1:]:
            parent[find(i)] = find(exclusive[0])
    groups: dict[int, list[Box]] = {}
    for i, box in enumerate(boxes):
        groups.setdefault(find(i), []).append(box)
    merged: list[Box] = []
    for members in groups.values():
        if len(members) == 1:
            merged.append(members[0])
        else:
            merged.append((min(b[0] for b in members), min(b[1] for b in members),
                           max(b[2] for b in members), max(b[3] for b in members)))
    return merged


def resolve_overlaps(boxes: list[Box], max_frac: float = 0.15) -> list[Box]:
    """Clip stacked boxes whose y-overlap exceeds ``max_frac`` of the smaller
    height, splitting the overlap at its midpoint. Sorted top-to-bottom."""
    ordered = sort_top_to_bottom(boxes)
    for i in range(len(ordered) - 1):
        upper, lower = ordered[i], ordered[i + 1]
        overlap = min(upper[3], lower[3]) - max(upper[1], lower[1])
        smallest = min(upper[3] - upper[1], lower[3] - lower[1])
        if smallest > 0 and overlap > max_frac * smallest:
            mid = (max(upper[1], lower[1]) + min(upper[3], lower[3])) // 2
            mid = max(upper[1] + 1, min(lower[3] - 1, mid))
            ordered[i] = (upper[0], upper[1], upper[2], mid)
            ordered[i + 1] = (lower[0], mid, lower[2], lower[3])
    return ordered


def grid_to_pixels(bbox: dict[str, float], image_w: int, image_h: int) -> Box:
    """Spark 0-1000 grid box to clamped image pixels."""
    def clamp(v: float, lo: int, hi: int) -> int:
        return max(lo, min(hi, int(round(v))))

    x1 = clamp(bbox["x_min"] * image_w / 1000, 0, image_w - 1)
    y1 = clamp(bbox["y_min"] * image_h / 1000, 0, image_h - 1)
    x2 = clamp(bbox["x_max"] * image_w / 1000, 0, image_w - 1)
    y2 = clamp(bbox["y_max"] * image_h / 1000, 0, image_h - 1)
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return (x1, y1, max(x2, x1 + 1), max(y2, y1 + 1))
