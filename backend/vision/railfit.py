"""Global rail fit: one robust line per scan instead of per-device U calls.

Rails are evenly spaced, so every (box bottom edge, u_label) pair measures
the same line ``y = a + b*U`` (y down-positive, hence ``b < 0``). Pairs use
the boundary convention: the bottom edge of a correctly read unit ``L`` sits
on boundary ``B(L-1)``, so the fit runs over ``X = u_label - 1``. Theil-Sen
(pairwise median slope) tolerates minority outlier reads; a second pass on
inliers tightens the line. With fewer than 4 pairs the frame rails serve as
fallback.
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple

import numpy as np

MIN_PAIRS = 4
INLIER_TOL_U = 0.4  # residual tolerance for inliers, in U
BAD_FIT_MAD_U = 0.5  # scaled MAD above this flags every device
COVER_FRAC = 0.5  # a unit counts only when the box covers more than half of it


class Fit(NamedTuple):
    a: float  # y intercept: boundary B(0)
    b: float  # px per unit step (negative: y grows downward)
    total_u: int
    n_pairs: int
    n_inliers: int
    mad_u: float
    source: str  # "labels" | "frame"
    large_residuals: bool


def theil_sen(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Median pairwise slope; None when no usable pair exists."""
    slopes = [(ys[j] - ys[i]) / (xs[j] - xs[i])
              for i in range(len(xs)) for j in range(i + 1, len(xs))
              if xs[j] != xs[i]]
    if not slopes:
        return None
    b = float(np.median(np.asarray(slopes, dtype=float)))
    a = float(np.median(np.asarray(
        [y - b * x for x, y in zip(xs, ys, strict=True)], dtype=float)))
    return a, b


def _residuals(a: float, b: float, pairs: list[tuple[float, float]]) -> list[float]:
    return [x - (y - a) / b for x, y in pairs]


def _mad(values: list[float]) -> float:
    med = float(np.median(np.asarray(values, dtype=float)))
    return 1.4826 * float(np.median(np.asarray(
        [abs(v - med) for v in values], dtype=float)))


def fit_rail(pairs: list[tuple[float, float]], total_u: int,
             frame: tuple[float, float] | None = None) -> Fit | None:
    """Robust boundary line from ``(u_label - 1, box bottom y)`` pairs.

    Slope comes from Theil-Sen on the pairs (relative geometry no single box
    biases); the intercept is arbitrated between the pairs' median intercept
    and the frame rails, picking whichever the reads agree with more. Small
    systematic label bias (top-read 2U stickers) loses to frame rails this
    way, while a shifted frame loses to consistent reads. Returns None with
    fewer than 4 pairs or a non-downward slope, so the caller falls back to
    the frame rails alone.
    """
    if len(pairs) < MIN_PAIRS:
        return None
    first = theil_sen([p[0] for p in pairs], [p[1] for p in pairs])
    if first is None or first[1] >= 0:
        return None
    _a_labels, b = first
    a_labels = float(np.median(np.asarray(
        [y - b * x for x, y in zip([p[0] for p in pairs], [p[1] for p in pairs],
                                   strict=True)], dtype=float)))
    candidates = [("labels", a_labels)]
    if frame is not None:
        candidates.append(("frame", frame[1]))
    scored = [(name, _mad([x - (y - a) / b for x, y in pairs])) for name, a in candidates]
    source, _ = min(scored, key=lambda s: s[1])
    a = dict(candidates)[source]
    inliers = [p for p in pairs if abs(p[0] - (p[1] - a) / b) <= INLIER_TOL_U]
    if len(inliers) >= MIN_PAIRS:
        second = theil_sen([p[0] for p in inliers], [p[1] for p in inliers])
        if second is not None and second[1] < 0:
            a, b = second
            inliers = [p for p in inliers
                       if abs(p[0] - (p[1] - a) / b) <= INLIER_TOL_U]
    mad = _mad([x - (y - a) / b for x, y in pairs])
    return Fit(a=a, b=b, total_u=total_u, n_pairs=len(pairs), n_inliers=len(inliers),
               mad_u=mad, source=source, large_residuals=mad > BAD_FIT_MAD_U)


