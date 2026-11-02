"""The agent loop with scripted model turns: pass, fail then re-plan then pass, budget exhaustion, and more."""

from __future__ import annotations

import json

from app.agent.digest import build_digest
from app.agent.loop import run_triage
from app.agent.router import pick_effort, purpose_for
from app.config import Settings
from app.fake_meta_client import FakeMetaClient, fake_text, fake_tool_call, fake_tool_calls

SELECT_A = ["type:host_down", "type:dns_timeout", "type:backup_failed AND message:network path"]
SELECT_B = ["type:disk_full", "type:snapshot_failed", "type:backup_failed AND message:no space"]
SELECT_NOISE = ["type:user_ticket", "type:cpu_high"]


def investigate():
    """One turn of tool calls that retrieves all the logs the answer cites."""
    return fake_tool_calls(
        [
            ("cmdb_lookup", {"query": "SW-ACC-02"}),
            ("log_search", {"query": "Te1/1/1 SFP flapping", "device_id": "dev_A_12", "limit": 10}),
            ("log_search", {"query": "Volume usage backup snapshot", "device_id": "dev_A_04", "limit": 10}),
            ("ping_sim", {"target": "SRV-DC01"}),
            ("kb_search", {"query": "SFP link flap", "limit": 2}),
        ]
    )


def drafts():
    return fake_tool_calls(
        [
            (
                "draft_ticket",
                {
                    "incident_id": "INC-1",
                    "title": "Switch",
                    "priority": "P1",
                    "summary": "s",
                    "affected_device_ids": ["dev_A_12"],
                    "steps": ["Replace the SFP"],
                    "citations": ["L-0001", "L-0003"],
                },
            ),
            (
                "draft_client_email",
                {
                    "incident_id": "INC-1",
                    "subject": "Network issue",
                    "body": "A switch failed and we are replacing a part.",
                    "tone": "calm",
                },
            ),
            (
                "draft_ticket",
                {
                    "incident_id": "INC-2",
                    "title": "NAS",
                    "priority": "P2",
                    "summary": "s",
                    "affected_device_ids": ["dev_A_04"],
                    "steps": ["Prune"],
                    "citations": ["L-0004", "L-0005"],
                },
            ),
            (
                "draft_client_email",
                {
                    "incident_id": "INC-2",
                    "subject": "Backups",
                    "body": "Your backup storage is full and we are clearing old copies.",
                    "tone": "calm",
                },
            ),
        ]
    )


def submission(fix="FIX-SFP-REPLACE", citations=("L-0001",)):
    return {
        "incidents": [
            {
                "id": "INC-1",
                "title": "Access switch uplink flapping",
                "root_cause_device_id": "dev_A_12",
                "root_cause": "Bad SFP on SW-ACC-02",
                "alert_ids": SELECT_A,
                "claims": [
                    {
                        "id": "c1",
                        "text": "SW-ACC-02 in rack A U12 lost Te1/1/1",
                        "citations": list(citations),
                        "kind": "root_cause",
                    }
                ],
                "proposed_fix_id": fix,
                "ticket_id": "TKT-1",
                "email_id": "EML-1",
                "confidence": 0.9,
            },
            {
                "id": "INC-2",
                "title": "NAS volume full",
                "root_cause_device_id": "dev_A_04",
                "root_cause": "NAS-01 volume 1 is full",
                "alert_ids": SELECT_B,
                "claims": [
                    {
                        "id": "c1",
                        "text": "NAS-01 at U4 is at 98%",
                        "citations": ["L-0004"],
                        "kind": "root_cause",
                    }
                ],
                "proposed_fix_id": "FIX-NAS-PRUNE-SNAPSHOTS",
                "ticket_id": "TKT-2",
                "email_id": "EML-2",
            },
        ],
        "noise_alert_ids": SELECT_NOISE,
    }


def client_for(world, script, settings=None):
    return FakeMetaClient(script=script, settings=settings or world.settings, conn=world.conn, run_id="run_a")


