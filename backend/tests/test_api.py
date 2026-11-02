"""The API end to end in replay mode: no key, a cassette, real tools and a real verifier."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from app import main
from app.config import Settings
from app.fake_meta_client import fake_text, fake_tool_call
from app.replay import CassetteWriter
from fastapi.testclient import TestClient
from test_agent import drafts, investigate, submission


def write_cassette(replay_dir: Path, name: str, entries: list[tuple[str, str, dict]]) -> None:
    writer = CassetteWriter(replay_dir, name)
    for model, purpose, response in entries:
        writer.append(
            {
                "model": model,
                "endpoint": "/v1/responses",
                "purpose": purpose,
                "request_hash": "not-compared-in-this-test",
                "request_summary": "",
                "response_json": response,
                "usage": response.get("usage"),
                "latency_ms": 0,
            }
        )


@pytest.fixture
def api(tmp_path, dataset, monkeypatch):
    replay_dir = tmp_path / "replay"
    spark = "muse-spark-1.3"
    bad = submission(fix="FIX-FACTORY-RESET")
    write_cassette(
        replay_dir,
        main.TRIAGE_CASSETTE,
        [
            (spark, "agent_plan", investigate()),
            (spark, "agent_plan", drafts()),
            (spark, "agent_plan", fake_tool_call("submit_incidents", bad)),
            (spark, "agent_replan", fake_tool_call("submit_incidents", submission(), call_id="call_9")),
        ],
    )
    write_cassette(
        replay_dir,
        main.VOICE_CASSETTE,
        [
            (
                "muse-voice-transcribe-1.0",
                "voice_note",
                {"transcript": "Jane Doe says the SFP on SW-ACC-02 is reseated", "audioDurationMs": 4000},
            ),
            (spark, "voice_work_log", fake_text("Reseated the SFP on SW-ACC-02.")),
        ],
    )
    settings = Settings(
        api_key="",
        mode="replay",
        generated_dir=dataset,
        replay_dir=replay_dir,
        db_path=tmp_path / "app.sqlite",
    )
    monkeypatch.setattr(main, "settings", lambda: settings)
    monkeypatch.setattr(main, "SAMPLE_VOICE", dataset / "sample.wav")
    (dataset / "sample.wav").write_bytes(b"RIFF")
    main._local.conn = None
    with TestClient(main.app) as client:
        yield client
    main._local.conn = None


def wait_for(api, run_id: str, seconds: float = 20) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        detail = api.get(f"/api/runs/{run_id}").json()
        if detail["run"]["status"] != "running":
            return detail
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def run_replay(api) -> str:
    run_id = api.post("/api/triage", json={"speed": 0}).json()["run_id"]
    wait_for(api, run_id)
    return run_id


def test_status_reports_replay_with_no_key(api):
    status = api.get("/api/status").json()
    assert status["mode"] == "replay"
    assert status["key"]["state"] == "none"
    assert status["alerts"] == 13 and status["devices"] == 5
    assert main.TRIAGE_CASSETTE in status["cassettes"]


def test_alerts_scrubbed_by_default_and_raw_on_request(api):
    scrubbed = api.get("/api/alerts").json()
    dump = json.dumps(scrubbed["alerts"])
    assert "jane.doe" not in dump and "555-0142" not in dump
    assert scrubbed["pii"]["tokens"] > 0
    raw = api.get("/api/alerts?view=raw").json()
    assert "jane.doe" in json.dumps(raw["alerts"])


def test_replay_run_fails_then_replans_then_passes(api):
    run_id = run_replay(api)
    detail = api.get(f"/api/runs/{run_id}").json()
    assert detail["run"]["status"] == "passed" and detail["run"]["attempts"] == 2
    assert [v["passed"] for v in detail["verifier"]] == [False, True]
    assert {i["status"] for i in detail["incidents"]} == {"verified"}
    calls = api.get(f"/api/calls?run_id={run_id}").json()
    assert calls and all(c["replayed"] for c in calls)
    assert api.get("/api/status").json()["budget"]["spent"] == 0


def test_sse_stream_ends_after_run_finished(api):
    run_id = run_replay(api)
    with api.stream("GET", f"/api/runs/{run_id}/events") as response:
        body = "".join(response.iter_text())
    assert "event: verifier" in body and "event: replan" in body and "event: call" in body
    assert body.rstrip().count("event: run_finished") == 1
    assert "event: error" not in body  # that name would close the browser's EventSource


def test_missing_cassette_is_a_clear_409(api, tmp_path, monkeypatch):
    response = api.post("/api/rack/scan", data={"replay": "true"})
    assert response.status_code == 409 and "recording" in response.json()["detail"]


def test_uploading_a_photo_needs_a_key(api):
    response = api.post("/api/rack/scan", files={"file": ("rack.jpg", b"fake", "image/jpeg")})
    assert response.status_code == 409


def test_human_gate_and_exports(api):
    run_id = run_replay(api)
    base = f"/api/incidents/{run_id}/INC-1"
    assert api.get(f"{base}/export/ticket.json").status_code == 409  # not before a person decides
    assert api.get(f"{base}/export/email.eml").status_code == 409
    detail = api.get(base).json()
    assert detail["status"] == "verified"
    assert {line["id"] for line in detail["logs"]} >= {"L-0001"}
    approved = api.post(f"{base}/approve").json()
    assert approved["status"] == "approved"
    assert api.get(f"{base}/export/ticket.json").json()["status"] == "approved"
    eml = api.get(f"{base}/export/email.eml")
    assert eml.status_code == 200 and "Subject: Network issue" in eml.text


def test_escalate_needs_a_reason(api):
    run_id = run_replay(api)
    base = f"/api/incidents/{run_id}/INC-2"
    assert api.post(f"{base}/escalate", json={"reason": "  "}).status_code == 422
    escalated = api.post(f"{base}/escalate", json={"reason": "Client VIP"}).json()
    assert escalated["status"] == "escalated" and escalated["escalation_reason"] == "Client VIP"
    assert api.get(f"{base}/export/ticket.json").status_code == 200
    assert (
        api.get(f"{base}/export/email.eml").status_code == 409
    )  # an escalated incident's email does not go out


def test_detokenize_only_on_export(api):
    run_id = run_replay(api)
    base = f"/api/incidents/{run_id}/INC-1"
    conn = main.conn()
    token = conn.execute("SELECT token, value FROM pii_map WHERE kind = 'EMAIL' LIMIT 1").fetchone()
    email = {
        "email_id": "EML-1",
        "incident_id": "INC-1",
        "subject": "s",
        "body": f"Contact {token['token']} today.",
        "tone": "calm",
    }
    api.patch(base, json={"client_email": email})
    assert token["value"] not in api.get(base).text
    api.post(f"{base}/approve")
    assert token["value"] in api.get(f"{base}/export/email.eml").text


def test_voice_note_lands_in_the_ticket_scrubbed(api):
    run_id = run_replay(api)
    base = f"/api/incidents/{run_id}/INC-1"
    response = api.post(f"{base}/voice", data={"sample": "true"})
    assert response.status_code == 200, response.text
    note = response.json()
    assert "Jane Doe" not in note["transcript"] and "<PERSON_" in note["transcript"]
    detail = api.get(base).json()
    assert detail["voice_notes"][0]["work_log_line"] == "Reseated the SFP on SW-ACC-02."
    assert "Reseated the SFP on SW-ACC-02." in detail["ticket"]["work_log"]


def test_device_edit_is_manual_and_clears_review(api):
    response = api.patch("/api/devices/dev_A_12", json={"serial": "ABC123"})
    assert response.status_code == 200
    body = response.json()
    assert body["serial"] == "ABC123" and body["source"] == "manual" and body["needs_review"] is False
    assert api.patch("/api/devices/nope", json={"serial": "x"}).status_code == 404


def test_files_route_serves_only_images_under_data(api):
    assert api.get("/files/generated/ground_truth.json").status_code == 404
    assert api.get("/files/runs/app.sqlite").status_code == 404
    assert api.get("/files/rack/..%2F..%2F.env").status_code == 404
    assert api.get("/files/..%2F.env").status_code == 404


@pytest.mark.parametrize(
    "path",
    ["/.env", "/../.env", "/..%2F.env", "/..%2F..%2F.env", "/..%2F..%2Fdata%2Fgenerated%2Fground_truth.json"],
)
def test_no_route_serves_secrets_or_ground_truth(api, path):
    response = api.get(path)
    assert "MODEL_API_KEY" not in response.text
    assert "root_cause_device_id" not in response.text


def test_metrics_come_from_the_run(api):
    run_id = run_replay(api)
    metrics = api.get(f"/api/metrics?run_id={run_id}").json()
    assert metrics["ready"] and metrics["incidents"] == 2 and metrics["attempts"] == 2
    assert metrics["grouping"]["f1"] == 1.0
