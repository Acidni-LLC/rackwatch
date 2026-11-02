"""Cassette recording and playback (DESIGN.md §13).

A cassette line is {seq, model, endpoint, purpose, request_hash, request_summary, response_json, usage,
latency_ms}. Encrypted reasoning is stripped on write: it is opaque, large and useless to a replay.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


class CassetteExhausted(RuntimeError):
    """Replay asked for a (model, purpose) the cassette has no entries left for."""


class ReplayUnavailable(RuntimeError):
    """Replay was requested but the named cassette does not exist."""


def normalize_request(request: dict) -> dict:
    """The stable, scrubbed view of a request: no reasoning items, and image bytes reduced to a hash."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(v) for v in node if not (isinstance(v, dict) and v.get("type") == "reasoning")]
        if isinstance(node, dict):
            if node.get("type") == "input_image":
                url = str(node.get("image_url", ""))
                return {"type": "input_image", "sha256": hashlib.sha256(url.encode()).hexdigest()}
            return {k: walk(v) for k, v in node.items() if k not in ("id", "status", "encrypted_content")}
        return node

    keep = ("model", "instructions", "input", "tools", "text")
    return {k: walk(request[k]) for k in keep if k in request}


def request_hash(request: dict) -> str:
    return hashlib.sha256(
        json.dumps(normalize_request(request), sort_keys=True, default=str).encode()
    ).hexdigest()


def request_summary(request: dict, limit: int = 300) -> str:
    return json.dumps(normalize_request(request), default=str)[:limit]


def strip_encrypted(response_json: dict) -> dict:
    cleaned = copy.deepcopy(response_json)
    for item in cleaned.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "reasoning":
            item.pop("encrypted_content", None)
    return cleaned


def cassette_path(replay_dir: Path, name: str) -> Path:
    return Path(replay_dir) / name / "cassette.jsonl"


class CassetteWriter:
    """Writes one cassette. Starting a recording replaces any earlier take of the same name."""

    def __init__(self, replay_dir: Path, name: str) -> None:
        self.path = cassette_path(replay_dir, name)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self.seq = 0

    def append(self, entry: dict) -> None:
        self.seq += 1
        entry = dict(entry, seq=self.seq)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def write_meta(self, meta: dict) -> None:
        (self.path.parent / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


class CassettePlayer:
    """Serves entries per (model, purpose) in order. A hash mismatch still serves the entry, flagged diverged."""

    def __init__(self, replay_dir: Path, name: str) -> None:
        self.path = cassette_path(replay_dir, name)
        if not self.path.exists():
            raise ReplayUnavailable(f"no cassette named {name!r} at {self.path}")
        self.queues: dict[tuple[str, str], list[dict]] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                self.queues.setdefault((entry["model"], entry["purpose"]), []).append(entry)

    def next(self, model: str, purpose: str, req_hash: str) -> tuple[dict, bool]:
        queue = self.queues.get((model, purpose))
        if not queue:
            raise CassetteExhausted(
                f"cassette {self.path.parent.name} has no entry left for {model}/{purpose}"
            )
        entry = queue.pop(0)
        return entry, entry.get("request_hash") != req_hash

    def remaining(self) -> int:
        return sum(len(q) for q in self.queues.values())