def frame_fit(top: float, bottom: float, total_u: int) -> Fit:
    """Fallback line from the frame call's rail edges (boundary convention:
    top edge is the top of the highest unit, bottom edge the bottom of unit 1)."""
    a = bottom
    b = -(bottom - top) / total_u if total_u else -1.0
    return Fit(a=a, b=b, total_u=total_u, n_pairs=0, n_inliers=0,
               mad_u=0.0, source="frame", large_residuals=False)


def unit_at(fit: Fit, y: float) -> int:
    """Boundary index at pixel y, clamped to the rack."""
    if fit.b == 0:
        return 1
    return max(1, min(fit.total_u, math.ceil((y - fit.a) / fit.b)))


def boundary_y(fit: Fit, k: int) -> float:
    """Pixel y of boundary B(k): the line between unit k and unit k+1."""
    return fit.a + fit.b * k


def unit_span(fit: Fit, unit: int) -> tuple[float, float]:
    """Pixel (top, bottom) of one unit: B(unit) sits above B(unit-1)."""
    return boundary_y(fit, unit), boundary_y(fit, unit - 1)


def assign_units(fit: Fit, y_top: float, y_bottom: float) -> tuple[int, int]:
    """u_start/u_end from one global line: a unit counts only when the box
    covers more than half of it, so a loose edge that crosses a rail line by
    a sliver cannot add a whole U. A box covering nothing by half falls back
    to the unit holding its center."""
    if y_bottom < y_top:
        y_top, y_bottom = y_bottom, y_top
    unit_h = abs(fit.b)
    if unit_h <= 0:
        u = unit_at(fit, (y_top + y_bottom) / 2)
        return (u, u)
    covered = [u for u in range(1, fit.total_u + 1)
               if _overlap(y_top, y_bottom, *unit_span(fit, u)) > COVER_FRAC * unit_h]
    if covered:
        return (min(covered), max(covered))
    u = unit_at(fit, (y_top + y_bottom) / 2)
    return (u, u)


def _overlap(lo_a: float, hi_a: float, lo_b: float, hi_b: float) -> float:
    top = max(lo_a, lo_b)
    bottom = min(hi_a, hi_b)
    return max(0.0, bottom - top)


def snap_box(fit: Fit, u_start: int, u_end: int, left_px: float, right_px: float,
             image_w: int, image_h: int) -> tuple[int, int, int, int]:
    """Grid-snapped box for a decided span: y from the top of u_end to the
    bottom of u_start on the fitted line, x across the rack interior between
    the rails, everything clamped inside the image. SAM still finds the
    device; the box is only snapped to the grid afterwards."""
    y_top = boundary_y(fit, u_end)
    y_bottom = boundary_y(fit, u_start - 1)
    x1 = max(0, min(image_w - 1, int(round(min(left_px, right_px)))))
    x2 = max(x1 + 1, min(image_w, int(round(max(left_px, right_px)))))
    y1 = max(0, min(image_h - 1, int(round(min(y_top, y_bottom)))))
    y2 = max(y1 + 1, min(image_h, int(round(max(y_top, y_bottom)))))
    return (x1, y1, x2, y2)


def label_pairs(boxes: list[Any], reads: dict[str, dict]) -> list[tuple[float, float]]:
    """Fit input: ``(u_label - 1, box bottom y)`` per read with a label."""
    pairs: list[tuple[float, float]] = []
    for i, box in enumerate(boxes):
        label = reads.get(f"b{i}", {}).get("u_label")
        if isinstance(label, bool) or not isinstance(label, (int, float)):
            continue
        pairs.append((float(label) - 1.0, float(box[3])))
    return pairs
