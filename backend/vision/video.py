"""Video mode (DESIGN.md §8.2c): sample up to 8 frames, keep the sharpest
by Laplacian variance, and run the photo pipeline on it."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def laplacian_sharpness(frame_bgr: np.ndarray) -> float:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def sample_video_frames(video_path: str, max_frames: int = 8) -> list[np.ndarray]:
    """Up to ``max_frames`` evenly spaced frames (BGR arrays)."""
    cap = cv2.VideoCapture(video_path)
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            frames: list[np.ndarray] = []
            while len(frames) < max_frames:
                ok, frame = cap.read()
                if not ok:
                    break
                frames.append(frame)
            return frames
        if total <= max_frames:
            idxs = list(range(total))
        else:
            idxs = sorted({round(i * (total - 1) / (max_frames - 1)) for i in range(max_frames)})
        frames_by_idx: dict[int, np.ndarray] = {}
        for n in range(total):
            ok, frame = cap.read()
            if not ok:
                break
            if n in set(idxs):
                frames_by_idx[n] = frame
        return [frames_by_idx[i] for i in idxs if i in frames_by_idx]
    finally:
        cap.release()


def select_sharpest_frame(frames: list[np.ndarray]) -> int:
    """Index of the sharpest frame. Empty input raises ValueError."""
    if not frames:
        raise ValueError("no frames to choose from")
    return max(range(len(frames)), key=lambda i: laplacian_sharpness(frames[i]))


def sharpest_frame_path(video_path: str, out_path: str, max_frames: int = 8) -> str:
    """Write the sharpest sampled frame to ``out_path`` (JPEG). Returns ``out_path``."""
    frames = sample_video_frames(video_path, max_frames)
    best = frames[select_sharpest_frame(frames)]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(out_path, best)
    return out_path
