"""The first user message: a digest of the alert batch, not the alerts (DESIGN.md §8.3).

Counts by type, device and five-minute bucket, plus the top distinct messages. The full data stays behind tools,
which keeps the prompt near 8-15K tokens however large the batch is.
"""

from __future__ import annotations

import sqlite3
from collections import Counter


def build_digest(conn: sqlite3.Connection, top_messages: int = 50) -> str:
    rows = conn.execute(
        "SELECT ts, type, device_hint, device_id, severity, client_id, scrubbed_message FROM alerts ORDER BY ts, id"
    ).fetchall()
    if not rows:
        return "ALERT BATCH: 0 alerts."
    by_type = Counter(r["type"] for r in rows)
    by_device = Counter(r["device_id"] or r["device_hint"] for r in rows)
    by_severity = Counter(r["severity"] for r in rows)
    by_bucket: Counter[str] = Counter()
    for r in rows:
        ts = r["ts"]
        minute = int(ts[14:16]) // 5 * 5
        by_bucket[f"{ts[11:13]}:{minute:02d}"] += 1
    by_type_device = Counter((r["type"], r["device_id"] or r["device_hint"]) for r in rows)
    messages = Counter(r["scrubbed_message"] for r in rows)

    lines = [
        f"ALERT BATCH: {len(rows)} alerts for client {rows[0]['client_id']}, {rows[0]['ts']} to {rows[-1]['ts']}.",
        "Severity: " + ", ".join(f"{k}={v}" for k, v in by_severity.most_common()),
        "",
        "By type:",
        *(f"  {k}: {v}" for k, v in by_type.most_common()),
        "",
        "By device:",
        *(f"  {k}: {v}" for k, v in by_device.most_common()),
        "",
        "By type and device:",
        *(f"  {t} @ {d}: {v}" for (t, d), v in by_type_device.most_common()),
        "",
        "By five-minute bucket:",
        *(f"  {k}: {by_bucket[k]}" for k in sorted(by_bucket)),
        "",
        f"Top {top_messages} distinct messages (count, scrubbed text):",
        *(f"  {v}x {k}" for k, v in messages.most_common(top_messages)),
        "",
        "Investigate with the tools, then call submit_incidents.",
    ]
    return "\n".join(lines)
