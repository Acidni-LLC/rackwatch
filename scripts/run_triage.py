"""Run one triage from the command line and print what happened. Uses the same harness as the app.

python scripts/run_triage.py                     live, in the app's database
python scripts/run_triage.py --record NAME       live, and writes data/replay/NAME/
python scripts/run_triage.py --strict            first attempt at low effort with a small turn cap
python scripts/run_triage.py --replay NAME       serve a cassette (no key needed)
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
from app.agent.loop import new_run_id, run_triage, start_run  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.meta_client import MetaClient  # noqa: E402
from app.metrics import run_metrics  # noqa: E402
from app.replay import cassette_meta  # noqa: E402
from app.store import load_dataset  # noqa: E402


def run_once(
    conn, settings, record: str | None, replay: str | None, strict: bool, speed: float, engine: str = "hosted"
) -> dict:
    run_settings = replace(settings, mode="record" if record else "replay" if replay else "live")
    if replay:
        meta = cassette_meta(settings.replay_dir, replay)
        strict = bool(meta.get("strict"))
        engine = meta.get("engine", "hosted")
    run_id = new_run_id()
    cassette = record or replay
    start_run(conn, run_id, "triage", run_settings.mode, cassette)
    client = MetaClient(
        settings=run_settings, conn=conn, run_id=run_id, cassette=cassette, replay_speed=speed
    )
    started = time.perf_counter()
    result = run_triage(conn, client, run_settings, run_id=run_id, demo_strict=strict, engine=engine)
    seconds = round(time.perf_counter() - started, 1)
    metrics = run_metrics(conn, run_id)
    summary = {
        "engine": engine,
        "run_id": run_id,
        "status": result.status,
        "attempts": result.attempts,
        "seconds": seconds,
        "incidents": result.incident_ids,
        "cost_usd": metrics.get("cost_usd"),
        "tokens_in": metrics.get("tokens_in"),
        "tokens_cached": metrics.get("tokens_cached"),
        "tokens_out": metrics.get("tokens_out"),
        "grouping": metrics.get("grouping"),
        "error": result.error,
    }
    if client.writer is not None:
        client.writer.write_meta(
            {
                "kind": "triage",
                "strict": strict,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                **summary,
            }
        )
    return summary


def describe(conn, run_id: str) -> None:
    for row in conn.execute(
        "SELECT attempt, result FROM verifier_results WHERE run_id = ? ORDER BY attempt", (run_id,)
    ):
        result = json.loads(row["result"])
        marks = " ".join(f"{c['rule'].split('_')[0]}{'+' if c['passed'] else '-'}" for c in result["checks"])
        print(f"  attempt {row['attempt']}: passed={result['passed']}  {marks}")
        print(f"    grouping {result['grouping']}")
        for check in [c for c in result["checks"] if not c["passed"]][:12]:
            print(f"    FAIL {check['rule']}: {check['detail'][:240]}")
    for row in conn.execute(
        "SELECT id, title, root_cause_device_id, proposed_fix_id, alert_ids, status FROM incidents WHERE run_id = ?",
        (run_id,),
    ):
        print(
            f"  {row['id']} [{row['status']}] {row['title'][:80]} | root={row['root_cause_device_id']} fix={row['proposed_fix_id']} alerts={len(json.loads(row['alert_ids']))}"
        )
    rows = conn.execute(
        "SELECT name, COUNT(*) AS n FROM tool_calls WHERE run_id = ? GROUP BY name ORDER BY n DESC", (run_id,)
    ).fetchall()
    print("  tools:", ", ".join(f"{r['name']}={r['n']}" for r in rows))
    rows = conn.execute(
        "SELECT purpose, effort, COUNT(*) AS n, SUM(tokens_in) AS ti, SUM(tokens_cached) AS tc, SUM(tokens_out) AS tok, SUM(latency_ms) AS ms, ROUND(SUM(est_cost_usd),4) AS c "
        "FROM calls WHERE run_id = ? GROUP BY purpose, effort",
        (run_id,),
    ).fetchall()
    for r in rows:
        print(
            f"  calls {r['purpose']}/{r['effort']}: n={r['n']} in={r['ti']} cached={r['tc']} out={r['tok']} ms={r['ms']} ${r['c']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record")
    parser.add_argument("--replay")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--speed", type=float, default=0.0)
    parser.add_argument("--engine", choices=["hosted", "open", "mixed"], default="hosted")
    parser.add_argument("--reload", action="store_true", help="reload the dataset first")
    args = parser.parse_args()
    settings = get_settings()
    conn = dbmod.connect(settings.db_path)
    if args.reload or not conn.execute("SELECT 1 FROM alerts LIMIT 1").fetchone():
        print("dataset:", load_dataset(conn, settings.generated_dir))
    summary = run_once(conn, settings, args.record, args.replay, args.strict, args.speed, args.engine)
    print(json.dumps(summary, indent=2))
    describe(conn, summary["run_id"])


if __name__ == "__main__":
    main()
