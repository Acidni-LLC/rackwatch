"""The agent's tools (DESIGN.md §8.3). Results pass through the scrubber on the way back to the model.

Nothing here can reach ground truth or verifier configuration. submit_incidents is the only route to the
verifier, and the harness (app.agent.loop) supplies that callback.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from ...events import emit, now_iso
from ...pii import PiiScrubber
from ...schemas import SimState
from . import alerts, cmdb, drafts, kb, logs, netsim, submit


@dataclass
class ToolContext:
    conn: sqlite3.Connection
    run_id: str
    scrubber: PiiScrubber
    sim: SimState | None = None
    attempt: int = 1
    turn: int = 0
    kb_index: kb.KBIndex | None = None
    on_submit: Callable[[dict], dict] | None = None
    drafted: bool = field(default=False)
    submitted: bool = field(default=False)


REGISTRY = {
    "cmdb_lookup": cmdb.cmdb_lookup,
    "list_alerts": alerts.list_alerts,
    "log_search": logs.log_search,
    "ping_sim": netsim.ping_sim,
    "dns_sim": netsim.dns_sim,
    "kb_search": kb.kb_search,
    "draft_ticket": drafts.draft_ticket,
    "draft_client_email": drafts.draft_client_email,
    "submit_incidents": submit.submit_incidents,
}

DEFINITIONS = [
    cmdb.DEFINITION,
    alerts.DEFINITION,
    logs.DEFINITION,
    netsim.PING_DEFINITION,
    netsim.DNS_DEFINITION,
    kb.DEFINITION,
    drafts.TICKET_DEFINITION,
    drafts.EMAIL_DEFINITION,
    submit.DEFINITION,
]


def run_tool(ctx: ToolContext, name: str, raw_arguments: str | dict) -> dict:
    """Run one tool call and return its scrubbed result. Errors go back to the model as data."""
    try:
        args = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    except json.JSONDecodeError as error:
        args = {}
        result = {"error": f"arguments were not valid JSON ({error}); call {name} again with valid JSON"}
    else:
        tool = REGISTRY.get(name)
        if tool is None:
            result = {"error": f"unknown tool {name}"}
        else:
            try:
                result = tool(ctx, args)
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                result = {
                    "error": f"{name}: {type(error).__name__}: {error}. Check the arguments and call it again."
                }
    result = ctx.scrubber.scrub_obj(result)
    ctx.conn.execute(
        "INSERT INTO tool_calls (run_id, attempt, turn, name, args, result, ts) VALUES (?,?,?,?,?,?,?)",
        (ctx.run_id, ctx.attempt, ctx.turn, name, json.dumps(args), json.dumps(result), now_iso()),
    )
    emit(
        ctx.conn,
        ctx.run_id,
        "tool",
        {"attempt": ctx.attempt, "turn": ctx.turn, "name": name, "args": args, "result": _brief(result)},
    )
    return result


def _brief(result: dict, limit: int = 1200) -> dict | str:
    """Keep SSE events small: the timeline shows a preview and the full result stays in tool_calls."""
    text = json.dumps(result)
    return result if len(text) <= limit else text[:limit] + "…"
