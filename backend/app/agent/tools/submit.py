"""submit_incidents: the only route to the verifier. The harness expands selectors and runs the checks."""

from __future__ import annotations

_CLAIM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "text": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
        "kind": {"type": "string", "enum": ["root_cause", "impact", "timeline", "evidence"]},
    },
    "required": ["id", "text", "citations", "kind"],
}

DEFINITION = {
    "type": "function",
    "name": "submit_incidents",
    "description": "Submit your final grouping. A separate verifier checks it and returns the result. alert_ids and "
    "noise_alert_ids may mix explicit ids with selector strings, to save tokens: `type:voip_jitter AND "
    "device_id:dev_A_08`. Selector fields are type, device_id, device_hint, severity, source and message (substring "
    "of the scrubbed text, e.g. `type:backup_failed AND message:no space`).",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "incidents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "root_cause_device_id": {"type": "string"},
                        "root_cause": {"type": "string"},
                        "alert_ids": {"type": "array", "items": {"type": "string"}},
                        "claims": {"type": "array", "items": _CLAIM},
                        "proposed_fix_id": {"type": "string"},
                        "ticket_id": {"type": "string"},
                        "email_id": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": [
                        "id",
                        "title",
                        "root_cause_device_id",
                        "root_cause",
                        "alert_ids",
                        "claims",
                        "proposed_fix_id",
                        "ticket_id",
                        "email_id",
                    ],
                },
            },
            "noise_alert_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["incidents", "noise_alert_ids"],
    },
}


def submit_incidents(ctx, args: dict) -> dict:
    if ctx.on_submit is None:
        raise ValueError("submit_incidents is not available in this context")
    ctx.submitted = True
    return ctx.on_submit(args)
