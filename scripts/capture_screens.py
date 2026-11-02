"""Save README screenshots with a headless browser. The app must be running on port 8000, with a finished run.

python scripts/capture_screens.py --run RUN_ID --incident INC-SW
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "google-chrome",
    "chromium",
]


def browser() -> str:
    for candidate in CANDIDATES:
        if Path(candidate).exists() or shutil.which(candidate):
            return candidate
    raise SystemExit("no Chromium-family browser found")


def shot(exe: str, url: str, out: Path, wait_ms: int = 9000) -> None:
    subprocess.run(
        [
            exe,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--window-size=1600,900",
            "--force-device-scale-factor=1",
            f"--virtual-time-budget={wait_ms}",
            f"--screenshot={out}",
            url,
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    print(f"{out.name}: {out.stat().st_size // 1024} KB")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--incident", default="INC-SW")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    out = ROOT / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    exe = browser()
    pages = {
        "alert-wall.png": "/#/alerts",
        "rack-cmdb.png": "/#/rack",
        "run-verified.png": f"/#/run/{args.run}",
        "incident.png": f"/#/incident/{args.run}/{args.incident}",
        "metrics.png": f"/#/metrics/{args.run}",
        "call-log.png": "/#/calls",
    }
    for name, path in pages.items():
        shot(exe, args.base + path, out / name)


if __name__ == "__main__":
    main()
