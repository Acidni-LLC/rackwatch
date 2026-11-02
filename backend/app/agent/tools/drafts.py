"""draft_ticket and draft_client_email. Drafts are stored per run; ids are sequential so replays match recordings."""

from __future__ import annotations

import json

TICKET_DEFINITION = {
    "type": "function",
    "name": "draft_ticket",
    "description": "Store a PSA ticket draft for an incident. Cite the log ids that support it. Returns ticket_id.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "incident_id": {"type": "string"},
            "title": {"type": "string"},
            "priority": {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
            "summary": {"type": "string"},
            "affected_device_ids": {"type": "array", "items": {"type": "string"}},
            "steps": {"type": "array", "items": {"type": "string"}},
            "citations": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "incident_id",
            "title",
            "priority",
            "summary",
            "affected_device_ids",
            "steps",
            "citations",
        ],
    },
}

EMAIL_DEFINITION = {
    "type": "function",
    "name": "draft_client_email",
    "description": "Store an email to the client, in plain English for a non-technical reader: what happened, what "
    "it affects, what we are doing. Returns email_id.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "incident_id": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "tone": {"type": "string", "enum": ["calm", "urgent"]},
        },
        "required": ["incident_id", "subject", "body", "tone"],
    },
}


def _store(ctx, prefix: str, kind: str, incident_id: str, payload: dict) -> str:
    count = ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM drafts WHERE run_id = ? AND kind = ?", (ctx.run_id, kind)
    ).fetchone()["n"]
    draft_id = f"{prefix}-{count + 1}"
    ctx.conn.execute(
        "INSERT INTO drafts (run_id, id, kind, incident_id, body) VALUES (?,?,?,?,?)",
        (ctx.run_id, draft_id, kind, incident_id, json.dumps(payload)),
    )
    ctx.drafted = True
    return draft_id


def draft_ticket(ctx, args: dict) -> dict:
    incident_id = str(args["incident_id"])
    ticket_id = _store(ctx, "TKT", "ticket", incident_id, args)
    return {"ticket_id": ticket_id}


def draft_client_email(ctx, args: dict) -> dict:
    incident_id = str(args["incident_id"])
    email_id = _store(ctx, "EML", "email", incident_id, args)
    return {"email_id": email_id}