def events(conn, run_id="run_a"):
    return [
        r["type"] for r in conn.execute("SELECT type FROM events WHERE run_id = ? ORDER BY seq", (run_id,))
    ]


def test_pass_path(world):
    client = client_for(world, [investigate(), drafts(), fake_tool_call("submit_incidents", submission())])
    result = run_triage(world.conn, client, world.settings)
    assert result.status == "passed", result.verifier.feedback_for_agent if result.verifier else result.error
    assert result.attempts == 1
    assert result.verifier.grouping.f1 == 1.0
    rows = world.conn.execute(
        "SELECT id, status, alert_ids FROM incidents WHERE run_id = 'run_a' ORDER BY id"
    ).fetchall()
    assert [r["status"] for r in rows] == ["verified", "verified"]
    assert len(json.loads(rows[0]["alert_ids"])) == 6  # the selectors were expanded
    run = world.conn.execute("SELECT status, attempts FROM runs WHERE id = 'run_a'").fetchone()
    assert run["status"] == "passed" and run["attempts"] == 1
    seen = events(world.conn)
    assert seen[0] == "run_started" and seen[-1] == "run_finished"
    for expected in ("attempt", "turn", "tool", "call", "submission", "verifier"):
        assert expected in seen


def test_fail_then_replan_then_pass(world):
    bad = submission(fix="FIX-FACTORY-RESET")
    bad["incidents"][0]["claims"].append(
        {"id": "c2", "text": "Users are affected", "citations": [], "kind": "impact"}
    )
    client = client_for(
        world,
        [
            investigate(),
            drafts(),
            fake_tool_call("submit_incidents", bad),
            fake_tool_call("submit_incidents", submission(), call_id="call_9"),
        ],
    )
    result = run_triage(world.conn, client, world.settings)
    assert result.status == "passed"
    assert result.attempts == 2
    assert "replan" in events(world.conn)

    first = json.loads(
        world.conn.execute(
            "SELECT result FROM verifier_results WHERE run_id = 'run_a' AND attempt = 1"
        ).fetchone()["result"]
    )
    assert first["passed"] is False
    failed = {c["rule"] for c in first["checks"] if not c["passed"]}
    assert {"R1_citations_exist", "R7_fix_allowed"} <= failed

    replan_input = client.requests[3]["input"]
    note = next(
        i["content"]
        for i in replan_input
        if isinstance(i.get("content"), str) and i["content"].startswith("VERIFIER FAILED")
    )
    assert "attempt 1/3" in note and "FIX-FACTORY-RESET is not approved" in note and "claim c2" in note
    assert client.requests[3]["purpose"] == "agent_replan"


def test_three_failures_end_as_failed(world):
    bad = submission(fix="FIX-FACTORY-RESET")
    client = client_for(
        world, [investigate(), drafts()] + [fake_tool_call("submit_incidents", bad) for _ in range(3)]
    )
    result = run_triage(world.conn, client, world.settings)
    assert result.status == "failed" and result.attempts == 3
    statuses = {
        r["status"] for r in world.conn.execute("SELECT status FROM incidents WHERE run_id = 'run_a'")
    }
    assert statuses == {"failed"}


def test_budget_exhaustion_is_a_hard_stop(world):
    expensive = {"input_tokens": 2_000_000, "output_tokens": 0}
    tight = Settings(api_key="", mode="live", generated_dir=world.dataset, budget_per_run=1.50)
    script = [fake_tool_call("cmdb_lookup", {"query": "FW-01"}, usage=expensive), fake_text("never reached")]
    client = client_for(world, script, tight)
    result = run_triage(world.conn, client, tight)
    assert result.status == "budget_exceeded"
    assert "run budget" in result.error
    assert len(client.requests) == 1  # the second call never left
    assert (
        world.conn.execute("SELECT status FROM runs WHERE id = 'run_a'").fetchone()["status"]
        == "budget_exceeded"
    )


