"""Dev-time asset step: Muse Image garbles small text, so repaint each asset sticker with its exact layout text.

SAM 3.1 finds the white stickers, each is matched to a device top to bottom, and the text is redrawn on the
sticker's own paper colour with a little blur so it sits in the photo.

    python scripts/repaint_stickers.py --image data/rack/sample_rack.jpg --out data/rack/sample_rack.jpg
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.meta_client import MetaClient  # noqa: E402

BOX = re.compile(r"\d+<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=\d+;h=\d+")


def fitted_font(text: str, box_w: int, box_h: int) -> ImageFont.FreeTypeFont:
    for name in ("consolab.ttf", "DejaVuSansMono-Bold.ttf"):
        try:
            size = box_h
            while size > 6:
                font = ImageFont.truetype(name, size)
                if font.getlength(text) <= box_w - 8 and size <= box_h - 5:
                    return font
                size -= 1
        except OSError:
            continue
    raise SystemExit("no monospace font found")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=str(ROOT / "data/rack/sample_rack.jpg"))
    parser.add_argument("--out", default=str(ROOT / "data/rack/sample_rack.jpg"))
    parser.add_argument("--layout", default=str(ROOT / "data/rack/layout.json"))
    args = parser.parse_args()

    layout = json.loads(Path(args.layout).read_text(encoding="utf-8"))
    devices = sorted(layout["devices"], key=lambda d: -d["u_start"])
    client = MetaClient(settings=get_settings(mode="live"), run_id="dev_assets")
    raw = Path(args.image).read_bytes()
    url = f"data:image/jpeg;base64,{base64.b64encode(raw).decode()}"
    result = client.responses(
        purpose="dev_asset_stickers",
        model="sam-3.1",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "white label"},
                    {"type": "input_image", "image_url": url},
                ],
            }
        ],
    )
    boxes = sorted(
        {tuple(int(v) for v in m.groups()) for m in BOX.finditer(result.output_text)}, key=lambda b: b[1]
    )
    # The first box is the rack name plate. Device stickers are the ones in the right half of the rack.
    stickers = [b for b in boxes[1:] if b[0] > 500 and (b[2] - b[0]) > 40]
    print(f"{len(boxes)} boxes, {len(stickers)} stickers, {len(devices)} devices")
    for b in stickers:
        print("  sticker", b)
    if len(stickers) != len(devices):
        raise SystemExit("sticker count does not match the layout; inspect the boxes above")

    img = Image.open(args.image).convert("RGB")
    draw = ImageDraw.Draw(img)
    for device, (x1, y1, x2, y2) in zip(devices, stickers, strict=True):
        text = (
            device["hostname"]
            if device["device_type"] == "patch_panel"
            else f"{device['hostname']} S/N {device['serial']}"
        )
        paper = np.median(np.asarray(img.crop((x1, y1, x2, y2))).reshape(-1, 3), axis=0).astype(int).tolist()
        paper = tuple(int(max(c, 205)) for c in paper)
        draw.rectangle((x1 + 1, y1 + 1, x2 - 1, y2 - 1), fill=paper)
        font = fitted_font(text, x2 - x1, y2 - y1)
        top = y1 + ((y2 - y1) - (font.getbbox(text)[3] - font.getbbox(text)[1])) // 2 - font.getbbox(text)[1]
        draw.text((x1 + 4, top), text, font=font, fill=(18, 18, 20))
        region = img.crop((x1, y1, x2, y2)).filter(ImageFilter.GaussianBlur(0.4))
        img.paste(region, (x1, y1))
    img.save(args.out, quality=92)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
