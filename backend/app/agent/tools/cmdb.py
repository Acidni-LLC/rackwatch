"""cmdb_lookup: find devices by hostname, IP, serial, device id or rack position."""

from __future__ import annotations

import re

DEFINITION = {
    "type": "function",
    "name": "cmdb_lookup",
    "description": "Find devices in the CMDB by hostname, IP, serial, device id, or rack position such as 'rack A U12'. "
    "Returns rack position and the uplink device. Max 10 results.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}

RACK_U = re.compile(r"(?:rack[\s-]*([A-Za-z0-9]+)\s*)?\bU\s*(\d{1,2})\b", re.I)
FIELDS = "id, hostname, device_type, make, model, serial, rack_id, u_start, u_end, mgmt_ip, uplink_device_id"


def cmdb_lookup(ctx, args: dict) -> dict:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")
    rows = []
    position = RACK_U.search(query)
    if position:
        rack, u = position.group(1), int(position.group(2))
        sql = f"SELECT {FIELDS} FROM devices WHERE ? BETWEEN u_start AND u_end"
        params: list = [u]
        if rack:
            sql += " AND rack_id LIKE ?"
            params.append(f"%{rack}%")
        rows = ctx.conn.execute(sql, params).fetchall()
    if not rows:
        like = f"%{query}%"
        rows = ctx.conn.execute(
            f"SELECT {FIELDS} FROM devices WHERE id = ? OR hostname LIKE ? OR mgmt_ip = ? OR serial LIKE ? "
            "OR model LIKE ? OR device_type = ? ORDER BY u_start DESC LIMIT 10",
            (query, like, query, like, like, query.lower()),
        ).fetchall()
    return {"devices": [dict(r) for r in rows[:10]]}
