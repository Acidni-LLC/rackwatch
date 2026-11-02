"""list_alerts: page through the batch. Only scrubbed messages are ever returned."""

from __future__ import annotations

DEFINITION = {
    "type": "function",
    "name": "list_alerts",
    "description": "Page through alerts, optionally filtered. Returns the total and scrubbed messages.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "filter": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"type": "string"},
                    "device_id": {"type": "string"},
                    "device_hint": {"type": "string"},
                    "from_ts": {"type": "string"},
                    "to_ts": {"type": "string"},
                    "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                },
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "offset": {"type": "integer", "minimum": 0},
        },
        "required": ["limit", "offset"],
    },
}


def list_alerts(ctx, args: dict) -> dict:
    flt = args.get("filter") or {}
    where, params = ["1=1"], []
    for column in ("type", "device_id", "device_hint", "severity"):
        if flt.get(column):
            where.append(f"{column} = ?")
            params.append(flt[column])
    if flt.get("from_ts"):
        where.append("ts >= ?")
        params.append(flt["from_ts"])
    if flt.get("to_ts"):
        where.append("ts <= ?")
        params.append(flt["to_ts"])
    clause = " AND ".join(where)
    total = ctx.conn.execute(f"SELECT COUNT(*) AS n FROM alerts WHERE {clause}", params).fetchone()["n"]
    limit = max(1, min(int(args.get("limit") or 50), 100))
    offset = max(0, int(args.get("offset") or 0))
    rows = ctx.conn.execute(
        f"SELECT id, ts, type, device_hint, device_id, severity, scrubbed_message FROM alerts WHERE {clause} "
        "ORDER BY ts, id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return {"total": total, "alerts": [dict(r) for r in rows]}
