"""The agent's tools against the tiny dataset."""

from __future__ import annotations

import json

from app.agent import selectors
from app.agent.tools import DEFINITIONS, REGISTRY, run_tool


def test_every_tool_has_a_closed_schema():
    assert {d["name"] for d in DEFINITIONS} == set(REGISTRY)

    def closed(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, node
            for value in node.values():
                closed(value)
        elif isinstance(node, list):
            for value in node:
                closed(value)

    for definition in DEFINITIONS:
        closed(definition["parameters"])


def test_cmdb_lookup_by_each_key(world):
    assert run_tool(world.ctx, "cmdb_lookup", {"query": "SW-ACC-02"})["devices"][0]["u_start"] == 12
    assert run_tool(world.ctx, "cmdb_lookup", {"query": "192.0.2.12"})["devices"][0]["id"] == "dev_A_12"
    assert run_tool(world.ctx, "cmdb_lookup", {"query": "dev_A_12"})["devices"][0]["hostname"] == "SW-ACC-02"
    assert run_tool(world.ctx, "cmdb_lookup", {"query": "SN12"})["devices"][0]["id"] == "dev_A_12"
    assert run_tool(world.ctx, "cmdb_lookup", {"query": "rack A U12"})["devices"][0]["id"] == "dev_A_12"
    assert (
        run_tool(world.ctx, "cmdb_lookup", {"query": "U9"})["devices"][0]["id"] == "dev_A_08"
    )  # middle of 8-9
    assert run_tool(world.ctx, "cmdb_lookup", {"query": "nothing-here"})["devices"] == []


def test_list_alerts_filters_and_returns_only_scrubbed_text(world):
    out = run_tool(world.ctx, "list_alerts", {"filter": {"type": "user_ticket"}, "limit": 10, "offset": 0})
    assert out["total"] == 2
    dump = json.dumps(out)
    assert "jane.doe" not in dump and "555-0142" not in dump and "jdoe" not in dump
    assert "<PERSON_" in dump


def test_list_alerts_pages(world):
    first = run_tool(world.ctx, "list_alerts", {"limit": 5, "offset": 0})
    second = run_tool(world.ctx, "list_alerts", {"limit": 5, "offset": 5})
    assert first["total"] == 13
    assert not {a["id"] for a in first["alerts"]} & {a["id"] for a in second["alerts"]}


def test_log_search_scrubs_ranks_and_records_retrieval(world):
    out = run_tool(world.ctx, "log_search", {"query": "SFP rx power", "limit": 5})
    assert out["lines"][0]["id"] == "L-0003"
    rows = world.conn.execute("SELECT log_id FROM retrieved_logs WHERE run_id = 'run_t'").fetchall()
    assert "L-0003" in {r["log_id"] for r in rows}
    mail = run_tool(world.ctx, "log_search", {"query": "Admin session", "limit": 5})
    assert "jane.doe" not in json.dumps(mail)


def test_log_search_survives_fts_syntax(world):
    for query in ['%LINK-3-UPDOWN "quoted" (paren*', "AND OR NOT", "Te1/1/1"]:
        assert "error" not in run_tool(world.ctx, "log_search", {"query": query, "limit": 5})


def test_log_search_filters_by_device_and_time(world):
    out = run_tool(
        world.ctx,
        "log_search",
        {"query": "", "device_id": "dev_A_04", "from_ts": "2026-11-02T01:10:00Z", "limit": 10},
    )
    assert [x["id"] for x in out["lines"]] == ["L-0005", "L-0006"]


def test_ping_breaks_at_the_failed_switch(world):
    out = run_tool(world.ctx, "ping_sim", {"target": "SRV-DC01"})
    assert out["reachable"] is False
    assert out["path"] == ["dev_A_10", "dev_A_41", "dev_A_12"]
    assert run_tool(world.ctx, "ping_sim", {"target": "SW-CORE-01"})["reachable"] is True
    assert run_tool(world.ctx, "ping_sim", {"target": "192.0.2.1"})["reachable"] is True
    assert run_tool(world.ctx, "ping_sim", {"target": "no-such-host"})["path"] == []


def test_dns_fails_because_its_server_is_behind_the_switch(world):
    assert run_tool(world.ctx, "dns_sim", {"name": "dc01.acme.example"})["rcode"] == "SERVFAIL"
    assert run_tool(world.ctx, "dns_sim", {"name": "core.acme.example"})["answers"] == ["192.0.2.2"]
    assert run_tool(world.ctx, "dns_sim", {"name": "nope.acme.example"})["rcode"] == "NXDOMAIN"
    assert (
        run_tool(world.ctx, "dns_sim", {"name": "core.acme.example", "server": "SRV-DC01"})["rcode"]
        == "TIMEOUT"
    )


def test_kb_search_returns_only_listed_fixes(world):
    out = run_tool(world.ctx, "kb_search", {"query": "SFP rx power link flap", "limit": 2})
    assert out["articles"][0]["id"] == "kb-sfp"
    fixes = {f["fix_id"] for a in out["articles"] for f in a["approved_fixes"]}
    assert "FIX-SFP-REPLACE" in fixes
    assert "FIX-FACTORY-RESET" not in fixes


def test_drafts_get_sequential_ids(world):
    args = {"incident_id": "INC-1", "subject": "s", "body": "b", "tone": "calm"}
    assert run_tool(world.ctx, "draft_client_email", args)["email_id"] == "EML-1"
    assert run_tool(world.ctx, "draft_client_email", args)["email_id"] == "EML-2"


def test_bad_arguments_come_back_as_data(world):
    assert "error" in run_tool(world.ctx, "cmdb_lookup", "{not json")
    assert "error" in run_tool(world.ctx, "cmdb_lookup", {})
    assert "error" in run_tool(world.ctx, "no_such_tool", {})
    assert "error" in run_tool(world.ctx, "draft_ticket", {"title": "missing fields"})


def test_tool_calls_are_traced(world):
    run_tool(world.ctx, "cmdb_lookup", {"query": "FW-01"})
    row = world.conn.execute("SELECT name, result FROM tool_calls").fetchone()
    assert row["name"] == "cmdb_lookup" and "FW-01" in row["result"]


def test_no_tool_can_reach_ground_truth():
    """The tool package must not reference ground truth or the verifier's config."""
    from pathlib import Path

    import app.agent.tools as pkg

    for path in Path(pkg.__file__).parent.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "ground_truth" not in text, path
        assert "allowlist" not in text, path


def test_selector_expansion(world):
    ids, errors = selectors.expand_items(world.conn, ["type:backup_failed AND message:no space"])
    assert ids == ["A-0008", "A-0010"] and not errors
    ids, errors = selectors.expand_items(
        world.conn, ["type:host_down AND device_id:dev_A_08", "A-0002", "A-0002"]
    )
    assert ids == ["A-0001", "A-0003", "A-0002"] and not errors


def test_selector_errors_are_reported(world):
    _, errors = selectors.expand_items(world.conn, ["bogus:thing"])
    assert "unknown selector field" in errors[0]
    _, errors = selectors.expand_items(world.conn, ["type:nope"])
    assert "matched nothing" in errors[0]
