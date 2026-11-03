"""SQLite DDL and helpers (DESIGN.md §7). JSON columns hold validated schemas.py models."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY, rack_id TEXT NOT NULL, u_start INTEGER NOT NULL, u_end INTEGER NOT NULL,
  device_type TEXT NOT NULL, make TEXT, model TEXT, serial TEXT, hostname TEXT, mgmt_ip TEXT,
  uplink_device_id TEXT, confidence REAL NOT NULL DEFAULT 1.0, field_confidence TEXT NOT NULL DEFAULT '{}',
  bbox TEXT, source_image TEXT, source TEXT NOT NULL DEFAULT 'seed', needs_review INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, source TEXT NOT NULL, client_id TEXT NOT NULL, device_hint TEXT NOT NULL,
  device_id TEXT, severity TEXT NOT NULL, type TEXT NOT NULL, message TEXT NOT NULL, raw TEXT NOT NULL DEFAULT '{}',
  scrubbed_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(type);
CREATE INDEX IF NOT EXISTS idx_alerts_device ON alerts(device_id);
CREATE TABLE IF NOT EXISTS logs (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, device_id TEXT NOT NULL, facility TEXT NOT NULL, level TEXT NOT NULL,
  line TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS logs_fts USING fts5(id UNINDEXED, line);
CREATE TABLE IF NOT EXISTS kb_articles (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, approved_fixes TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS pii_map (
  token TEXT PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, mode TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0, tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'running', cassette TEXT
);
CREATE TABLE IF NOT EXISTS calls (
  id TEXT PRIMARY KEY, run_id TEXT, ts TEXT NOT NULL, model TEXT NOT NULL, endpoint TEXT NOT NULL,
  purpose TEXT NOT NULL, effort TEXT, latency_ms INTEGER NOT NULL DEFAULT 0, tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_cached INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
  est_cost_usd REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'ok',
  request_digest TEXT NOT NULL DEFAULT '', response_excerpt TEXT NOT NULL DEFAULT '',
  replayed INTEGER NOT NULL DEFAULT 0, diverged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_calls_run ON calls(run_id);
CREATE TABLE IF NOT EXISTS incidents (
  id TEXT NOT NULL, run_id TEXT NOT NULL, title TEXT NOT NULL, root_cause_device_id TEXT, root_cause TEXT NOT NULL,
  alert_ids TEXT NOT NULL, claims TEXT NOT NULL, proposed_fix_id TEXT NOT NULL, ticket TEXT, client_email TEXT,
  status TEXT NOT NULL DEFAULT 'draft', voice_notes TEXT NOT NULL DEFAULT '[]', confidence REAL,
  escalation_reason TEXT, created_at TEXT,
  PRIMARY KEY (run_id, id)
);
CREATE TABLE IF NOT EXISTS tool_calls (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, attempt INTEGER NOT NULL, turn INTEGER NOT NULL,
  name TEXT NOT NULL, args TEXT NOT NULL, result TEXT NOT NULL, ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run ON tool_calls(run_id);
CREATE TABLE IF NOT EXISTS retrieved_logs (
  run_id TEXT NOT NULL, log_id TEXT NOT NULL, PRIMARY KEY (run_id, log_id)
);
CREATE TABLE IF NOT EXISTS verifier_results (
  run_id TEXT NOT NULL, attempt INTEGER NOT NULL, result TEXT NOT NULL, PRIMARY KEY (run_id, attempt)
);
CREATE TABLE IF NOT EXISTS drafts (
  run_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL, incident_id TEXT NOT NULL, body TEXT NOT NULL,
  PRIMARY KEY (run_id, id)
);
CREATE TABLE IF NOT EXISTS rack_scans (
  run_id TEXT PRIMARY KEY, rack_id TEXT NOT NULL, image_path TEXT NOT NULL, image_w INTEGER, image_h INTEGER,
  total_u INTEGER, overlay_path TEXT, accuracy TEXT, devices TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ts TEXT NOT NULL, type TEXT NOT NULL,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
"""


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(DDL)
    return conn
