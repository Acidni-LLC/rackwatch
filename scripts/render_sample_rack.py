"""Render data/rack/sample_rack_render.jpg from data/rack/layout.json. Our own render, so no client imagery.

Deterministic: the same layout gives the same bytes, apart from JPEG encoder differences. It draws a
photographic-looking 42U rack (rails with printed U numbers, device faces, label stickers, lens falloff,
sensor noise, a small perspective skew) so SAM 3.1 and Muse Spark get something close to a phone photo.

    python scripts/render_sample_rack.py [--out data/rack/sample_rack_render.jpg]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
W, H = 1100, 2048
RACK_X1, RACK_X2 = 170, 930
RACK_Y1, RACK_Y2 = 70, 1980
RAIL = 58


def font(size: int, bold: bool = False, mono: bool = False):
    names = (
        ["consolab.ttf", "DejaVuSansMono-Bold.ttf"]
        if mono and bold
        else ["consola.ttf", "DejaVuSansMono.ttf"]
        if mono
        else ["arialbd.ttf", "DejaVuSans-Bold.ttf"]
        if bold
        else ["arial.ttf", "DejaVuSans.ttf"]
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def u_box(u_start: int, u_end: int, total_u: int) -> tuple[int, int, int, int]:
    unit = (RACK_Y2 - RACK_Y1) / total_u
    top = RACK_Y1 + (total_u - u_end) * unit
    bottom = RACK_Y1 + (total_u - u_start + 1) * unit
    return RACK_X1 + RAIL, int(top) + 1, RACK_X2 - RAIL, int(bottom) - 1


def bevel(d: ImageDraw.ImageDraw, box, fill, light, dark) -> None:
    x1, y1, x2, y2 = box
    d.rectangle(box, fill=fill)
    d.line([(x1, y1), (x2, y1)], fill=light, width=1)
    d.line([(x1, y2), (x2, y2)], fill=dark, width=2)


def screws(d: ImageDraw.ImageDraw, box) -> None:
    x1, y1, x2, y2 = box
    my = (y1 + y2) // 2
    for x in (x1 + 9, x2 - 9):
        d.ellipse((x - 4, my - 4, x + 4, my + 4), fill=(150, 150, 152), outline=(60, 60, 62))
        d.line((x - 3, my, x + 3, my), fill=(70, 70, 72), width=1)


def ports(d, x, y1, y2, columns, rows=2, pw=13, gap=3, lit=None, rng=None) -> int:
    height = y2 - y1
    ph = max(5, (height - (rows + 1) * 2) // rows)
    for c in range(columns):
        for r in range(rows):
            px = x + c * (pw + gap)
            py = y1 + 2 + r * (ph + 2)
            d.rectangle((px, py, px + pw, py + ph), fill=(16, 16, 18), outline=(92, 92, 95))
            if lit and rng.random() < lit:
                d.rectangle((px + 2, py + 1, px + 4, py + 2), fill=(70, 230, 110))
    return x + columns * (pw + gap)


def sticker(d: ImageDraw.ImageDraw, x: int, y: int, lines: list[str], size: int = 15) -> None:
    f = font(size, mono=True, bold=True)
    tw = max(int(d.textlength(t, font=f)) for t in lines)
    line_h = size + 2
    h = line_h * len(lines) + 4
    d.rectangle((x, y, x + tw + 10, y + h), fill=(238, 236, 226), outline=(120, 118, 110))
    for i, t in enumerate(lines):
        d.text((x + 5, y + 2 + i * line_h), t, font=f, fill=(12, 12, 14))


def vents(d, x1, y1, x2, y2) -> None:
    for y in range(y1, y2, 5):
        for x in range(x1, x2, 6):
            d.rectangle((x, y, x + 3, y + 2), fill=(14, 14, 16))


def label_lines(dev: dict) -> list[str]:
    if dev["device_type"] == "patch_panel":
        return [dev["hostname"]]
    return [f"{dev['hostname']}  S/N {dev['serial']}"]


def draw_switch(d, dev, box, rng) -> None:
    x1, y1, x2, y2 = box
    bevel(d, box, (32, 72, 118) if "9300" in dev["model"] else (40, 88, 132), (92, 136, 176), (16, 34, 58))
    screws(d, box)
    h = y2 - y1
    d.text((x1 + 20, y1 + 2), "CISCO", font=font(13, bold=True), fill=(226, 232, 240))
    d.text(
        (x1 + 20, y1 + h - 16), dev["model"].replace("Catalyst ", "C"), font=font(11), fill=(205, 215, 226)
    )
    ports(d, x1 + 110, y1 + 2, y2 - 2, 14, rows=2, pw=10, gap=2, lit=0.75, rng=rng)
    sticker(d, x1 + 288, y1 + 8, label_lines(dev), size=14)


def draw_panel(d, dev, box, rng) -> None:
    x1, y1, x2, y2 = box
    bevel(d, box, (26, 26, 28), (64, 64, 68), (8, 8, 9))
    screws(d, box)
    columns = 24
    x = x1 + 24
    for c in range(columns):
        px = x + c * 15
        d.rectangle((px, y1 + 10, px + 11, y2 - 10), fill=(10, 10, 12), outline=(84, 84, 88))
    d.text((x, y1 + 1), "PANDUIT", font=font(9, bold=True), fill=(190, 190, 195))
    sticker(d, x2 - 110, y1 + 8, label_lines(dev), size=14)


def draw_server(d, dev, box, rng) -> None:
    x1, y1, x2, y2 = box
    dell = dev["make"] == "Dell"
    bevel(d, box, (42, 44, 48) if dell else (58, 60, 64), (92, 94, 98), (14, 14, 16))
    screws(d, box)
    d.rectangle((x1 + 18, y1 + 6, x1 + 120, y2 - 6), fill=(28, 28, 32), outline=(80, 80, 86))
    d.text((x1 + 26, y1 + 12), "DELL EMC" if dell else "HPE", font=font(15, bold=True), fill=(210, 214, 222))
    d.text(
        (x1 + 26, y1 + 34),
        dev["model"].replace("PowerEdge ", "PE ")[:18],
        font=font(11),
        fill=(190, 194, 200),
    )
    bays = 8
    bw = 36
    bx = x1 + 132
    for i in range(bays):
        px = bx + i * (bw + 3)
        d.rectangle((px, y1 + 6, px + bw, y2 - 6), fill=(24, 24, 27), outline=(96, 96, 100))
        d.rectangle((px + 4, y1 + 10, px + bw - 4, y1 + 14), fill=(120, 122, 128))
        d.ellipse(
            (px + bw - 9, y2 - 15, px + bw - 4, y2 - 10),
            fill=(80, 220, 120) if rng.random() < 0.8 else (235, 170, 40),
        )
    sticker(d, x2 - 210, y1 + 16, label_lines(dev), size=12)


def draw_kvm(d, dev, box, rng) -> None:
    x1, y1, x2, y2 = box
    bevel(d, box, (56, 58, 62), (100, 100, 104), (20, 20, 22))
    screws(d, box)
    d.text((x1 + 22, y1 + 3), "ATEN", font=font(16, bold=True), fill=(236, 90, 60))
    d.text((x1 + 22, y2 - 16), "CS1716A", font=font(11), fill=(216, 216, 218))
    for i in range(16):
        d.rectangle(
            (x1 + 110 + i * 14, y1 + 14, x1 + 120 + i * 14, y1 + 24),
            fill=(28, 28, 30),
            outline=(120, 120, 124),
        )
    sticker(d, x1 + 345, y1 + 9, label_lines(dev), size=14)


def draw_firewall(d, dev, box, rng) -> None:
    x1, y1, x2, y2 = box
    bevel(d, box, (28, 28, 30), (70, 70, 72), (8, 8, 8))
    screws(d, box)
    d.rectangle((x1 + 20, y1 + 3, x1 + 150, y2 - 3), fill=(205, 40, 40))
    d.text((x1 + 26, y1 + 5), "FORTINET", font=font(13, bold=True), fill=(255, 255, 255))
    d.text((x1 + 26, y2 - 18), "FortiGate 60F", font=font(11), fill=(250, 235, 235))
    ports(d, x1 + 162, y1 + 3, y2 - 3, 10, rows=1, pw=11, gap=3, lit=0.8, rng=rng)
    sticker(d, x1 + 318, y1 + 9, label_lines(dev), size=13)


def draw_nas(d, dev, box, rng) -> None:
    x1, y1, x2, y2 = box
    bevel(d, box, (22, 22, 24), (70, 70, 72), (6, 6, 6))
    screws(d, box)
    d.text((x1 + 24, y1 + 10), "Synology", font=font(17, bold=True), fill=(226, 226, 230))
    d.text((x1 + 24, y1 + 36), "RS1221+", font=font(12), fill=(196, 196, 200))
    for i in range(8):
        px = x1 + 130 + i * 40
        d.rectangle((px, y1 + 6, px + 36, y2 - 6), fill=(36, 36, 40), outline=(100, 100, 104))
        d.ellipse((px + 6, y2 - 16, px + 11, y2 - 11), fill=(80, 220, 120) if i != 3 else (235, 170, 40))
    sticker(d, x2 - 205, y1 + 16, label_lines(dev), size=12)


def draw_ups(d, dev, box, rng) -> None:
    x1, y1, x2, y2 = box
    bevel(d, box, (30, 30, 32), (80, 80, 84), (10, 10, 12))
    screws(d, box)
    d.text((x1 + 24, y1 + 10), "APC", font=font(22, bold=True), fill=(226, 226, 230))
    d.text((x1 + 24, y1 + 40), "Smart-UPS", font=font(11), fill=(196, 196, 200))
    d.rectangle((x1 + 120, y1 + 10, x1 + 250, y2 - 10), fill=(120, 160, 110), outline=(70, 90, 70))
    d.text((x1 + 128, y1 + 18), "ONLINE 100%", font=font(11, mono=True), fill=(18, 40, 16))
    d.text((x1 + 128, y1 + 34), "1500VA 120V", font=font(11, mono=True), fill=(18, 40, 16))
    vents(d, x1 + 262, y1 + 8, x2 - 230, y2 - 8)
    sticker(d, x2 - 225, y1 + 16, label_lines(dev), size=12)


def draw_pdu(d, dev, box, rng) -> None:
    x1, y1, x2, y2 = box
    bevel(d, box, (38, 38, 40), (84, 84, 88), (14, 14, 16))
    screws(d, box)
    d.text((x1 + 22, y1 + 3), "APC", font=font(15, bold=True), fill=(226, 226, 230))
    d.text((x1 + 22, y2 - 15), "AP7920B", font=font(10), fill=(200, 200, 204))
    for i in range(8):
        px = x1 + 100 + i * 24
        d.rectangle((px, y1 + 10, px + 16, y2 - 10), fill=(14, 14, 16), outline=(120, 120, 124))
    sticker(d, x2 - 245, y1 + 9, label_lines(dev), size=13)


DRAWERS = {
    "switch": draw_switch,
    "patch_panel": draw_panel,
    "server": draw_server,
    "kvm": draw_kvm,
    "firewall": draw_firewall,
    "storage": draw_nas,
    "ups": draw_ups,
    "pdu": draw_pdu,
}


def render(layout: dict) -> np.ndarray:
    rng = random.Random(42)
    total_u = layout["total_u"]
    img = Image.new("RGB", (W, H), (58, 60, 64))
    d = ImageDraw.Draw(img)

    # Background: a dim comms-closet wall with floor falloff.
    for y in range(H):
        shade = 66 - int(26 * (y / H))
        d.line([(0, y), (W, y)], fill=(shade, shade + 1, shade + 4))

    # Rack frame and interior.
    d.rectangle((RACK_X1 - 30, RACK_Y1 - 46, RACK_X2 + 30, RACK_Y2 + 46), fill=(18, 18, 20))
    d.rectangle((RACK_X1, RACK_Y1, RACK_X2, RACK_Y2), fill=(8, 8, 9))
    unit = (RACK_Y2 - RACK_Y1) / total_u

    # Rails with printed U numbers, the way a real rack marks them.
    for rail_x in (RACK_X1, RACK_X2 - RAIL):
        d.rectangle((rail_x, RACK_Y1, rail_x + RAIL, RACK_Y2), fill=(34, 34, 37))
        for u in range(1, total_u + 1):
            y = RACK_Y1 + (total_u - u) * unit
            d.line([(rail_x, y), (rail_x + RAIL, y)], fill=(60, 60, 63), width=1)
            d.text((rail_x + 8, y + unit / 2 - 8), f"{u}", font=font(14, bold=True), fill=(228, 228, 216))
            for hy in (y + unit * 0.2, y + unit * 0.8):
                d.rectangle((rail_x + RAIL - 15, hy - 2, rail_x + RAIL - 9, hy + 2), fill=(4, 4, 5))

    # Blanking panels and bare space between devices, mostly bare with a few blanks.
    used = {u for dev in layout["devices"] for u in range(dev["u_start"], dev["u_end"] + 1)}
    for u in range(1, total_u + 1):
        if u in used or rng.random() > 0.28:
            continue
        x1, y1, x2, y2 = u_box(u, u, total_u)
        d.rectangle((x1, y1, x2, y2), fill=(17, 17, 19), outline=(30, 30, 32))

    for dev in layout["devices"]:
        box = u_box(dev["u_start"], dev["u_end"], total_u)
        DRAWERS[dev["device_type"]](d, dev, box, rng)

    # Rack name plate on the top rail.
    plate = layout.get("rack_label") or layout["rack_id"]
    d.rectangle(
        (RACK_X1 + 240, RACK_Y1 - 38, RACK_X1 + 520, RACK_Y1 - 6),
        fill=(236, 234, 224),
        outline=(110, 110, 104),
    )
    d.text(
        (RACK_X1 + 252, RACK_Y1 - 35), f"{plate}  ACME DENTAL", font=font(19, bold=True), fill=(10, 10, 12)
    )

    img = img.filter(ImageFilter.GaussianBlur(0.45))
    arr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR).astype(np.float32)

    # Lens falloff, a warm flash hot spot and sensor noise.
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.sqrt(((xx - W / 2) / (W * 0.75)) ** 2 + ((yy - H * 0.45) / (H * 0.72)) ** 2)
    arr *= np.clip(1.14 - 0.42 * r**2, 0.4, 1.16)[..., None]
    arr += np.random.default_rng(42).normal(0, 3.2, arr.shape)
    arr = np.clip(arr, 0, 255).astype(np.uint8)

    # A small perspective skew: nobody holds a phone perfectly square to a rack.
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    dst = np.float32([[10, 6], [W - 4, 0], [W - 14, H - 4], [2, H - 10]])
    return cv2.warpPerspective(
        arr, cv2.getPerspectiveTransform(src, dst), (W, H), borderMode=cv2.BORDER_REPLICATE
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout", default=str(ROOT / "data/rack/layout.json"))
    parser.add_argument("--out", default=str(ROOT / "data/rack/sample_rack_render.jpg"))
    args = parser.parse_args()
    layout = json.loads(Path(args.layout).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), render(layout), [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {W}x{H})")


if __name__ == "__main__":
    main()
