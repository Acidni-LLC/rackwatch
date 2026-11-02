"""The run event stream. Events persist in SQLite and the SSE endpoint tails the table, so any thread can emit."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def emit(conn: sqlite3.Connection | None, run_id: str | None, event_type: str, data: dict) -> None:
    if conn is None or not run_id:
        return
    conn.execute(
        "INSERT INTO events (run_id, ts, type, data) VALUES (?, ?, ?, ?)",
        (run_id, now_iso(), event_type, json.dumps(data, default=str)),
    )


def events_after(conn: sqlite3.Connection, run_id: str, seq: int, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        "SELECT seq, ts, type, data FROM events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
        (run_id, seq, limit),
    ).fetchall()
    return [{"seq": r["seq"], "ts": r["ts"], "type": r["type"], "data": json.loads(r["data"])} for r in rows]
