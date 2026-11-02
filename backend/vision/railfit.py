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
EDGE_INSET_FRAC = 0.15  # box edges inset this fraction of a unit before mapping


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


def assign_units(fit: Fit, y_top: float, y_bottom: float) -> tuple[int, int]:
    """u_start/u_end from one global line: each box edge rounded inward by a
    fraction of a unit, so edges resting exactly on a boundary resolve inside."""
    s = EDGE_INSET_FRAC * abs(fit.b)
    lo = unit_at(fit, y_bottom - s)
    hi = unit_at(fit, y_top + s)
    return (lo, hi) if lo <= hi else (lo, lo)


def label_pairs(boxes: list[Any], reads: dict[str, dict]) -> list[tuple[float, float]]:
    """Fit input: ``(u_label - 1, box bottom y)`` per read with a label."""
    pairs: list[tuple[float, float]] = []
    for i, box in enumerate(boxes):
        label = reads.get(f"b{i}", {}).get("u_label")
        if isinstance(label, bool) or not isinstance(label, (int, float)):
            continue
        pairs.append((float(label) - 1.0, float(box[3])))
    return pairs
