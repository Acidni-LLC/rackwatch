"""The metrics card (DESIGN.md §12 screen 5). Numbers come from the call log and the verifier, never from a constant."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .replay import cassette_meta


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    start_at = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_at = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return round((end_at - start_at).total_seconds(), 1)


def run_metrics(conn: sqlite3.Connection, run_id: str | None = None, replay_dir: Path | None = None) -> dict:
    if run_id is None:
        row = conn.execute(
            "SELECT id FROM runs WHERE kind = 'triage' AND status != 'running' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        run_id = row["id"] if row else None
    alerts_total = conn.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"]
    pii = conn.execute("SELECT COUNT(*) AS n FROM alerts WHERE message != scrubbed_message").fetchone()["n"]
    tokens = conn.execute("SELECT COUNT(*) AS n FROM pii_map").fetchone()["n"]
    base = {"run_id": run_id, "alerts": alerts_total, "pii_alerts": pii, "pii_tokens": tokens}
    if run_id is None:
        return {**base, "ready": False}

    run = dict(conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
    incidents = conn.execute("SELECT id FROM incidents WHERE run_id = ?", (run_id,)).fetchall()
    verifier = conn.execute(
        "SELECT result FROM verifier_results WHERE run_id = ? ORDER BY attempt DESC LIMIT 1", (run_id,)
    ).fetchone()
    grouping = json.loads(verifier["result"])["grouping"] if verifier else None
    by_model = [
        dict(r)
        for r in conn.execute(
            "SELECT model, COUNT(*) AS calls, SUM(tokens_in) AS tokens_in, SUM(tokens_cached) AS tokens_cached, SUM(tokens_out) AS tokens_out, "
            "SUM(est_cost_usd) AS cost_usd, SUM(latency_ms) AS latency_ms FROM calls WHERE run_id = ? GROUP BY model ORDER BY calls DESC",
            (run_id,),
        )
    ]
    by_purpose = [
        dict(r)
        for r in conn.execute(
            "SELECT purpose, effort, COUNT(*) AS calls, SUM(est_cost_usd) AS cost_usd FROM calls WHERE run_id = ? "
            "GROUP BY purpose, effort ORDER BY calls DESC",
            (run_id,),
        )
    ]
    # A replay spends nothing and plays back faster, so the card reports what the recorded run actually cost and took.
    seconds = _seconds(run["started_at"], run["finished_at"])
    if run["mode"] == "replay" and run["cassette"] and replay_dir is not None:
        seconds = cassette_meta(replay_dir, run["cassette"]).get("seconds", seconds)
    cost = sum(m["cost_usd"] or 0 for m in by_model)
    count = len(incidents)
    return {
        **base,
        "ready": True,
        "status": run["status"],
        "mode": run["mode"],
        "attempts": run["attempts"],
        "incidents": count,
        "grouping": grouping,
        "cost_usd": round(cost, 4),
        "cost_per_incident": round(cost / count, 4) if count else None,
        "seconds": seconds,
        "tokens_in": sum(m["tokens_in"] or 0 for m in by_model),
        "tokens_cached": sum(m["tokens_cached"] or 0 for m in by_model),
        "tokens_out": sum(m["tokens_out"] or 0 for m in by_model),
        "by_model": by_model,
        "by_purpose": by_purpose,
    }
