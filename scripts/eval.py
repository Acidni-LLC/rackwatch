"""Triage eval (DESIGN.md §15): N live runs against the seeded dataset. Writes docs/eval.json for the README.

    python scripts/eval.py --n 5          normal runs
    python scripts/eval.py --n 3 --strict strict first attempt, which measures re-planning

Each run is a real Meta API call sequence. About $0.35 per run, within the $1.50 run cap.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import db as dbmod  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.store import load_dataset  # noqa: E402
from run_triage import run_once  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--engine", choices=["hosted", "open", "mixed"], default="hosted")
    parser.add_argument("--out", default=str(ROOT / "docs/eval.json"))
    args = parser.parse_args()

    kind = "strict" if args.strict else "normal"
    if args.engine != "hosted":
        kind = args.engine
    settings = replace(get_settings(), db_path=ROOT / "data/runs/eval.sqlite")
    conn = dbmod.connect(settings.db_path)
    load_dataset(conn, settings.generated_dir)
    runs = []
    for i in range(args.n):
        summary = run_once(conn, settings, None, None, args.strict, 0.0, args.engine)
        runs.append(summary)
        grouping = summary["grouping"] or {}
        print(
            f"run {i + 1}/{args.n}: {summary['status']} attempts={summary['attempts']} f1={grouping.get('f1')} "
            f"{summary['seconds']}s ${summary['cost_usd']}",
            flush=True,
        )
    passed = [r for r in runs if r["status"] == "passed"]
    result = {
        "kind": kind,
        "engine": args.engine,
        "date": time.strftime("%Y-%m-%d"),
        "dataset": "seed 42, 512 alerts",
        "n": len(runs),
        "passed": len(passed),
        "first_try": sum(1 for r in passed if r["attempts"] == 1),
        "mean_attempts": round(statistics.mean(r["attempts"] for r in runs), 2),
        "f1_mean": round(statistics.mean((r["grouping"] or {}).get("f1", 0) for r in runs), 4),
        "cost_mean_usd": round(statistics.mean(r["cost_usd"] or 0 for r in runs), 4),
        "cost_max_usd": round(max(r["cost_usd"] or 0 for r in runs), 4),
        "seconds_mean": round(statistics.mean(r["seconds"] for r in runs), 1),
        "seconds_max": max(r["seconds"] for r in runs),
        "runs": runs,
    }
    out = Path(args.out)
    existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    existing[result["kind"]] = result
    out.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "runs"}, indent=2))


if __name__ == "__main__":
    main()
