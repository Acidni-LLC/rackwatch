"""Run one rack scan from the command line and compare it with the seed, device by device.

python scripts/run_vision.py                 live, in a throwaway database
python scripts/run_vision.py --record NAME   live, and writes data/replay/NAME/
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db as dbmod  # noqa: E402
from app.agent.loop import finish_run, new_run_id, start_run  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.meta_client import MetaClient  # noqa: E402
from vision.pipeline import run_rack_pipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=str(ROOT / "data/rack/sample_rack.jpg"))
    parser.add_argument("--record")
    args = parser.parse_args()
    settings = get_settings()
    settings = replace(settings, mode="record" if args.record else "live")
    conn = dbmod.connect(settings.db_path)
    run_id = new_run_id()
    start_run(conn, run_id, "vision", settings.mode, args.record)
    client = MetaClient(settings=settings, conn=conn, run_id=run_id, cassette=args.record)
    seed_path = settings.generated_dir / "cmdb_seed.json"
    started = time.perf_counter()
    scan = run_rack_pipeline(args.image, client, "RACK-A", str(seed_path))
    seconds = round(time.perf_counter() - started, 1)
    summary = finish_run(conn, run_id, "passed", 1)
    seed = {d["hostname"]: d for d in json.loads(seed_path.read_text(encoding="utf-8"))}
    print(
        f"{seconds}s  ${summary['cost_usd']:.4f}  calls={len(scan.calls)}  total_u={scan.total_u}  accuracy={scan.accuracy}"
    )
    for d in scan.devices:
        s = seed.get(d.hostname or "", {})
        flags = []
        if s and (d.u_start, d.u_end) != (s["u_start"], s["u_end"]):
            flags.append(f"U should be {s['u_start']}-{s['u_end']}")
        if s and d.serial != s.get("serial"):
            flags.append(f"serial should be {s.get('serial')}")
        if s and d.model != s.get("model"):
            flags.append(f"model should be {s.get('model')}")
        print(
            f"  {d.id:9} U{d.u_start}-{d.u_end} {d.device_type:11} {str(d.make):9} {str(d.model):22} {str(d.hostname):11} "
            f"{str(d.serial):17} conf={d.confidence:.2f} review={d.needs_review} {'; '.join(flags)}"
        )
    if client.writer is not None:
        client.writer.write_meta(
            {
                "kind": "vision",
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "run_id": run_id,
                "seconds": seconds,
                "cost_usd": summary["cost_usd"],
                "accuracy": scan.accuracy,
            }
        )


if __name__ == "__main__":
    main()
