"""Muse Spark Open through an inference partner: routing, the same guards as the Meta API, and mixed runs. All offline, $0."""

from __future__ import annotations

import json

import pytest
from app import db as dbmod
from app.agent.loop import portable_input, run_triage
from app.agent.router import model_for
from app.budget import BudgetExceeded
from app.config import Settings, get_settings
from app.fake_meta_client import FakeMetaClient, fake_text, fake_tool_call
from app.meta_client import MetaClient
from app.pii import UnscrubbedPayloadError
from test_agent import client_for, drafts, investigate, submission

OPEN = "meta-models/muse-spark-open"
FAKE_KEY = "fake-partner-abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret


class Recorder:
    """Stands in for one provider."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def responses(self, request, purpose, req_hash):
        self.requests.append(request)
        return {"json": fake_text("ok"), "latency_ms": 1}


def routed_client(partner: Recorder | None) -> tuple[MetaClient, Recorder]:
    meta = Recorder()
    client = MetaClient(
        settings=Settings(api_key="k", mode="live"), conn=dbmod.connect(":memory:"), transport=meta
    )
    client.routes_open = True
    client.open_transport = partner
    return client, meta


def test_open_model_goes_to_the_partner_and_the_rest_to_meta():
    partner = Recorder()
    client, meta = routed_client(partner)
    client.responses(purpose="t", model=OPEN, input="hello")
    client.responses(purpose="t", model="muse-spark-1.3", input="hello")
    assert [r["model"] for r in partner.requests] == [OPEN]
    assert [r["model"] for r in meta.requests] == ["muse-spark-1.3"]


def test_open_model_without_a_key_is_a_clear_error():
    client, _ = routed_client(None)
    with pytest.raises(RuntimeError, match="BASETEN_API_KEY"):
        client.responses(purpose="t", model=OPEN, input="hello")
    row = client.conn.execute("SELECT status FROM calls").fetchone()
    assert row["status"] == "error"


def test_partner_transport_drops_include():
    pytest.importorskip("openai")
    from app.meta_client import OpenWeightsTransport

    captured = {}

    class Sdk:
        class responses:  # noqa: N801
            @staticmethod
            def create(**kw):
                captured.update(kw)

                class Result:
                    def model_dump(self, **_):
                        return {"output_text": "ok"}

                return Result()

    transport = OpenWeightsTransport(Settings(open_api_key=FAKE_KEY))
    transport.sdk = Sdk
    transport.responses({"model": OPEN, "input": "x", "include": ["reasoning.encrypted_content"]}, "t", "h")
    assert "include" not in captured


def test_the_pii_guard_covers_the_open_model():
    partner = Recorder()
    client, _ = routed_client(partner)
    with pytest.raises(UnscrubbedPayloadError):
        client.responses(purpose="t", model=OPEN, input="mail jane.doe@clientco.example")
    assert partner.requests == []


def test_the_budget_guard_covers_the_open_model():
    usage = {"input_tokens": 2_000_000, "output_tokens": 0}
    client = FakeMetaClient(
        script=[fake_text("a", usage=usage), fake_text("b")], settings=Settings(budget_per_run=1.5)
    )
    client.responses(purpose="t", model=OPEN, input="first")
    with pytest.raises(BudgetExceeded):
        client.responses(purpose="t", model=OPEN, input="second")


def test_the_call_log_names_the_open_model_and_endpoint():
    client = FakeMetaClient(script=[fake_text("a")])
    client.responses(purpose="t", model=OPEN, input="x")
    row = client.conn.execute("SELECT model, endpoint FROM calls").fetchone()
    assert row["model"] == OPEN and row["endpoint"].startswith("baseten:")


def test_partner_key_never_reaches_the_log():
    client = FakeMetaClient(script=[fake_text(f"echo {FAKE_KEY}")], settings=Settings(open_api_key=FAKE_KEY))
    client.responses(purpose="t", model=OPEN, input="x")
    assert FAKE_KEY not in json.dumps([dict(r) for r in client.conn.execute("SELECT * FROM calls")])


def test_settings_repr_hides_the_partner_key():
    assert FAKE_KEY not in repr(Settings(open_api_key=FAKE_KEY))


@pytest.mark.parametrize(
    ("provider", "key_name", "host", "model"),
    [
        ("baseten", "BASETEN_API_KEY", "inference.baseten.co", OPEN),
        ("fireworks", "FIREWORKS_API_KEY", "api.fireworks.ai", "accounts/fireworks/models/muse-spark-open"),
    ],
)
def test_open_provider_picks_key_url_and_model(monkeypatch, provider, key_name, host, model):
    monkeypatch.setattr("app.config.load_dotenv", lambda *_: None)
    monkeypatch.setenv("OPEN_PROVIDER", provider)
    monkeypatch.setenv(key_name, FAKE_KEY)
    monkeypatch.delenv("OPEN_BASE_URL", raising=False)
    monkeypatch.delenv("SPARK_OPEN_MODEL", raising=False)
    settings = get_settings()
    assert settings.open_api_key == FAKE_KEY
    assert host in settings.open_base_url
    assert settings.spark_open_model == model


def test_a_key_for_the_other_provider_is_not_used(monkeypatch):
    monkeypatch.setattr("app.config.load_dotenv", lambda *_: None)
    monkeypatch.setenv("OPEN_PROVIDER", "baseten")
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    monkeypatch.setenv("FIREWORKS_API_KEY", FAKE_KEY)
    assert get_settings().open_api_key == ""


@pytest.mark.parametrize(
    ("engine", "effort", "expected"),
    [
        ("hosted", "low", "muse-spark-1.3"),
        ("hosted", "high", "muse-spark-1.3"),
        ("open", "low", OPEN),
        ("open", "high", OPEN),
        ("mixed", "low", OPEN),
        ("mixed", "high", "muse-spark-1.3"),
    ],
)
def test_engine_picks_the_model(engine, effort, expected):
    assert model_for(engine, effort, Settings()) == expected


def test_open_engine_serves_every_turn_on_open_weights(world):
    client = client_for(world, [investigate(), drafts(), fake_tool_call("submit_incidents", submission())])
    result = run_triage(world.conn, client, world.settings, engine="open")
    assert result.status == "passed"
    assert {r["model"] for r in client.requests} == {OPEN}


def test_mixed_engine_keeps_planning_on_hosted_and_low_effort_on_open(world):
    lookups = [fake_tool_call("cmdb_lookup", {"query": "FW-01"}, call_id=f"c{i}") for i in range(4)]
    script = lookups + [
        investigate(),
        drafts(),
        fake_tool_call("submit_incidents", submission(), call_id="c9"),
    ]
    client = client_for(world, script)
    result = run_triage(world.conn, client, world.settings, engine="mixed")
    assert result.status == "passed"
    models = [r["model"] for r in client.requests]
    assert models[:3] == ["muse-spark-1.3"] * 3  # the planning turns
    assert OPEN in models  # the bulk tool turns
    assert models[-1] == "muse-spark-1.3"  # submitting after drafting is high effort again


def test_portable_input_drops_the_other_models_reasoning_and_ids():
    messages = [
        {"role": "user", "content": "digest"},
        {"type": "reasoning", "id": "rs_1", "encrypted_content": "BLOB", "_model": "muse-spark-1.3"},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "c1",
            "name": "x",
            "arguments": "{}",
            "status": "completed",
            "_model": "muse-spark-1.3",
        },
        {"type": "function_call_output", "call_id": "c1", "output": "{}"},
    ]
    to_open = portable_input(messages, OPEN)
    assert [i.get("type") for i in to_open] == [None, "function_call", "function_call_output"]
    assert to_open[1] == {"type": "function_call", "call_id": "c1", "name": "x", "arguments": "{}"}

    same = portable_input(messages, "muse-spark-1.3")
    assert same[1]["encrypted_content"] == "BLOB"
    assert same[2]["id"] == "fc_1"
    assert all("_model" not in i for i in same)


def test_mixed_run_never_sends_the_private_tag(world):
    lookups = [fake_tool_call("cmdb_lookup", {"query": "FW-01"}, call_id=f"c{i}") for i in range(4)]
    script = lookups + [
        investigate(),
        drafts(),
        fake_tool_call("submit_incidents", submission(), call_id="c9"),
    ]
    client = client_for(world, script)
    run_triage(world.conn, client, world.settings, engine="mixed")
    assert "_model" not in json.dumps(client.requests, default=str)
