"""Overlay rendering: SVG-style boxes drawn on the rack photo (DESIGN.md §12)."""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

COLORS = {
    "switch": "cyan",
    "server": "lime",
    "firewall": "red",
    "router": "orange",
    "ups": "yellow",
    "pdu": "yellow",
    "patch_panel": "magenta",
    "storage": "lime",
    "kvm": "white",
    "blank": "gray",
    "other": "white",
}


def render_overlay(image_path: str, devices: list[Any], out_path: str) -> str:
    """Draw one box per device labeled ``U12 SW-ACC-02``. Returns ``out_path``."""
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    for dev in devices:
        bbox = dev.bbox if hasattr(dev, "bbox") else dev.get("bbox")
        if bbox is None:
            continue
        if isinstance(bbox, dict):
            x1, y1, x2, y2 = bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"]
        else:
            x1, y1, x2, y2 = bbox.x1, bbox.y1, bbox.x2, bbox.y2
        dtype = dev.device_type if hasattr(dev, "device_type") else dev.get("device_type", "other")
        color = COLORS.get(dtype, "white")
        draw.rectangle([x1, y1, x2, y2], outline=color, width=3)
        if hasattr(dev, "u_start"):
            u_start, hostname = dev.u_start, dev.hostname or dtype
        else:
            u_start, hostname = dev.get("u_start"), dev.get("hostname") or dtype
        label = f"U{u_start} {hostname}"
        tw, th = draw.textlength(label), 12
        draw.rectangle([x1, max(0, y1 - th - 4), x1 + tw + 6, y1], fill="black")
        draw.text((x1 + 3, max(0, y1 - th - 2)), label, fill=color)
    image.save(out_path, "PNG")
    return out_path
