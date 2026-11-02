"""MetaClient guards, call log and cassette round trip. All offline, $0."""

from __future__ import annotations

import json

import pytest
from app import db as dbmod
from app.budget import BudgetExceeded
from app.config import Settings
from app.fake_meta_client import FakeMetaClient, fake_text
from app.meta_client import LiveTransport, MetaClient, RecordingTransport, ReplayTransport
from app.pii import UnscrubbedPayloadError
from app.replay import CassetteExhausted, CassettePlayer, CassetteWriter, ReplayUnavailable

FAKE_KEY = "fake-key-abcdefghijklmnopqrstuvwxyz0123"  # pragma: allowlist secret


def settings(tmp_path=None, **kw) -> Settings:
    base = dict(api_key="", mode="live")
    if tmp_path is not None:
        base["replay_dir"] = tmp_path
    return Settings(**(base | kw))


def test_refuses_unscrubbed_payload_and_logs_the_refusal():
    client = FakeMetaClient(script=[fake_text("never sent")])
    with pytest.raises(UnscrubbedPayloadError):
        client.responses(purpose="t", model="muse-spark-1.3", input="ping jane.doe@clientco.example")
    row = client.conn.execute("SELECT status, response_excerpt FROM calls").fetchone()
    assert row["status"] == "refused_pii"
    assert "jane.doe" not in row["response_excerpt"]
    assert client.requests == []


def test_refuses_unscrubbed_tool_output():
    client = FakeMetaClient(script=[fake_text("never sent")])
    items = [{"type": "function_call_output", "call_id": "c", "output": '{"phone": "555-0142"}'}]
    with pytest.raises(UnscrubbedPayloadError):
        client.responses(purpose="t", model="muse-spark-1.3", input=items)


def test_model_output_in_history_is_not_a_refusal():
    client = FakeMetaClient(script=[fake_text("ok")])
    items = [{"type": "function_call", "call_id": "c", "name": "x", "arguments": '{"email":"a@b.example"}'}]
    assert client.responses(purpose="t", model="muse-spark-1.3", input=items).output_text == "ok"


def test_budget_guard_refuses_over_run_cap():
    client = FakeMetaClient(
        script=[fake_text("a", usage={"input_tokens": 2_000_000, "output_tokens": 0}), fake_text("b")],
        settings=settings(budget_per_run=1.50),
    )
    client.responses(purpose="t", model="muse-spark-1.3", input="first")  # costs $2.50
    with pytest.raises(BudgetExceeded):
        client.responses(purpose="t", model="muse-spark-1.3", input="second")
    statuses = [r["status"] for r in client.conn.execute("SELECT status FROM calls ORDER BY rowid")]
    assert statuses == ["ok", "refused_budget"]


def test_budget_guard_refuses_over_total_cap():
    client = FakeMetaClient(
        script=[fake_text("a", usage={"input_tokens": 1_000_000, "output_tokens": 0}), fake_text("b")],
        settings=settings(budget_total=1.30, budget_per_run=99),
    )
    client.responses(purpose="t", model="muse-spark-1.3", input="first")
    with pytest.raises(BudgetExceeded):
        client.responses(purpose="t", model="muse-spark-1.3", input="second")


def test_cost_uses_cached_rate_and_sam_is_per_image():
    client = FakeMetaClient(
        script=[
            fake_text(
                "a",
                usage={
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 400},
                },
            ),
            fake_text(""),
        ]
    )
    client.responses(purpose="t", model="muse-spark-1.3", input="x")
    client.responses(purpose="s", model="sam-3.1", input="x")
    rows = client.conn.execute("SELECT est_cost_usd FROM calls ORDER BY rowid").fetchall()
    assert rows[0]["est_cost_usd"] == pytest.approx((600 * 1.25 + 400 * 0.15 + 100 * 4.25) / 1e6, abs=1e-6)
    assert rows[1]["est_cost_usd"] == pytest.approx(0.0025)


def test_effort_and_max_output_tokens_are_set():
    client = FakeMetaClient(script=[fake_text("a"), fake_text("")])
    client.responses(purpose="t", model="muse-spark-1.3", input="x", effort="low")
    client.responses(purpose="s", model="sam-3.1", input="x")
    assert client.requests[0]["reasoning"] == {"effort": "low"}
    assert client.requests[0]["max_output_tokens"] == 4000
    assert client.requests[1]["stream"] is True
    assert "max_output_tokens" not in client.requests[1]


