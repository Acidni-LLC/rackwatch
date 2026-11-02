"""Record the three shipped cassettes from real live runs (DESIGN.md §13). Nothing here is scripted.

    python scripts/record_replay.py vision     data/replay/vision_sample_rack/
    python scripts/record_replay.py triage     data/replay/triage_pass_after_replan/  (keeps trying for a real re-plan)
    python scripts/record_replay.py voice      data/replay/voice_note/
    python scripts/record_replay.py open       data/replay/triage_spark_open/  (a normal run on Muse Spark Open)

Triage uses the strict first attempt, which usually fails the verifier on its merits. A take is kept only if the
first attempt really failed and a later one really passed. Otherwise it is discarded and the script tries again, up to
--tries, so the demo never pretends: the failure on screen happened.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import db as dbmod  # noqa: E402
from app import voice as voice_mod  # noqa: E402
from app.agent.loop import finish_run, new_run_id, start_run  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import OPEN_CASSETTE, TRIAGE_CASSETTE, VISION_CASSETTE, VOICE_CASSETTE  # noqa: E402
from app.meta_client import MetaClient  # noqa: E402
from app.rack import SAMPLE_IMAGE  # noqa: E402
from app.store import load_dataset  # noqa: E402
from run_triage import describe, run_once  # noqa: E402


def fresh(settings, name: str):
    """A throwaway database per take, so a recording starts from the same state a clean install has."""
    tmp = Path(tempfile.mkdtemp(prefix=f"rw_{name}_"))
    run_settings = replace(settings, db_path=tmp / "rec.sqlite")
    conn = dbmod.connect(run_settings.db_path)
    load_dataset(conn, run_settings.generated_dir)
    return run_settings, conn


def record_vision(settings, tries: int, min_devices: int) -> None:
    """Scans vary between runs (docs/eval.json has the range). Keeps the first take that meets the bar, and records
    how many takes that needed in meta.json, so the shipped replay is never mistaken for the average."""
    from vision.pipeline import run_rack_pipeline  # noqa: PLC0415

    for take in range(1, tries + 1):
        run_settings, conn = fresh(settings, "vision")
        run_settings = replace(run_settings, mode="record")
        run_id = new_run_id()
        start_run(conn, run_id, "vision", "record", VISION_CASSETTE)
        client = MetaClient(settings=run_settings, conn=conn, run_id=run_id, cassette=VISION_CASSETTE)
        started = time.perf_counter()
        overlay = str(Path(run_settings.db_path).parent / "overlay.png")
        scan = run_rack_pipeline(
            str(SAMPLE_IMAGE),
            client,
            "RACK-A",
            str(run_settings.generated_dir / "cmdb_seed.json"),
            overlay_path=overlay,
        )
        summary = finish_run(conn, run_id, "passed", 1)
        accuracy = scan.accuracy or {}
        print(f"take {take}: {accuracy}", flush=True)
        if accuracy.get("devices_matched", 0) >= min_devices:
            meta = {
                "kind": "vision",
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "takes": take,
                "seconds": round(time.perf_counter() - started, 1),
                "cost_usd": summary["cost_usd"],
                "accuracy": accuracy,
                "devices": len(scan.devices),
            }
            client.writer.write_meta(meta)
            print(json.dumps(meta, indent=2))
            return
    raise SystemExit(f"no take reached {min_devices} devices in {tries} tries")


def record_triage(settings, tries: int) -> None:
    for attempt in range(1, tries + 1):
        run_settings, conn = fresh(settings, "triage")
        summary = run_once(conn, run_settings, TRIAGE_CASSETTE, None, True, 0.0)
        results = [
            json.loads(r["result"])
            for r in conn.execute(
                "SELECT result FROM verifier_results WHERE run_id = ? ORDER BY attempt", (summary["run_id"],)
            )
        ]
        real_failure = len(results) >= 2 and not results[0]["passed"] and results[-1]["passed"]
        fixes = {
            r["proposed_fix_id"]
            for r in conn.execute(
                "SELECT proposed_fix_id FROM incidents WHERE run_id = ?", (summary["run_id"],)
            )
        }
        print(
            f"take {attempt}: status={summary['status']} attempts={summary['attempts']} fixes={sorted(fixes)}",
            flush=True,
        )
        describe(conn, summary["run_id"])
        if summary["status"] == "passed" and real_failure and "ESCALATE" not in fixes:
            print(
                "kept: the first attempt really failed, a later one passed, and every incident proposes an approved fix"
            )
            return
        print("discarded this take")
        shutil.rmtree(Path(run_settings.replay_dir) / TRIAGE_CASSETTE, ignore_errors=True)
    raise SystemExit(f"no take qualified in {tries} tries")


def record_open(settings) -> None:
    """A normal run on Muse Spark Open. It is whatever happened on this take, pass or fail, and meta.json says which."""
    if not settings.fireworks_api_key:
        raise SystemExit("recording on Muse Spark Open needs FIREWORKS_API_KEY")
    run_settings, conn = fresh(settings, "open")
    summary = run_once(conn, run_settings, OPEN_CASSETTE, None, False, 0.0, "open")
    describe(conn, summary["run_id"])


def record_voice(settings, wav: Path) -> None:
    triage_dir = Path(settings.replay_dir) / TRIAGE_CASSETTE
    if not (triage_dir / "cassette.jsonl").exists():
        raise SystemExit("record the triage cassette first: voice keywords come from its incidents")
    run_settings, conn = fresh(settings, "voice")
    replayed = run_once(conn, replace(run_settings, mode="replay"), None, TRIAGE_CASSETTE, False, 0.0)
    if replayed["status"] != "passed":
        raise SystemExit(f"the triage cassette did not replay cleanly: {replayed}")
    incident = conn.execute(
        "SELECT id FROM incidents WHERE run_id = ? AND root_cause_device_id = 'dev_A_12'",
        (replayed["run_id"],),
    ).fetchone()
    live = replace(run_settings, mode="record")
    call_run = new_run_id()
    start_run(conn, call_run, "voice", "record", VOICE_CASSETTE)
    client = MetaClient(settings=live, conn=conn, run_id=call_run, cassette=VOICE_CASSETTE)
    note = voice_mod.add_voice_note(conn, client, live, replayed["run_id"], incident["id"], wav.read_bytes())
    summary = finish_run(conn, call_run, "passed", 1)
    meta = {
        "kind": "voice",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "incident": incident["id"],
        "cost_usd": summary["cost_usd"],
        "transcript": note["transcript"],
        "work_log_line": note["work_log_line"],
    }
    client.writer.write_meta(meta)
    print(json.dumps(meta, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("what", choices=["vision", "triage", "voice", "open"])
    parser.add_argument("--tries", type=int, default=4)
    parser.add_argument("--min-devices", type=int, default=13)
    parser.add_argument("--wav", default=str(ROOT / "data/voice/sample_note.wav"))
    args = parser.parse_args()
    settings = get_settings()
    if not settings.api_key:
        raise SystemExit("recording needs MODEL_API_KEY")
    if args.what == "vision":
        record_vision(settings, args.tries, args.min_devices)
    elif args.what == "triage":
        record_triage(settings, args.tries)
    elif args.what == "open":
        record_open(settings)
    else:
        record_voice(settings, Path(args.wav))


if __name__ == "__main__":
    main()