def test_plain_text_gets_a_nudge(world):
    client = client_for(
        world,
        [
            fake_text("I think it is the switch."),
            investigate(),
            drafts(),
            fake_tool_call("submit_incidents", submission()),
        ],
    )
    assert run_triage(world.conn, client, world.settings).status == "passed"
    assert any(
        i.get("content", "").startswith("Plain text")
        for i in client.requests[1]["input"]
        if isinstance(i.get("content"), str)
    )


def test_turn_cap_ends_the_attempt(world):
    lookup = lambda request, purpose: fake_tool_call("cmdb_lookup", {"query": "FW-01"})  # noqa: E731
    client = FakeMetaClient(responder=lookup, settings=world.settings, conn=world.conn, run_id="run_a")
    result = run_triage(world.conn, client, world.settings, max_turns=3, max_attempts=2)
    assert result.status == "failed" and result.attempts == 2
    assert len(client.requests) == 6


def test_no_pii_reaches_the_model(world):
    client = client_for(
        world,
        [
            fake_tool_calls(
                [
                    ("list_alerts", {"filter": {"type": "user_ticket"}, "limit": 10, "offset": 0}),
                    ("log_search", {"query": "Admin session", "limit": 5}),
                ]
            ),
            drafts(),
            fake_tool_call("submit_incidents", submission()),
        ],
    )
    run_triage(world.conn, client, world.settings)
    dump = json.dumps(client.requests, default=str)
    for secret in ("jane.doe", "555-0142", "203.0.113.44", "jdoe", "Jane Doe"):
        assert secret not in dump, secret
    assert "<PERSON_" in dump


def test_prior_output_is_replayed_with_reasoning_items(world):
    reasoning = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [{"type": "summary_text", "text": "plan"}],
        "encrypted_content": "BLOB",
    }
    first = fake_tool_call("cmdb_lookup", {"query": "FW-01"})
    first["output"].insert(0, reasoning)
    client = client_for(
        world, [first, investigate(), drafts(), fake_tool_call("submit_incidents", submission())]
    )
    run_triage(world.conn, client, world.settings)
    assert reasoning in client.requests[1]["input"]
    assert client.requests[0]["include"] == ["reasoning.encrypted_content"]
    assert client.requests[0]["store"] is False


def test_invalid_submission_lets_the_agent_retry(world):
    client = client_for(
        world,
        [
            investigate(),
            drafts(),
            fake_tool_call("submit_incidents", {"incidents": [{"id": "x"}]}),
            fake_tool_call("submit_incidents", submission(), call_id="c9"),
        ],
    )
    result = run_triage(world.conn, client, world.settings)
    assert result.status == "passed" and result.attempts == 1


def test_digest_is_scrubbed_and_small(world):
    text = build_digest(world.conn)
    assert "ALERT BATCH: 13 alerts" in text and "backup_failed" in text
    assert "jane.doe" not in text and "555-0142" not in text


def test_effort_router():
    assert pick_effort(1, False) == "high"
    assert pick_effort(3, False) == "high"
    assert pick_effort(4, False) == "low"
    assert pick_effort(9, True) == "high"
    assert pick_effort(1, False, demo_strict_first_attempt=True) == "low"
    assert purpose_for("high", 2) == "agent_replan"
    assert purpose_for("low", 2) == "agent_tool_turn"
    assert purpose_for("high", 1) == "agent_plan"


def test_key_never_reaches_logs_or_events(world):
    fake_key = "fake-key-abcdefghijklmnopqrstuvwxyz0123"  # pragma: allowlist secret
    keyed = Settings(api_key=fake_key, mode="live", generated_dir=world.dataset)
    client = client_for(
        world, [investigate(), drafts(), fake_tool_call("submit_incidents", submission())], keyed
    )
    run_triage(world.conn, client, keyed)
    for table in ("calls", "events", "tool_calls", "runs", "verifier_results"):
        dump = json.dumps([dict(r) for r in world.conn.execute(f"SELECT * FROM {table}")])
        assert fake_key not in dump, table
