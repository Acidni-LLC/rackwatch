"""Checks on what the repository actually ships: cassettes, generated data, and the replay promise."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app import db as dbmod
from app.agent.loop import new_run_id, run_triage, start_run
from app.config import GENERATED_DIR, REPLAY_DIR, Settings
from app.meta_client import MetaClient
from app.replay import cassette_meta
from app.store import load_contacts, load_dataset

CASSETTES = ["triage_pass_after_replan", "triage_spark_open", "vision_sample_rack", "voice_note"]


def _text(name: str) -> str:
    return (REPLAY_DIR / name / "cassette.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", CASSETTES)
def test_cassettes_are_present_and_have_metadata(name):
    assert _text(name).strip()
    meta = cassette_meta(REPLAY_DIR, name)
    assert meta["kind"] and meta["recorded_at"]


@pytest.mark.parametrize("name", CASSETTES)
def test_cassettes_hold_no_contact_data_or_encrypted_reasoning(name):
    text = _text(name)
    assert '"encrypted_content"' not in text
    for contact in load_contacts(GENERATED_DIR):
        for value in (contact.name, contact.email, contact.phone):
            if value:
                assert value not in text, value


@pytest.mark.parametrize("name", ["triage_pass_after_replan", "triage_spark_open"])
def test_shipped_triage_cassettes_really_replan(name):
    """The demo's rejection has to be real: replay it, with real tools and a real verifier, and it must fail then pass."""
    settings = Settings(api_key="", mode="replay", generated_dir=GENERATED_DIR, replay_dir=REPLAY_DIR)
    conn = dbmod.connect(":memory:")
    load_dataset(conn, GENERATED_DIR)
    run_id = new_run_id()
    start_run(conn, run_id, "triage", "replay", name)
    client = MetaClient(settings=settings, conn=conn, run_id=run_id, cassette=name, replay_speed=0)
    meta = cassette_meta(REPLAY_DIR, name)
    result = run_triage(
        conn, client, settings, run_id=run_id, demo_strict=bool(meta.get("strict")), engine=meta.get("engine", "hosted")
    )

    assert result.status == "passed", result.error
    assert result.attempts == 2
    rows = conn.execute(
        "SELECT result FROM verifier_results WHERE run_id = ? ORDER BY attempt", (run_id,)
    ).fetchall()
    verdicts = [json.loads(r["result"]) for r in rows]
    assert [v["passed"] for v in verdicts] == [False, True]
    assert verdicts[-1]["grouping"]["f1"] == 1.0
    calls = conn.execute("SELECT diverged, replayed FROM calls WHERE run_id = ?", (run_id,)).fetchall()
    assert calls and all(c["replayed"] for c in calls)
    assert not any(c["diverged"] for c in calls), "the shipped data no longer matches the cassette"
    if meta.get("engine") == "open":
        models = {r["model"] for r in conn.execute("SELECT model FROM calls WHERE run_id = ?", (run_id,))}
        assert models == {settings.spark_open_model}


def test_generated_ground_truth_is_consistent():
    truth = json.loads((GENERATED_DIR / "ground_truth.json").read_text(encoding="utf-8"))
    alert_ids = {
        json.loads(line)["id"]
        for line in Path(GENERATED_DIR / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
    }
    grouped = [a for i in truth["incidents"] for a in i["alert_ids"]] + truth["noise_alert_ids"]
    assert len(grouped) == len(set(grouped)) and set(grouped) == alert_ids
