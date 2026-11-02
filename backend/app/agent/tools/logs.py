"""log_search: full-text search over scrubbed log lines. Records what the agent saw, for verifier rule R2."""

from __future__ import annotations

import re

DEFINITION = {
    "type": "function",
    "name": "log_search",
    "description": "Search device logs by keywords, optionally by device and time. Only lines returned here may be "
    "cited. An empty query returns lines matching the other filters.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string"},
            "device_id": {"type": "string"},
            "from_ts": {"type": "string"},
            "to_ts": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["query", "limit"],
    },
}

WORD = re.compile(r"[A-Za-z0-9_]+")


def fts_query(query: str) -> str:
    """Quote each word so FTS5 syntax in user text cannot break the query. Any word may match; rank decides."""
    return " OR ".join(f'"{w}"' for w in WORD.findall(query))


def log_search(ctx, args: dict) -> dict:
    query = fts_query(str(args.get("query", "")))
    limit = max(1, min(int(args.get("limit") or 20), 50))
    where, params = [], []
    for column, op, key in (
        ("l.device_id", "=", "device_id"),
        ("l.ts", ">=", "from_ts"),
        ("l.ts", "<=", "to_ts"),
    ):
        if args.get(key):
            where.append(f"{column} {op} ?")
            params.append(args[key])
    filters = (" AND " + " AND ".join(where)) if where else ""
    if query:
        rows = ctx.conn.execute(
            "SELECT l.id, l.ts, l.device_id, l.level, l.line FROM logs_fts f JOIN logs l ON l.id = f.id "
            f"WHERE logs_fts MATCH ?{filters} ORDER BY bm25(logs_fts), l.ts LIMIT ?",
            [query, *params, limit],
        ).fetchall()
    else:
        rows = ctx.conn.execute(
            f"SELECT l.id, l.ts, l.device_id, l.level, l.line FROM logs l WHERE 1=1{filters} ORDER BY l.ts LIMIT ?",
            [*params, limit],
        ).fetchall()
    lines = [dict(r) for r in rows]
    ctx.conn.executemany(
        "INSERT OR IGNORE INTO retrieved_logs (run_id, log_id) VALUES (?, ?)",
        [(ctx.run_id, x["id"]) for x in lines],
    )
    return {"lines": lines}
