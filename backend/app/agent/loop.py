"""The triage harness (DESIGN.md §8.3): model turn, tools, verifier, re-plan.

At most 24 turns per attempt and 3 attempts. Prior output items, including encrypted reasoning, are replayed each
turn so reasoning carries across turns. A failed check goes back as text and the agent re-plans. Running out of
budget is a hard stop (R9), not a re-plan.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ..budget import BudgetExceeded
from ..config import Settings
from ..events import emit, now_iso
from ..meta_client import MetaClient
from ..pii import PiiScrubber, UnscrubbedPayloadError
from ..schemas import ClientEmail, Incident, Submission, Ticket, VerifierResult
from ..store import all_alerts, all_devices, contact_names, load_contacts, load_sim_state
from ..verifier.verify import VerifierInputs, load_allowlist, load_ground_truth, verify
from . import digest, prompts, router, selectors
from .tools import DEFINITIONS, ToolContext, run_tool
from .tools.submit import DEFINITION as SUBMIT_DEFINITION

MAX_TURNS = 24
MAX_ATTEMPTS = 3
DEMO_STRICT_TURNS = 6
# Reasoning tokens count toward max_output_tokens, so a high-effort planning turn needs more room than §14's 4k.
MAX_OUTPUT = {"low": 4000, "high": 12000}


@dataclass
class TriageResult:
    run_id: str
    status: str  # passed | failed | budget_exceeded | error
    attempts: int
    verifier: VerifierResult | None
    incident_ids: list[str]
    error: str | None = None


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:10]}"


def start_run(
    conn: sqlite3.Connection, run_id: str, kind: str, mode: str, cassette: str | None = None
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO runs (id, kind, mode, started_at, status, cassette) VALUES (?,?,?,?, 'running', ?)",
        (run_id, kind, mode, now_iso(), cassette),
    )
    emit(conn, run_id, "run_started", {"run_id": run_id, "kind": kind, "mode": mode, "cassette": cassette})


def finish_run(conn: sqlite3.Connection, run_id: str, status: str, attempts: int = 0) -> dict:
    totals = conn.execute(
        "SELECT COALESCE(SUM(est_cost_usd),0) AS c, COALESCE(SUM(tokens_in),0) AS i, COALESCE(SUM(tokens_out),0) AS o "
        "FROM calls WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    conn.execute(
        "UPDATE runs SET finished_at = ?, attempts = ?, cost_usd = ?, tokens_in = ?, tokens_out = ?, status = ? WHERE id = ?",
        (now_iso(), attempts, totals["c"], totals["i"], totals["o"], status, run_id),
    )
    summary = {
        "status": status,
        "attempts": attempts,
        "cost_usd": totals["c"],
        "tokens_in": totals["i"],
        "tokens_out": totals["o"],
    }
    emit(conn, run_id, "run_finished", summary)
    return summary


def _drafts(conn: sqlite3.Connection, run_id: str, kind: str) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT id, body FROM drafts WHERE run_id = ? AND kind = ?", (run_id, kind)
    ).fetchall()
    return {r["id"]: json.loads(r["body"]) for r in rows}


def _logs_map(conn: sqlite3.Connection) -> dict:
    from ..schemas import LogLine

    return {r["id"]: LogLine(**dict(r)) for r in conn.execute("SELECT * FROM logs")}


def build_inputs(
    conn: sqlite3.Connection, run_id: str, attempt: int, generated_dir: Path, production: bool
) -> VerifierInputs:
    tokens = {r["token"] for r in conn.execute("SELECT token FROM pii_map")}
    retrieved = {
        r["log_id"] for r in conn.execute("SELECT log_id FROM retrieved_logs WHERE run_id = ?", (run_id,))
    }
    return VerifierInputs(
        run_id=run_id,
        attempt=attempt,
        alerts={a.id: a for a in all_alerts(conn)},
        logs=_logs_map(conn),
        devices={d.id: d for d in all_devices(conn)},
        retrieved_log_ids=retrieved,
        tickets=_drafts(conn, run_id, "ticket"),
        emails=_drafts(conn, run_id, "email"),
        allowed_fix_ids=load_allowlist(generated_dir),
        known_pii_tokens=tokens,
        ground_truth=None if production else load_ground_truth(generated_dir),
    )


def save_incidents(
    conn: sqlite3.Connection, run_id: str, sub: Submission, inp: VerifierInputs, status: str
) -> list[str]:
    conn.execute("DELETE FROM incidents WHERE run_id = ?", (run_id,))
    ids = []
    for inc in sub.incidents:
        ticket_body = inp.tickets.get(inc.ticket_id or "")
        email_body = inp.emails.get(inc.email_id or "")
        ticket = Ticket(ticket_id=inc.ticket_id, **ticket_body).model_dump() if ticket_body else None
        email = ClientEmail(email_id=inc.email_id, **email_body).model_dump() if email_body else None
        record = Incident(
            id=inc.id,
            run_id=run_id,
            title=inc.title,
            root_cause_device_id=inc.root_cause_device_id,
            root_cause=inc.root_cause,
            alert_ids=inc.alert_ids,
            claims=inc.claims,
            proposed_fix_id=inc.proposed_fix_id,
            ticket=ticket,
            client_email=email,
            status=status,
            confidence=inc.confidence,
            created_at=now_iso(),
        )
        conn.execute(
            "INSERT INTO incidents (id, run_id, title, root_cause_device_id, root_cause, alert_ids, claims, proposed_fix_id, "
            "ticket, client_email, status, voice_notes, confidence, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.id,
                run_id,
                record.title,
                record.root_cause_device_id,
                record.root_cause,
                json.dumps(record.alert_ids),
                json.dumps([c.model_dump() for c in record.claims]),
                record.proposed_fix_id,
                json.dumps(ticket) if ticket else None,
                json.dumps(email) if email else None,
                status,
                "[]",
                record.confidence,
                record.created_at,
            ),
        )
        ids.append(inc.id)
    return ids


class Harness:
    """Holds per-run state for the submit callback."""

    def __init__(self, conn, run_id: str, generated_dir: Path, production: bool) -> None:
        self.conn = conn
        self.run_id = run_id
        self.generated_dir = Path(generated_dir)
        self.production = production
        self.attempt = 1
        self.last_result: VerifierResult | None = None
        self.incident_ids: list[str] = []

    def on_submit(self, args: dict) -> dict:
        errors: list[str] = []
        incidents = []
        raw_incidents = args.get("incidents") or []
        if isinstance(raw_incidents, str):  # some turns send the array as a JSON string
            try:
                raw_incidents = json.loads(raw_incidents)
            except json.JSONDecodeError:
                return {
                    "error": "incidents must be an array of objects, not a string. Call submit_incidents again."
                }
        if not isinstance(raw_incidents, list) or not all(isinstance(r, dict) for r in raw_incidents):
            return {"error": "incidents must be an array of objects. Call submit_incidents again."}
        for raw in raw_incidents:
            expanded, errs = selectors.expand_items(self.conn, list(raw.get("alert_ids") or []))
            errors.extend(errs)
            incidents.append({**raw, "alert_ids": expanded})
        noise, errs = selectors.expand_items(self.conn, list(args.get("noise_alert_ids") or []))
        errors.extend(errs)
        try:
            submission = Submission(incidents=incidents, noise_alert_ids=noise)
        except ValidationError as error:
            detail = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()[:8]
            )
            return {
                "error": f"submission does not match the schema: {detail}. Fix it and call submit_incidents again."
            }
        inp = build_inputs(self.conn, self.run_id, self.attempt, self.generated_dir, self.production)
        result = verify(submission, inp)
        if errors:
            result.feedback_for_agent = (
                result.feedback_for_agent + "\nSelector problems:\n- " + "\n- ".join(errors[:10])
            ).strip()
        self.last_result = result
        self.conn.execute(
            "INSERT OR REPLACE INTO verifier_results (run_id, attempt, result) VALUES (?,?,?)",
            (self.run_id, self.attempt, result.model_dump_json()),
        )
        self.incident_ids = save_incidents(
            self.conn, self.run_id, submission, inp, "verified" if result.passed else "draft"
        )
        emit(
            self.conn,
            self.run_id,
            "submission",
            {
                "attempt": self.attempt,
                "incidents": [
                    {
                        "id": i.id,
                        "title": i.title,
                        "root_cause_device_id": i.root_cause_device_id,
                        "root_cause": i.root_cause,
                        "alert_ids": i.alert_ids,
                        "proposed_fix_id": i.proposed_fix_id,
                    }
                    for i in submission.incidents
                ],
                "noise_alert_ids": submission.noise_alert_ids,
            },
        )
        emit(self.conn, self.run_id, "verifier", result.model_dump())
        return result.model_dump(exclude={"run_id"})


def run_triage(
    conn: sqlite3.Connection,
    client: MetaClient,
    settings: Settings,
    run_id: str | None = None,
    demo_strict: bool = False,
    production: bool = False,
    max_turns: int = MAX_TURNS,
    max_attempts: int = MAX_ATTEMPTS,
) -> TriageResult:
    run_id = run_id or client.run_id or new_run_id()
    client.run_id = run_id
    mode = "replay" if type(client.transport).__name__ == "ReplayTransport" else settings.mode
    conn.execute("SELECT 1")
    if not conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone():
        start_run(conn, run_id, "triage", mode)

    contacts = load_contacts(settings.generated_dir)
    scrubber = PiiScrubber(conn, names=contact_names(contacts))
    client.names_guard = scrubber.names
    harness = Harness(conn, run_id, settings.generated_dir, production)
    ctx = ToolContext(
        conn=conn,
        run_id=run_id,
        scrubber=scrubber,
        sim=load_sim_state(settings.generated_dir),
        on_submit=harness.on_submit,
    )

    messages: list = [{"role": "user", "content": digest.build_digest(conn)}]
    attempt = 0
    status = "failed"
    error_text = None
    try:
        for attempt in range(1, max_attempts + 1):
            harness.attempt = ctx.attempt = attempt
            ctx.drafted = False
            strict_now = demo_strict and attempt == 1
            turn_cap = DEMO_STRICT_TURNS if strict_now else max_turns
            emit(
                conn,
                run_id,
                "attempt",
                {"attempt": attempt, "max_attempts": max_attempts, "turn_cap": turn_cap},
            )
            submitted = False
            for turn in range(1, turn_cap + 1):
                ctx.turn = turn
                if turn == turn_cap:
                    messages.append({"role": "user", "content": prompts.LAST_TURN_NUDGE})
                effort = router.pick_effort(turn, ctx.drafted, strict_now)
                # On the last turn submit_incidents is the only tool on offer, so a rushed plan reaches the verifier
                # rather than timing out unseen. The API supports only tool_choice "auto", so it cannot be forced.
                response = client.responses(
                    purpose=router.purpose_for(effort, attempt),
                    model=settings.spark_model,
                    instructions=prompts.SYSTEM_PROMPT,
                    input=messages,
                    tools=[SUBMIT_DEFINITION] if turn == turn_cap else DEFINITIONS,
                    effort=effort,
                    max_output_tokens=MAX_OUTPUT[effort],
                    store=False,
                    include=["reasoning.encrypted_content"],
                )
                messages = messages + response.output
                _emit_reasoning(conn, run_id, attempt, turn, response.output)
                calls = response.function_calls
                if not calls:
                    messages.append({"role": "user", "content": prompts.PLAIN_TEXT_NUDGE})
                    continue
                verdict: dict | None = None
                for call in calls:
                    result = run_tool(ctx, call["name"], call.get("arguments", "{}"))
                    messages.append(
                        {
                            "type": "function_call_output",
                            "call_id": call["call_id"],
                            "output": json.dumps(result),
                        }
                    )
                    if call["name"] == "submit_incidents" and "passed" in result:
                        verdict = result
                if verdict is not None:
                    submitted = True
                    break
            if harness.last_result is not None and submitted and harness.last_result.passed:
                status = "passed"
                break
            feedback = (
                harness.last_result.feedback_for_agent
                if submitted and harness.last_result
                else "You did not call submit_incidents before the turn limit."
            )
            if attempt < max_attempts:
                note = prompts.REPLAN_PREFIX.format(attempt=attempt, max_attempts=max_attempts) + feedback
                messages.append({"role": "user", "content": scrubber.scrub(note)})
                emit(conn, run_id, "replan", {"attempt": attempt, "feedback": feedback})
    except BudgetExceeded as error:
        status, error_text = "budget_exceeded", str(error)
    except UnscrubbedPayloadError as error:
        status, error_text = "error", str(error)
    except Exception as error:  # a run must always finish with a status; the UI reports the failure
        status, error_text = "error", client._redact(f"{type(error).__name__}: {error}")

    if error_text:
        emit(conn, run_id, "run_error", {"message": error_text})
    if status == "failed" and harness.incident_ids:
        conn.execute("UPDATE incidents SET status = 'failed' WHERE run_id = ?", (run_id,))
    finish_run(conn, run_id, status, attempt)
    return TriageResult(run_id, status, attempt, harness.last_result, harness.incident_ids, error_text)


def _emit_reasoning(conn, run_id: str, attempt: int, turn: int, output: list[dict]) -> None:
    summaries = []
    for item in output:
        if item.get("type") == "reasoning":
            summaries.extend(
                part.get("text", "") for part in item.get("summary") or [] if isinstance(part, dict)
            )
    text = [item for item in output if item.get("type") == "message"]
    emit(
        conn,
        run_id,
        "turn",
        {
            "attempt": attempt,
            "turn": turn,
            "reasoning_summary": " ".join(summaries)[:1500],
            "tool_calls": [i.get("name") for i in output if i.get("type") == "function_call"],
            "said_text": bool(text),
        },
    )
