"""One passing and one failing fixture for every verifier rule (DESIGN.md §15)."""

from __future__ import annotations

import copy

import pytest
from app.agent.loop import build_inputs
from app.schemas import Grouping, Submission
from app.verifier.verify import RULES, grouping_metrics, verify

INC_A_IDS = [f"A-{i:04d}" for i in range(1, 7)]
INC_B_IDS = [f"A-{i:04d}" for i in range(7, 11)]
NOISE = ["A-0011", "A-0012", "A-0013"]


def good_submission() -> dict:
    return {
        "incidents": [
            {
                "id": "INC-1",
                "title": "Access switch uplink flapping",
                "root_cause_device_id": "dev_A_12",
                "root_cause": "Bad SFP on SW-ACC-02",
                "alert_ids": list(INC_A_IDS),
                "claims": [
                    {
                        "id": "c1",
                        "text": "SW-ACC-02 in rack A U12 lost Te1/1/1",
                        "citations": ["L-0001"],
                        "kind": "root_cause",
                    },
                    {
                        "id": "c2",
                        "text": "SFP rx power is below threshold",
                        "citations": ["L-0003"],
                        "kind": "evidence",
                    },
                ],
                "proposed_fix_id": "FIX-SFP-REPLACE",
                "ticket_id": "TKT-1",
                "email_id": "EML-1",
            },
            {
                "id": "INC-2",
                "title": "NAS volume full",
                "root_cause_device_id": "dev_A_04",
                "root_cause": "NAS-01 volume 1 is full",
                "alert_ids": list(INC_B_IDS),
                "claims": [
                    {
                        "id": "c1",
                        "text": "NAS-01 at U4-5 is at 98%",
                        "citations": ["L-0004", "L-0005"],
                        "kind": "root_cause",
                    }
                ],
                "proposed_fix_id": "FIX-NAS-PRUNE-SNAPSHOTS",
                "ticket_id": "TKT-2",
                "email_id": "EML-2",
            },
        ],
        "noise_alert_ids": list(NOISE),
    }


def seed_drafts(conn, run_id="run_t"):
    drafts = [
        (
            "TKT-1",
            "ticket",
            "INC-1",
            {
                "incident_id": "INC-1",
                "title": "t",
                "priority": "P1",
                "summary": "s",
                "affected_device_ids": ["dev_A_12"],
                "steps": ["x"],
                "citations": ["L-0001", "L-0003"],
            },
        ),
        (
            "TKT-2",
            "ticket",
            "INC-2",
            {
                "incident_id": "INC-2",
                "title": "t",
                "priority": "P2",
                "summary": "s",
                "affected_device_ids": ["dev_A_04"],
                "steps": ["x"],
                "citations": ["L-0004", "L-0005"],
            },
        ),
        (
            "EML-1",
            "email",
            "INC-1",
            {
                "incident_id": "INC-1",
                "subject": "Network issue",
                "body": "A network switch failed. We are replacing a part.",
                "tone": "calm",
            },
        ),
        (
            "EML-2",
            "email",
            "INC-2",
            {
                "incident_id": "INC-2",
                "subject": "Backups",
                "body": "Your backup storage is full. We are clearing old copies.",
                "tone": "calm",
            },
        ),
    ]
    import json

    for draft_id, kind, inc, body in drafts:
        conn.execute(
            "INSERT OR REPLACE INTO drafts VALUES (?,?,?,?,?)",
            (run_id, draft_id, kind, inc, json.dumps(body)),
        )


def seed_retrieved(conn, ids=("L-0001", "L-0003", "L-0004", "L-0005"), run_id="run_t"):
    for log_id in ids:
        conn.execute("INSERT OR IGNORE INTO retrieved_logs VALUES (?, ?)", (run_id, log_id))


@pytest.fixture
def check(world):
    seed_drafts(world.conn)
    seed_retrieved(world.conn)

    def run(data=None, production=False, **patch):
        inputs = build_inputs(world.conn, "run_t", 1, world.dataset, production)
        for key, value in patch.items():
            setattr(inputs, key, value)
        return verify(Submission(**(data or good_submission())), inputs)

    return run


def failures(result, rule):
    return [c for c in result.checks if c.rule == rule and not c.passed]


def test_good_submission_passes_every_rule(check):
    result = check()
    assert result.passed, result.feedback_for_agent
    assert {c.rule for c in result.checks} == set(RULES)
    assert all(c.passed for c in result.checks)
    assert result.feedback_for_agent == ""
    assert result.grouping.f1 == 1.0 and result.grouping.ari == 1.0


