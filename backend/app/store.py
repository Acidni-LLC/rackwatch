"""Loads the generated dataset into SQLite and reads it back as schema models.

Alert messages and log lines are scrubbed here, at ingest, so nothing raw can reach a tool result. The raw
alert stays in the local `alerts.message` and `alerts.raw` columns for the human view and is never sent.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path

import yaml

from .pii import PiiScrubber
from .schemas import Alert, ApprovedFix, Contact, Device, KBArticle, LogLine, SimState

DEVICE_COLUMNS = (
    "id, rack_id, u_start, u_end, device_type, make, model, serial, hostname, mgmt_ip, uplink_device_id, "
    "confidence, field_confidence, bbox, source_image, source, needs_review"
)


def upsert_devices(conn: sqlite3.Connection, devices: Iterable[Device]) -> int:
    count = 0
    for d in devices:
        conn.execute(
            f"INSERT OR REPLACE INTO devices ({DEVICE_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                d.id,
                d.rack_id,
                d.u_start,
                d.u_end,
                d.device_type,
                d.make,
                d.model,
                d.serial,
                d.hostname,
                d.mgmt_ip,
                d.uplink_device_id,
                d.confidence,
                json.dumps(d.field_confidence),
                json.dumps(d.bbox.model_dump()) if d.bbox else None,
                d.source_image,
                d.source,
                int(d.needs_review),
            ),
        )
        count += 1
    return count


def row_to_device(row: sqlite3.Row) -> Device:
    data = dict(row)
    data["field_confidence"] = json.loads(data["field_confidence"] or "{}")
    data["bbox"] = json.loads(data["bbox"]) if data["bbox"] else None
    data["needs_review"] = bool(data["needs_review"])
    return Device(**data)


def all_devices(conn: sqlite3.Connection) -> list[Device]:
    return [row_to_device(r) for r in conn.execute("SELECT * FROM devices ORDER BY u_start DESC")]


def insert_alerts(conn: sqlite3.Connection, alerts: Iterable[Alert], scrubber: PiiScrubber) -> int:
    count = 0
    conn.execute("BEGIN")
    for a in alerts:
        conn.execute(
            "INSERT OR REPLACE INTO alerts (id, ts, source, client_id, device_hint, device_id, severity, type, message, "
            "raw, scrubbed_message) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                a.id,
                a.ts,
                a.source,
                a.client_id,
                a.device_hint,
                a.device_id,
                a.severity,
                a.type,
                a.message,
                json.dumps(a.raw),
                scrubber.scrub(a.message),
            ),
        )
        count += 1
    conn.execute("COMMIT")
    return count


def row_to_alert(row: sqlite3.Row) -> Alert:
    data = dict(row)
    data["raw"] = json.loads(data["raw"] or "{}")
    return Alert(**data)


def all_alerts(conn: sqlite3.Connection) -> list[Alert]:
    return [row_to_alert(r) for r in conn.execute("SELECT * FROM alerts ORDER BY ts, id")]


def insert_logs(conn: sqlite3.Connection, logs: Iterable[LogLine], scrubber: PiiScrubber) -> int:
    count = 0
    conn.execute("BEGIN")
    for log in logs:
        line = scrubber.scrub(log.line)
        conn.execute(
            "INSERT OR REPLACE INTO logs (id, ts, device_id, facility, level, line) VALUES (?,?,?,?,?,?)",
            (log.id, log.ts, log.device_id, log.facility, log.level, line),
        )
        conn.execute("INSERT INTO logs_fts (id, line) VALUES (?, ?)", (log.id, line))
        count += 1
    conn.execute("COMMIT")
    return count


def insert_kb(conn: sqlite3.Connection, articles: Iterable[KBArticle]) -> int:
    count = 0
    for art in articles:
        conn.execute(
            "INSERT OR REPLACE INTO kb_articles (id, title, body, approved_fixes) VALUES (?,?,?,?)",
            (art.id, art.title, art.body, json.dumps([f.model_dump() for f in art.approved_fixes])),
        )
        count += 1
    return count


def all_kb(conn: sqlite3.Connection) -> list[KBArticle]:
    return [
        KBArticle(
            id=r["id"], title=r["title"], body=r["body"], approved_fixes=json.loads(r["approved_fixes"])
        )
        for r in conn.execute("SELECT * FROM kb_articles ORDER BY id")
    ]


FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.S)


def parse_kb_file(path: Path) -> KBArticle:
    """A KB file is Markdown with YAML front matter holding id, title and approved_fixes."""
    text = path.read_text(encoding="utf-8")
    match = FRONT_MATTER.match(text)
    if not match:
        raise ValueError(f"{path}: KB article has no YAML front matter")
    meta = yaml.safe_load(match.group(1)) or {}
    fixes = [ApprovedFix(**f) for f in meta.get("approved_fixes") or []]
    return KBArticle(
        id=meta.get("id") or path.stem, title=meta["title"], body=match.group(2).strip(), approved_fixes=fixes
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_contacts(generated_dir: Path) -> list[Contact]:
    path = Path(generated_dir) / "contacts.json"
    if not path.exists():
        return []
    return [Contact(**c) for c in json.loads(path.read_text(encoding="utf-8"))]


def contact_names(contacts: Iterable[Contact]) -> list[str]:
    """Full names and usernames. A bare first name is too ambiguous for the regex list."""
    names: list[str] = []
    for c in contacts:
        names.append(c.name)
        if c.username:
            names.append(c.username)
    return names


def load_sim_state(generated_dir: Path) -> SimState:
    return SimState(**json.loads((Path(generated_dir) / "sim_state.json").read_text(encoding="utf-8")))


def dataset_loaded(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT 1 FROM alerts LIMIT 1").fetchone())


def load_dataset(conn: sqlite3.Connection, generated_dir: Path, scrubber: PiiScrubber | None = None) -> dict:
    """Reset and reload the operational tables. Ground truth is deliberately not loaded: only the verifier reads it."""
    generated_dir = Path(generated_dir)
    contacts = load_contacts(generated_dir)
    for table in ("alerts", "logs", "logs_fts", "kb_articles", "devices", "pii_map"):
        conn.execute(f"DELETE FROM {table}")
    scrubber = scrubber or PiiScrubber(conn, names=contact_names(contacts))
    seed = json.loads((generated_dir / "cmdb_seed.json").read_text(encoding="utf-8"))
    devices = upsert_devices(conn, [Device(**d) for d in seed])
    alerts = insert_alerts(conn, [Alert(**a) for a in read_jsonl(generated_dir / "alerts.jsonl")], scrubber)
    logs = insert_logs(conn, [LogLine(**x) for x in read_jsonl(generated_dir / "logs.jsonl")], scrubber)
    kb = insert_kb(conn, [parse_kb_file(p) for p in sorted((generated_dir / "kb").glob("*.md"))])
    return {
        "devices": devices,
        "alerts": alerts,
        "logs": logs,
        "kb": kb,
        "pii_scrubbed": scrubber.items_scrubbed,
    }
