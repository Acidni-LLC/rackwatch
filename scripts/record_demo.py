"""Record a silent walkthrough of the replay flow, for the README GIF and as raw footage for the demo video.

Dev only, and not part of the app. Needs Playwright and a Chromium-family browser:
    pip install playwright

Start the app in replay mode first (python scripts/rw.py replay), then:
    python scripts/record_demo.py --out demo-out
Writes demo-out/walkthrough.webm and demo-out/walkthrough.mp4, and docs/demo.gif from the triage part.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg  # noqa: PLC0415

    return imageio_ffmpeg.get_ffmpeg_exe()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--out", default="demo-out")
    parser.add_argument("--channel", default="msedge", help="installed browser: msedge or chrome")
    parser.add_argument("--speed", default="4", help="vision replay speed")
    parser.add_argument("--replay", default="Replay recorded run", help="which triage replay button to click")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel, headless=True)
        context = browser.new_context(
            viewport={"width": 1600, "height": 900},
            record_video_dir=str(out),
            record_video_size={"width": 1600, "height": 900},
        )
        page = context.new_page()
        marks: dict[str, float] = {}
        start = time.monotonic()
        mark = lambda name: marks.__setitem__(name, time.monotonic() - start)  # noqa: E731

        page.request.post(f"{args.base}/api/reset")  # start from an unscanned rack

        # The rack: scan, then hold on the filled CMDB.
        mark("rack")
        page.goto(f"{args.base}/#/rack")
        page.wait_for_selector("text=Replay scan")
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="Replay scan").click()
        page.wait_for_selector("text=Accuracy vs seed", timeout=240_000)
        page.wait_for_timeout(4000)

        # The wall.
        mark("alerts")
        page.goto(f"{args.base}/#/alerts")
        page.wait_for_selector("text=Open alerts at Acme Dental Group")
        page.wait_for_timeout(3500)

        # Triage: rejection, re-plan, verified. Replay speed is the Alerts page default of 4x.
        mark("triage")
        page.get_by_role("button", name=args.replay).click()
        page.wait_for_url("**/#/run/**")
        page.wait_for_selector("text=Waiting for your approval", timeout=120_000)
        page.wait_for_timeout(3500)
        mark("triage_end")

        # The incident: a citation, the voice note, approval, the exports.
        page.get_by_role("button", name="Review 3 incidents").click()
        page.wait_for_selector("text=Root cause and evidence")
        page.wait_for_timeout(2500)
        page.locator("button.tag").first.click()
        page.wait_for_timeout(3000)
        page.get_by_role("button", name="Close").click()
        page.wait_for_timeout(800)
        page.get_by_role("button", name="Play recorded note").click()
        page.wait_for_selector("text=Work log", timeout=30_000)
        page.wait_for_timeout(3000)
        page.get_by_role("button", name="Approve").click()
        page.wait_for_selector("text=Client email .eml")
        page.wait_for_timeout(3000)

        # The numbers.
        page.get_by_role("link", name="Metrics").click()
        page.wait_for_selector("text=Grouping F1")
        page.wait_for_timeout(4000)
        mark("end")

        video_path = page.video.path()
        context.close()
        browser.close()

    source = Path(video_path)
    webm = out / "walkthrough.webm"
    source.replace(webm)
    ff = ffmpeg()
    subprocess.run(
        [
            ff,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(webm),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "20",
            str(out / "walkthrough.mp4"),
        ],
        check=True,
    )
    begin = max(marks["triage"] - 1.0, 0)
    length = marks["triage_end"] - marks["triage"] + 1.0
    gif = ROOT / "docs" / "demo.gif"
    palette = "fps=9,scale=1000:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"
    subprocess.run(
        [
            ff,
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{begin:.1f}",
            "-t",
            f"{length:.1f}",
            "-i",
            str(webm),
            "-vf",
            palette,
            str(gif),
        ],
        check=True,
    )
    print({k: round(v, 1) for k, v in marks.items()})
    print(
        f"{webm} {webm.stat().st_size // 1024} KB; {out / 'walkthrough.mp4'} {(out / 'walkthrough.mp4').stat().st_size // 1024} KB; {gif} {gif.stat().st_size // 1024} KB"
    )


if __name__ == "__main__":
    main()