def test_r1_claim_without_citation(check):
    data = good_submission()
    data["incidents"][0]["claims"][1]["citations"] = []
    result = check(data)
    assert not result.passed
    assert "claim c2 in INC-1 has no citation" in failures(result, "R1_citations_exist")[0].detail
    assert "claim c2 in INC-1 has no citation" in result.feedback_for_agent


def test_r1_incident_without_claims(check):
    data = good_submission()
    data["incidents"][0]["claims"] = []
    assert failures(check(data), "R1_citations_exist")


def test_r2_citation_does_not_exist(check):
    data = good_submission()
    data["incidents"][0]["claims"][0]["citations"] = ["L-9999"]
    assert "L-9999 does not exist" in failures(check(data), "R2_citations_real")[0].detail


def test_r2_citation_never_retrieved(check):
    data = good_submission()
    data["incidents"][0]["claims"][0]["citations"] = ["L-0002"]  # real, but no log_search returned it
    assert "L-0002 was never retrieved" in failures(check(data), "R2_citations_real")[0].detail


def test_r3_citation_from_an_unrelated_device(world, check):
    seed_retrieved(world.conn, ["L-0007"])
    data = good_submission()
    data["incidents"][1]["claims"][0]["citations"] = ["L-0007"]  # FW-01 is not related to the NAS incident
    assert "L-0007" in failures(check(data), "R3_citation_relevance")[0].detail


def test_r3_citation_outside_the_window(world, check):
    seed_retrieved(world.conn, ["L-0008"])
    data = good_submission()
    data["incidents"][0]["claims"][0]["citations"] = ["L-0008"]  # right device, three hours early
    assert "outside the alert window" in failures(check(data), "R3_citation_relevance")[0].detail


def test_r4_device_not_in_cmdb(check):
    data = good_submission()
    data["incidents"][0]["root_cause_device_id"] = "dev_Z_99"
    assert "not in the CMDB" in failures(check(data), "R4_device_resolves")[0].detail


def test_r4_wrong_u_position(check):
    data = good_submission()
    data["incidents"][0]["claims"][0]["text"] = "SW-ACC-02 in rack A U30 lost Te1/1/1"
    assert "wrong U position" in failures(check(data), "R4_device_resolves")[0].detail


def test_r4_missing_u_position(check):
    data = good_submission()
    data["incidents"][0]["claims"][0]["text"] = "SW-ACC-02 lost Te1/1/1"
    assert "do not state the rack U" in failures(check(data), "R4_device_resolves")[0].detail


def test_r5_unassigned_alerts(check):
    data = good_submission()
    data["noise_alert_ids"] = ["A-0011"]
    result = check(data)
    detail = failures(result, "R5_coverage")[0].detail
    assert "2 alerts unassigned" in detail and "A-0012" in detail
    assert result.grouping.unassigned_alerts == 2


def test_r5_alert_assigned_twice(check):
    data = good_submission()
    data["noise_alert_ids"].append("A-0001")
    assert "assigned twice" in failures(check(data), "R5_coverage")[0].detail


def test_r5_unknown_alert(check):
    data = good_submission()
    data["noise_alert_ids"].append("A-9999")
    assert "unknown alert ids" in failures(check(data), "R5_coverage")[0].detail


def test_r6_merge_names_the_conflict_not_the_answer(check):
    data = good_submission()
    data["incidents"][0]["alert_ids"] = INC_A_IDS + INC_B_IDS
    data["incidents"] = data["incidents"][:1]
    result = check(data)
    details = " ".join(c.detail for c in failures(result, "R6_grouping_vs_truth"))
    assert "merges alerts with different failure signatures" in details
    assert "INC-A" not in details and "INC-B" not in details
    assert "INC-A" not in result.feedback_for_agent


def test_r6_wrong_root_cause_does_not_reveal_the_device(check):
    data = good_submission()
    data["incidents"][0]["root_cause_device_id"] = "dev_A_08"
    data["incidents"][0]["claims"][0]["text"] = "SRV-DC01 at U8 crashed"
    result = check(data)
    detail = " ".join(c.detail for c in failures(result, "R6_grouping_vs_truth"))
    assert "does not fit the evidence" in detail
    assert "dev_A_12" not in detail and "SW-ACC-02" not in detail


def test_r6_incident_sent_to_noise(check):
    data = good_submission()
    data["noise_alert_ids"] += INC_B_IDS
    data["incidents"] = data["incidents"][:1]
    assert "no incident of their own" in failures(check(data), "R6_grouping_vs_truth")[0].detail


