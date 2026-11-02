"""Expands alert selectors such as `type:voip_jitter AND device_id:dev_A_08` deterministically, before verifying."""

from __future__ import annotations

import re
import sqlite3

COLUMNS = {
    "type": "type",
    "device_id": "device_id",
    "device_hint": "device_hint",
    "severity": "severity",
    "source": "source",
}
PART = re.compile(r"^\s*(\w+)\s*:\s*(.+?)\s*$", re.S)


def is_selector(item: str) -> bool:
    return ":" in item


def expand_selector(conn: sqlite3.Connection, selector: str) -> tuple[list[str], str | None]:
    where, params = [], []
    for part in re.split(r"\s+AND\s+", selector, flags=re.I):
        match = PART.match(part)
        if not match:
            return [], f"cannot parse selector {selector!r}"
        field, value = match.group(1).lower(), match.group(2).strip().strip("'\"")
        if field == "message":
            where.append("scrubbed_message LIKE ?")
            params.append(f"%{value}%")
        elif field in COLUMNS:
            where.append(f"{COLUMNS[field]} = ?")
            params.append(value)
        else:
            return [], f"unknown selector field {field!r} in {selector!r}"
    rows = conn.execute(f"SELECT id FROM alerts WHERE {' AND '.join(where)} ORDER BY id", params).fetchall()
    return [r["id"] for r in rows], None


def expand_items(conn: sqlite3.Connection, items: list[str]) -> tuple[list[str], list[str]]:
    """Returns (alert ids in order without per-list duplicates, selector errors)."""
    ids: list[str] = []
    errors: list[str] = []
    for item in items:
        if is_selector(item):
            expanded, error = expand_selector(conn, item)
            if error:
                errors.append(error)
            elif not expanded:
                errors.append(f"selector matched nothing: {item!r}")
            ids.extend(expanded)
        else:
            ids.append(item)
    return list(dict.fromkeys(ids)), errors
