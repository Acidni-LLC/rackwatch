"""FastAPI app: REST routes, the SSE run stream, and the built frontend (DESIGN.md §3, §12)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import replace
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import db as dbmod
from . import metrics, rack, store, voice
from .agent.loop import finish_run, new_run_id, run_triage, start_run
from .agent.router import ENGINES
from .budget import BudgetGuard
from .config import DATA_DIR, REPO_ROOT, UPLOADS_DIR, Settings, get_settings
from .events import emit, events_after, now_iso
from .meta_client import MetaClient
from .pii import PiiScrubber
from .replay import cassette_meta, cassette_path

TRIAGE_CASSETTE = "triage_pass_after_replan"
OPEN_CASSETTE = "triage_spark_open"
VOICE_CASSETTE = "voice_note"
VISION_CASSETTE = "vision_sample_rack"
SAMPLE_VOICE = DATA_DIR / "voice" / "sample_note.wav"
VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".m4v", ".avi")

app = FastAPI(title="RackWatch", version="0.1.0")
_local = threading.local()


def settings() -> Settings:
    return get_settings()


def conn() -> sqlite3.Connection:
    """One connection per thread; WAL lets runs and readers overlap."""
    path = settings().db_path
    cached = getattr(_local, "conn", None)
    if cached is None or getattr(_local, "path", None) != path:
        cached = dbmod.connect(path)
        _local.conn, _local.path = cached, path
    return cached


def ensure_dataset() -> None:
    c = conn()
    s = settings()
    if not store.dataset_loaded(c) and (s.generated_dir / "alerts.jsonl").exists():
        store.load_dataset(c, s.generated_dir)


@app.on_event("startup")
def _startup() -> None:
    ensure_dataset()


def available_cassettes(s: Settings) -> list[str]:
    return sorted(p.parent.name for p in Path(s.replay_dir).glob("*/cassette.jsonl"))


# ---- status -----------------------------------------------------------------------------


_key_cache: dict[str, Any] = {"at": 0.0, "value": None}


def key_status(s: Settings) -> dict:
    if not s.api_key:
        return {"state": "none", "models": []}
    if time.time() - _key_cache["at"] < 300 and _key_cache["value"]:
        return _key_cache["value"]
    try:
        client = MetaClient(settings=replace(s, mode="live"), conn=conn())
        models = client.models()
        value = {
            "state": "ok",
            "models": [m for m in models if m in (s.spark_model, s.sam_model, s.asr_model)],
        }
    except Exception as error:
        value = {"state": "error", "models": [], "detail": type(error).__name__}
    _key_cache.update(at=time.time(), value=value)
    return value


@app.get("/api/status")
def status() -> dict:
    s = settings()
    c = conn()
    guard = BudgetGuard(s, c)
    pii = c.execute("SELECT COUNT(*) AS n FROM pii_map").fetchone()["n"]
    return {
        "mode": s.mode,
        "key": key_status(s),
        "budget": {
            "spent": round(guard.total_spent(), 4),
            "total": s.budget_total,
            "per_run": s.budget_per_run,
        },
        "dataset_loaded": store.dataset_loaded(c),
        "alerts": c.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"],
        "devices": c.execute("SELECT COUNT(*) AS n FROM devices").fetchone()["n"],
        "pii_tokens": pii,
        "cassettes": available_cassettes(s),
        "models": {
            "spark": s.spark_model,
            "sam": s.sam_model,
            "asr": s.asr_model,
            "spark_open": s.spark_open_model,
        },
        "open_provider": s.open_provider,
        "open_weight_key": bool(s.open_api_key),
    }


@app.post("/api/reset")
def reset() -> dict:
    """Reload the seeded CMDB and alerts. The call log and spent budget survive a reset on purpose."""
    s = settings()
    c = conn()
    counts = store.load_dataset(c, s.generated_dir)
    for table in ("incidents", "verifier_results", "tool_calls", "retrieved_logs", "drafts", "rack_scans"):
        c.execute(f"DELETE FROM {table}")
    return counts


# ---- rack -> CMDB -----------------------------------------------------------------------


def _client_for_run(s: Settings, c, run_id: str, replay: bool, cassette: str, speed: float) -> MetaClient:
    run_settings = replace(s, mode="replay") if replay else s
    bound = cassette if run_settings.mode in ("replay", "record") else None
    return MetaClient(settings=run_settings, conn=c, run_id=run_id, cassette=bound, replay_speed=speed)


def _ensure_cassette(s: Settings, name: str) -> None:
    if not cassette_path(s.replay_dir, name).exists():
        raise HTTPException(
            409, f"No recording named {name} is available. Add a key to run live, or record one first."
        )


def _thread(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


def _vision_worker(run_id: str, image_path: str, replay: bool, speed: float) -> None:
    c = dbmod.connect(settings().db_path)
    s = settings()
    try:
        client = _client_for_run(s, c, run_id, replay, VISION_CASSETTE, speed)
        rack.scan_and_store(c, client, s, run_id, image_path)
        finish_run(c, run_id, "passed", 1)
    except Exception as error:
        emit(c, run_id, "run_error", {"message": f"{type(error).__name__}: {error}"})
        finish_run(c, run_id, "error", 1)
    finally:
        c.close()


def _sharpest_frame(video: Path) -> str:
    from vision.video import sharpest_frame_path  # noqa: PLC0415 - Muse Code's package, imported at use

    try:
        return sharpest_frame_path(str(video), str(video.with_suffix(".jpg")))
    except Exception as error:
        raise HTTPException(422, f"Could not read frames from that video: {error}") from error


@app.post("/api/rack/scan")
async def rack_scan(
    file: UploadFile | None = File(default=None),
    replay: bool = Form(default=False),
    speed: float = Form(default=1.0),
) -> dict:
    s = settings()
    use_replay = replay or s.mode == "replay"
    image_path = str(rack.SAMPLE_IMAGE)
    if file is not None and file.filename:
        if use_replay:
            raise HTTPException(
                409, "Replay mode can only scan the sample rack. Add a key to scan your own photo."
            )
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        target = UPLOADS_DIR / f"{uuid.uuid4().hex}{Path(file.filename).suffix.lower()}"
        target.write_bytes(await file.read())
        image_path = str(target)
        if target.suffix in VIDEO_SUFFIXES:
            image_path = await asyncio.to_thread(_sharpest_frame, target)
    if use_replay:
        _ensure_cassette(s, VISION_CASSETTE)
    run_id = new_run_id()
    start_run(
        conn(), run_id, "vision", "replay" if use_replay else s.mode, VISION_CASSETTE if use_replay else None
    )
    _thread(_vision_worker, run_id, image_path, use_replay, speed)
    return {"run_id": run_id}


@app.get("/api/rack")
def rack_state() -> dict:
    return rack.current_state(conn())


class DevicePatch(BaseModel):
    make: str | None = None
    model: str | None = None
    serial: str | None = None
    hostname: str | None = None
    mgmt_ip: str | None = None


@app.patch("/api/devices/{device_id}")
def edit_device(device_id: str, patch: DevicePatch) -> dict:
    updated = rack.edit_device(conn(), device_id, patch.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(404, "no such device")
    return updated


@app.get("/files/{path:path}")
def data_file(path: str) -> FileResponse:
    """Images only, and only under data/rack, data/uploads and data/runs."""
    full = (DATA_DIR / path).resolve()
    roots = [(DATA_DIR / d).resolve() for d in ("rack", "uploads", "runs")]
    if not any(full.is_relative_to(r) for r in roots) or full.suffix.lower() not in (
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    ):
        raise HTTPException(404, "not found")
    if not full.exists():
        raise HTTPException(404, "not found")
    return FileResponse(full)


# ---- alerts and logs ---------------------------------------------------------------------


@app.get("/api/alerts")
def alerts(limit: int = Query(default=1000, le=5000), offset: int = 0, view: str = "scrubbed") -> dict:
    """`view=raw` is the local human view, which shows the toggle's before state. It never goes to a model."""
    c = conn()
    total = c.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"]
    rows = c.execute(
        "SELECT id, ts, source, client_id, device_hint, device_id, severity, type, message, scrubbed_message FROM alerts "
        "ORDER BY ts, id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    items = []
    changed = 0
    for r in rows:
        item = dict(r)
        item["had_pii"] = item["message"] != item["scrubbed_message"]
        changed += int(item["had_pii"])
        item["text"] = item["message"] if view == "raw" else item["scrubbed_message"]
        item.pop("message")
        item.pop("scrubbed_message")
        items.append(item)
    tokens = c.execute("SELECT kind, COUNT(*) AS n FROM pii_map GROUP BY kind").fetchall()
    return {
        "total": total,
        "alerts": items,
        "pii": {
            "alerts_with_pii": changed,
            "tokens": sum(t["n"] for t in tokens),
            "by_kind": {t["kind"]: t["n"] for t in tokens},
        },
    }


@app.get("/api/logs/{log_id}")
def log_line(log_id: str, context: int = 3) -> dict:
    c = conn()
    row = c.execute("SELECT * FROM logs WHERE id = ?", (log_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such log line")
    around = c.execute(
        "SELECT * FROM (SELECT * FROM logs WHERE device_id = ? ORDER BY ABS(julianday(ts) - julianday(?)) LIMIT ?) ORDER BY ts",
        (row["device_id"], row["ts"], context * 2 + 1),
    ).fetchall()
    return {"line": dict(row), "context": [dict(r) for r in around]}


# ---- triage ------------------------------------------------------------------------------


class TriageRequest(BaseModel):
    replay: bool = False
    speed: float = 1.0
    demo_strict: bool = False
    engine: str = "hosted"  # hosted | open | mixed; on replay, open picks the Spark Open recording


def _triage_worker(
    run_id: str, replay: bool, speed: float, demo_strict: bool, engine: str, cassette: str
) -> None:
    c = dbmod.connect(settings().db_path)
    s = settings()
    try:
        client = _client_for_run(s, c, run_id, replay, cassette, speed)
        if replay:  # a replay has to run the way it was recorded, or its calls will not line up
            meta = cassette_meta(s.replay_dir, cassette)
            demo_strict = bool(meta.get("strict"))
            engine = meta.get("engine", "hosted")
        run_triage(c, client, s, run_id=run_id, demo_strict=demo_strict, engine=engine)
    except Exception as error:
        emit(c, run_id, "run_error", {"message": f"{type(error).__name__}: {error}"})
        finish_run(c, run_id, "error", 0)
    finally:
        c.close()


@app.post("/api/triage")
def triage(request: TriageRequest) -> dict:
    s = settings()
    if request.engine not in ENGINES:
        raise HTTPException(422, f"engine must be one of {', '.join(ENGINES)}")
    use_replay = request.replay or s.mode == "replay"
    cassette = OPEN_CASSETTE if request.engine == "open" else TRIAGE_CASSETTE
    if use_replay:
        _ensure_cassette(s, cassette)
    elif request.engine != "hosted" and not s.open_api_key:
        raise HTTPException(409, f"Running on Muse Spark Open needs a {s.open_provider} API key in .env.")
    ensure_dataset()
    run_id = new_run_id()
    start_run(conn(), run_id, "triage", "replay" if use_replay else s.mode, cassette if use_replay else None)
    _thread(_triage_worker, run_id, use_replay, request.speed, request.demo_strict, request.engine, cassette)
    return {"run_id": run_id}


@app.get("/api/runs")
def runs(kind: str | None = None, limit: int = 20) -> list[dict]:
    sql = "SELECT * FROM runs" + (" WHERE kind = ?" if kind else "") + " ORDER BY started_at DESC LIMIT ?"
    return [dict(r) for r in conn().execute(sql, ((kind, limit) if kind else (limit,)))]


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict:
    c = conn()
    row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such run")
    verifier = [
        json.loads(r["result"])
        for r in c.execute("SELECT result FROM verifier_results WHERE run_id = ? ORDER BY attempt", (run_id,))
    ]
    return {"run": dict(row), "verifier": verifier, "incidents": _incidents(c, run_id)}


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, after: int = 0) -> EventSourceResponse:
    """Tails the events table. The stream ends after run_finished, and the browser reconnects from `after`."""

    async def stream():
        c = dbmod.connect(settings().db_path)
        seq = after
        try:
            while True:
                batch = events_after(c, run_id, seq)
                for event in batch:
                    seq = event["seq"]
                    yield {"event": event["type"], "id": str(seq), "data": json.dumps(event)}
                    if event["type"] == "run_finished":
                        return
                if not batch:
                    row = c.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
                    if row is None or row["status"] != "running":
                        return
                await asyncio.sleep(0.15)
        finally:
            c.close()

    return EventSourceResponse(stream(), ping=10)


@app.get("/api/calls")
def calls(run_id: str | None = None, limit: int = 500) -> list[dict]:
    sql = "SELECT * FROM calls" + (" WHERE run_id = ?" if run_id else "") + " ORDER BY ts DESC LIMIT ?"
    return [dict(r) for r in conn().execute(sql, ((run_id, limit) if run_id else (limit,)))]


@app.get("/api/tool-calls/{run_id}")
def tool_calls(run_id: str) -> list[dict]:
    rows = conn().execute("SELECT * FROM tool_calls WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()
    return [{**dict(r), "args": json.loads(r["args"]), "result": json.loads(r["result"])} for r in rows]


@app.get("/api/metrics")
def metrics_route(run_id: str | None = None) -> dict:
    return metrics.run_metrics(conn(), run_id, settings().replay_dir)


# ---- incidents and the human gate --------------------------------------------------------


def _row_to_incident(row: sqlite3.Row) -> dict:
    data = dict(row)
    for key in ("alert_ids", "claims", "voice_notes"):
        data[key] = json.loads(data[key] or "[]")
    for key in ("ticket", "client_email"):
        data[key] = json.loads(data[key]) if data[key] else None
    return data


def _incidents(c, run_id: str) -> list[dict]:
    rows = c.execute("SELECT * FROM incidents WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    return [_row_to_incident(r) for r in rows]


def _latest_triage(c) -> str | None:
    row = c.execute(
        "SELECT r.id FROM runs r WHERE r.kind = 'triage' AND EXISTS (SELECT 1 FROM incidents i WHERE i.run_id = r.id) "
        "ORDER BY r.started_at DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


@app.get("/api/incidents")
def incidents(run_id: str | None = None) -> dict:
    c = conn()
    run_id = run_id or _latest_triage(c)
    return {"run_id": run_id, "incidents": _incidents(c, run_id) if run_id else []}


def _incident_or_404(c, run_id: str, incident_id: str) -> sqlite3.Row:
    row = c.execute("SELECT * FROM incidents WHERE run_id = ? AND id = ?", (run_id, incident_id)).fetchone()
    if row is None:
        raise HTTPException(404, "no such incident")
    return row


@app.get("/api/incidents/{run_id}/{incident_id}")
def incident_detail(run_id: str, incident_id: str) -> dict:
    c = conn()
    data = _row_to_incident(_incident_or_404(c, run_id, incident_id))
    cited = sorted({cite for claim in data["claims"] for cite in claim["citations"]})
    if data["ticket"]:
        cited = sorted(set(cited) | set(data["ticket"].get("citations") or []))
    marks = ",".join("?" * len(cited))
    lines = [dict(r) for r in c.execute(f"SELECT * FROM logs WHERE id IN ({marks})", cited)] if cited else []
    device = c.execute("SELECT * FROM devices WHERE id = ?", (data["root_cause_device_id"],)).fetchone()
    return {
        **data,
        "logs": lines,
        "root_cause_device": store.row_to_device(device).model_dump() if device else None,
        "rack": rack.current_state(c),
    }


class DraftPatch(BaseModel):
    ticket: dict | None = None
    client_email: dict | None = None


@app.patch("/api/incidents/{run_id}/{incident_id}")
def edit_incident(run_id: str, incident_id: str, patch: DraftPatch) -> dict:
    c = conn()
    _incident_or_404(c, run_id, incident_id)
    if patch.ticket is not None:
        c.execute(
            "UPDATE incidents SET ticket = ? WHERE run_id = ? AND id = ?",
            (json.dumps(patch.ticket), run_id, incident_id),
        )
    if patch.client_email is not None:
        c.execute(
            "UPDATE incidents SET client_email = ? WHERE run_id = ? AND id = ?",
            (json.dumps(patch.client_email), run_id, incident_id),
        )
    return _row_to_incident(_incident_or_404(c, run_id, incident_id))


@app.post("/api/incidents/{run_id}/{incident_id}/approve")
def approve(run_id: str, incident_id: str) -> dict:
    c = conn()
    row = _incident_or_404(c, run_id, incident_id)
    if row["status"] != "verified":
        raise HTTPException(409, f"Only a verified incident can be approved; this one is {row['status']}.")
    c.execute("UPDATE incidents SET status = 'approved' WHERE run_id = ? AND id = ?", (run_id, incident_id))
    emit(c, run_id, "incident_status", {"incident_id": incident_id, "status": "approved", "ts": now_iso()})
    return _row_to_incident(_incident_or_404(c, run_id, incident_id))


class Escalation(BaseModel):
    reason: str


@app.post("/api/incidents/{run_id}/{incident_id}/escalate")
def escalate(run_id: str, incident_id: str, body: Escalation) -> dict:
    if not body.reason.strip():
        raise HTTPException(422, "Escalating needs a reason.")
    c = conn()
    _incident_or_404(c, run_id, incident_id)
    c.execute(
        "UPDATE incidents SET status = 'escalated', escalation_reason = ? WHERE run_id = ? AND id = ?",
        (body.reason.strip(), run_id, incident_id),
    )
    emit(c, run_id, "incident_status", {"incident_id": incident_id, "status": "escalated", "ts": now_iso()})
    return _row_to_incident(_incident_or_404(c, run_id, incident_id))


def _detokenized(c, s: Settings, text: str) -> str:
    return PiiScrubber(c).detokenize(text)


@app.get("/api/incidents/{run_id}/{incident_id}/export/ticket.json")
def export_ticket(run_id: str, incident_id: str) -> JSONResponse:
    c = conn()
    row = _incident_or_404(c, run_id, incident_id)
    data = _row_to_incident(row)
    if row["status"] not in ("approved", "escalated"):
        raise HTTPException(409, "Approve or escalate the incident before exporting it.")
    body = json.loads(_detokenized(c, settings(), json.dumps(data["ticket"] or {})))
    payload = {
        **body,
        "incident_id": incident_id,
        "status": row["status"],
        "root_cause": data["root_cause"],
        "root_cause_device_id": data["root_cause_device_id"],
        "proposed_fix_id": data["proposed_fix_id"],
        "claims": data["claims"],
        "voice_notes": data["voice_notes"],
        "escalation_reason": data["escalation_reason"],
    }
    return JSONResponse(
        payload, headers={"Content-Disposition": f'attachment; filename="{incident_id}-ticket.json"'}
    )


@app.get("/api/incidents/{run_id}/{incident_id}/export/email.eml")
def export_email(run_id: str, incident_id: str) -> PlainTextResponse:
    c = conn()
    row = _incident_or_404(c, run_id, incident_id)
    if row["status"] != "approved":
        raise HTTPException(409, "Only an approved incident's client email can be exported.")
    email = json.loads(row["client_email"] or "{}")
    message = EmailMessage()
    message["Subject"] = _detokenized(c, settings(), email.get("subject", ""))
    message["To"] = "client-contact@acme-dental.example"
    message["From"] = "noc@msp.example"
    message.set_content(_detokenized(c, settings(), email.get("body", "")))
    return PlainTextResponse(
        message.as_string(),
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="{incident_id}.eml"'},
    )


@app.post("/api/incidents/{run_id}/{incident_id}/voice")
async def voice_note(
    run_id: str,
    incident_id: str,
    file: UploadFile | None = File(default=None),
    replay: bool = Form(default=False),
    sample: bool = Form(default=False),
) -> dict:
    s = settings()
    c = conn()
    _incident_or_404(c, run_id, incident_id)
    use_replay = replay or s.mode == "replay"
    if use_replay:
        _ensure_cassette(s, VOICE_CASSETTE)
    audio = await file.read() if file is not None else b""
    if not audio and (sample or use_replay) and SAMPLE_VOICE.exists():
        audio = SAMPLE_VOICE.read_bytes()
    if not audio:
        raise HTTPException(422, "No audio received.")
    call_run = f"voice_{uuid.uuid4().hex[:8]}"
    start_run(
        c, call_run, "voice", "replay" if use_replay else s.mode, VOICE_CASSETTE if use_replay else None
    )
    client = _client_for_run(s, c, call_run, use_replay, VOICE_CASSETTE, 1.0)
    try:
        note = await asyncio.to_thread(_voice_thread, client, s, run_id, incident_id, audio)
    except voice.TranscodeError as error:
        finish_run(c, call_run, "error", 1)
        raise HTTPException(422, str(error)) from error
    finish_run(c, call_run, "passed", 1)
    return note


def _voice_thread(client: MetaClient, s: Settings, run_id: str, incident_id: str, audio: bytes) -> dict:
    c = dbmod.connect(s.db_path)
    client.conn = c
    client.budget.conn = c
    try:
        return voice.add_voice_note(c, client, s, run_id, incident_id, audio)
    finally:
        c.close()


# ---- the built frontend ------------------------------------------------------------------

DIST = REPO_ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith(("api/", "files/")):
            raise HTTPException(404, "not found")
        candidate = (DIST / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(DIST):
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