def test_r6_is_off_in_production_mode(check):
    data = good_submission()
    data["incidents"][0]["root_cause_device_id"] = "dev_A_08"
    data["incidents"][0]["claims"][0]["text"] = "SRV-DC01 at U8 crashed"
    result = check(data, production=True)
    assert result.passed, result.feedback_for_agent
    assert "R6_grouping_vs_truth" not in {c.rule for c in result.checks}


def test_r7_unapproved_fix(check):
    data = good_submission()
    data["incidents"][0]["proposed_fix_id"] = "FIX-FACTORY-RESET"
    assert "FIX-FACTORY-RESET is not approved" in failures(check(data), "R7_fix_allowed")[0].detail


def test_r7_approved_fix_that_does_not_fit(check):
    data = good_submission()
    data["incidents"][0]["proposed_fix_id"] = "FIX-NAS-PRUNE-SNAPSHOTS"
    assert "does not address the cause" in failures(check(data), "R7_fix_allowed")[0].detail


def test_r7_escalate_is_always_allowed(check):
    data = good_submission()
    data["incidents"][0]["proposed_fix_id"] = "ESCALATE"
    assert not failures(check(data), "R7_fix_allowed")


def test_r8_missing_drafts(check):
    data = good_submission()
    data["incidents"][0]["ticket_id"] = "TKT-404"
    data["incidents"][0]["email_id"] = None
    details = " ".join(c.detail for c in failures(check(data), "R8_drafts_clean"))
    assert "no ticket draft" in details and "no client email draft" in details


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Contact jane.doe@clientco.example for details.", "raw PII"),
        ("See L-0193 for details.", "log ids"),
        ("word " * 181, "181 words"),
        ("The %LINK-3-UPDOWN event on Te1/1/1 happened.", "jargon"),
        ("Hello <PERSON_99>, your service is back.", "do not exist"),
    ],
)
def test_r8_dirty_emails(world, check, body, expected):
    import json

    world.conn.execute(
        "UPDATE drafts SET body = ? WHERE id = 'EML-1'",
        (json.dumps({"incident_id": "INC-1", "subject": "s", "body": body, "tone": "calm"}),),
    )
    assert expected in " ".join(c.detail for c in failures(check(), "R8_drafts_clean"))


def test_r8_known_placeholder_is_fine(world, check):
    import json

    token = world.conn.execute("SELECT token FROM pii_map WHERE kind = 'PERSON' LIMIT 1").fetchone()["token"]
    world.conn.execute(
        "UPDATE drafts SET body = ? WHERE id = 'EML-1'",
        (
            json.dumps(
                {
                    "incident_id": "INC-1",
                    "subject": "s",
                    "body": f"Hello {token}, we fixed it.",
                    "tone": "calm",
                }
            ),
        ),
    )
    assert not failures(check(), "R8_drafts_clean")


def test_r8_ticket_needs_two_citations(world, check):
    import json

    world.conn.execute(
        "UPDATE drafts SET body = ? WHERE id = 'TKT-1'",
        (
            json.dumps(
                {
                    "incident_id": "INC-1",
                    "title": "t",
                    "priority": "P1",
                    "summary": "s",
                    "affected_device_ids": [],
                    "steps": [],
                    "citations": ["L-0001"],
                }
            ),
        ),
    )
    assert "at least 2" in failures(check(), "R8_drafts_clean")[0].detail


def test_feedback_is_deterministic(check):
    data = good_submission()
    data["incidents"][0]["proposed_fix_id"] = "FIX-FACTORY-RESET"
    assert check(copy.deepcopy(data)).feedback_for_agent == check(copy.deepcopy(data)).feedback_for_agent


def test_grouping_metrics_shape():
    truth = {"a": "X", "b": "X", "c": "Y", "d": "Y", "e": None}
    perfect = grouping_metrics({"a": "1", "b": "1", "c": "2", "d": "2"}, truth)
    assert isinstance(perfect, Grouping) and perfect.f1 == 1.0 and perfect.ari == 1.0
    merged = grouping_metrics({"a": "1", "b": "1", "c": "1", "d": "1"}, truth)
    assert merged.recall == 1.0 and merged.precision < 1.0 and merged.f1 < 1.0
    split = grouping_metrics({"a": "1", "b": "2", "c": "3", "d": "4"}, truth)
    assert split.recall == 0.0


def test_empty_incident_gets_a_plain_message(check):
    data = good_submission()
    data["incidents"][0]["alert_ids"] = []
    result = check(data)
    assert "INC-1 has no alert_ids" in " ".join(c.detail for c in failures(result, "R5_coverage"))
    assert "does not fit the evidence" not in result.feedback_for_agent
