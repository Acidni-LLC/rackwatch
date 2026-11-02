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
