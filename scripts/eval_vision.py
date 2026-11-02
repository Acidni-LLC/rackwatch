"""Vision eval: N live scans of the sample rack against the seed. Adds a `vision` entry to docs/eval.json.

    python scripts/eval_vision.py --n 5

About $0.10 per scan. Results vary between runs, so report the range and not one run.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db as dbmod  # noqa: E402
from app.agent.loop import finish_run, new_run_id, start_run  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.meta_client import MetaClient  # noqa: E402
from app.rack import SAMPLE_IMAGE  # noqa: E402
from vision.pipeline import run_rack_pipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--out", default=str(ROOT / "docs/eval.json"))
    args = parser.parse_args()
    settings = replace(get_settings(mode="live"), db_path=ROOT / "data/runs/eval.sqlite")
    conn = dbmod.connect(settings.db_path)
    seed = str(settings.generated_dir / "cmdb_seed.json")
    runs = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(args.n):
            run_id = new_run_id()
            start_run(conn, run_id, "vision", "live")
            client = MetaClient(settings=settings, conn=conn, run_id=run_id)
            started = time.perf_counter()
            scan = run_rack_pipeline(
                str(SAMPLE_IMAGE), client, "RACK-A", seed, overlay_path=str(Path(tmp) / f"{i}.png")
            )
            seconds = round(time.perf_counter() - started, 1)
            summary = finish_run(conn, run_id, "passed", 1)
            accuracy = scan.accuracy or {}
            runs.append(
                {
                    "devices": accuracy.get("devices_matched"),
                    "serials": accuracy.get("serial_matched"),
                    "u": accuracy.get("u_matched"),
                    "boxed": len(scan.devices),
                    "needs_review": sum(d.needs_review for d in scan.devices),
                    "seconds": seconds,
                    "cost_usd": round(summary["cost_usd"], 4),
                }
            )
            print(f"scan {i + 1}/{args.n}: {runs[-1]}", flush=True)
    span = lambda key: [min(r[key] for r in runs), max(r[key] for r in runs)]  # noqa: E731
    result = {
        "kind": "vision",
        "date": time.strftime("%Y-%m-%d"),
        "n": len(runs),
        "of": 14,
        "devices_range": span("devices"),
        "serials_range": span("serials"),
        "u_range": span("u"),
        "boxed_range": span("boxed"),
        "seconds_mean": round(statistics.mean(r["seconds"] for r in runs), 1),
        "cost_mean_usd": round(statistics.mean(r["cost_usd"] for r in runs), 4),
        "runs": runs,
    }
    out = Path(args.out)
    existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    existing["vision"] = result
    out.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "runs"}, indent=2))


if __name__ == "__main__":
    main()