def test_call_log_never_stores_the_key():
    client = FakeMetaClient(script=[fake_text(f"echo {FAKE_KEY}")], settings=settings(api_key=FAKE_KEY))
    client.responses(purpose="t", model="muse-spark-1.3", input="x")
    dump = json.dumps([dict(r) for r in client.conn.execute("SELECT * FROM calls")])
    events = json.dumps([dict(r) for r in client.conn.execute("SELECT * FROM events")])
    assert FAKE_KEY not in dump
    assert FAKE_KEY not in events


def test_settings_repr_hides_the_key():
    assert FAKE_KEY not in repr(Settings(api_key=FAKE_KEY))


def test_cassette_round_trip(tmp_path):
    writer = CassetteWriter(tmp_path, "c1")

    class CannedLive(LiveTransport):
        def __init__(self):
            pass

        def responses(self, request, purpose, req_hash):
            output = [{"type": "reasoning", "encrypted_content": "BLOB"}]
            return {
                "json": {
                    "id": "r1",
                    "output": output,
                    "output_text": "recorded",
                    "usage": {"input_tokens": 5},
                },
                "latency_ms": 7,
            }

    conn = dbmod.connect(":memory:")
    recording = MetaClient(
        settings=settings(tmp_path), conn=conn, run_id="r", transport=RecordingTransport(CannedLive(), writer)
    )
    first = recording.responses(purpose="agent", model="muse-spark-1.3", input="hello")
    assert first.output_text == "recorded"

    saved = (tmp_path / "c1" / "cassette.jsonl").read_text()
    assert "BLOB" not in saved  # encrypted reasoning never reaches the cassette

    player = CassettePlayer(tmp_path, "c1")
    replaying = MetaClient(
        settings=settings(tmp_path), conn=conn, run_id="r2", transport=ReplayTransport(player, speed=0)
    )
    second = replaying.responses(purpose="agent", model="muse-spark-1.3", input="hello")
    assert second.output_text == "recorded" and second.replayed and not second.diverged

    row = conn.execute("SELECT replayed, diverged, est_cost_usd FROM calls WHERE run_id = 'r2'").fetchone()
    assert row["replayed"] == 1 and row["diverged"] == 0

    with pytest.raises(CassetteExhausted):
        replaying.responses(purpose="agent", model="muse-spark-1.3", input="hello")


def test_replay_flags_divergence(tmp_path):
    writer = CassetteWriter(tmp_path, "c2")
    writer.append(
        {
            "model": "m",
            "endpoint": "e",
            "purpose": "p",
            "request_hash": "abc",
            "response_json": {"output_text": "x"},
            "latency_ms": 0,
        }
    )
    client = MetaClient(
        settings=settings(tmp_path),
        conn=dbmod.connect(":memory:"),
        transport=ReplayTransport(CassettePlayer(tmp_path, "c2"), speed=0),
    )
    response = client.responses(purpose="p", model="m", input="different")
    assert response.diverged and response.output_text == "x"


def test_replay_does_not_spend_budget(tmp_path):
    writer = CassetteWriter(tmp_path, "c3")
    writer.append(
        {
            "model": "m",
            "endpoint": "e",
            "purpose": "p",
            "request_hash": "abc",
            "response_json": {"output_text": "x", "usage": {"input_tokens": 9_000_000}},
            "latency_ms": 0,
        }
    )
    client = MetaClient(
        settings=settings(tmp_path, budget_total=0.01),
        conn=dbmod.connect(":memory:"),
        transport=ReplayTransport(CassettePlayer(tmp_path, "c3"), speed=0),
    )
    client.responses(purpose="p", model="m", input="x")
    assert client.budget.total_spent() == 0


def test_missing_cassette_is_a_clear_error(tmp_path):
    with pytest.raises(ReplayUnavailable):
        MetaClient(
            settings=settings(tmp_path, mode="replay"), conn=dbmod.connect(":memory:"), cassette="nope"
        )


def test_replay_mode_needs_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    CassetteWriter(tmp_path, "c4").append(
        {
            "model": "m",
            "endpoint": "e",
            "purpose": "p",
            "request_hash": "h",
            "response_json": {"output_text": "x"},
            "latency_ms": 0,
        }
    )
    client = MetaClient(
        settings=settings(tmp_path, mode="replay"),
        conn=dbmod.connect(":memory:"),
        cassette="c4",
        replay_speed=0,
    )
    assert client.responses(purpose="p", model="m", input="x").output_text == "x"
