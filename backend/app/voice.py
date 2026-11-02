"""Voice notes (DESIGN.md §8.4): browser audio, then WAV 24 kHz mono, then Muse Voice Transcribe, then the ticket."""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
import wave
from pathlib import Path

from .config import Settings
from .events import emit, now_iso
from .meta_client import MetaClient, ReplayTransport
from .pii import PiiScrubber
from .store import all_devices, contact_names, load_contacts

WORK_LOG_PROMPT = (
    "Turn this technician's voice note into one short ticket work-log line, past tense, no preamble. "
    "Keep device names and rack positions exactly as given.\n\nNote: "
)


class TranscodeError(RuntimeError):
    pass


def ffmpeg_path(settings: Settings) -> str | None:
    found = shutil.which(settings.ffmpeg)
    if found:
        return found
    try:  # dev installs may have the bundled binary instead of a system ffmpeg
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


def is_good_wav(audio: bytes) -> bool:
    try:
        with wave.open(io.BytesIO(audio), "rb") as w:
            return w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() in (16000, 24000)
    except (wave.Error, EOFError):
        return False


def to_wav(audio: bytes, settings: Settings) -> bytes:
    """ASR needs mono 16-bit WAV at 16 or 24 kHz, and browsers record webm, so ffmpeg transcodes."""
    if is_good_wav(audio):
        return audio
    ffmpeg = ffmpeg_path(settings)
    if ffmpeg is None:
        raise TranscodeError("ffmpeg is not installed; install it or set FFMPEG_PATH")
    with tempfile.TemporaryDirectory() as tmp:
        source, target = Path(tmp) / "note.input", Path(tmp) / "note.wav"
        source.write_bytes(audio)
        result = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-ac",
                "1",
                "-ar",
                "24000",
                "-sample_fmt",
                "s16",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise TranscodeError(f"ffmpeg failed: {result.stderr[:300]}")
        return target.read_bytes()


def keywords_for(conn: sqlite3.Connection, incident: sqlite3.Row) -> list[str]:
    devices = {d.id: d for d in all_devices(conn)}
    alert_ids = json.loads(incident["alert_ids"])
    related = {incident["root_cause_device_id"]}
    if alert_ids:
        marks = ",".join("?" * len(alert_ids))
        related |= {
            r["device_id"]
            for r in conn.execute(f"SELECT DISTINCT device_id FROM alerts WHERE id IN ({marks})", alert_ids)
        }
    keywords = ["rack"]
    for device_id in related:
        device = devices.get(device_id or "")
        if device and device.hostname:
            keywords.append(device.hostname)
    root = devices.get(incident["root_cause_device_id"] or "")
    if root:
        keywords.append(f"U{root.u_start}")
    return sorted(set(keywords))


def add_voice_note(
    conn: sqlite3.Connection,
    client: MetaClient,
    settings: Settings,
    run_id: str,
    incident_id: str,
    audio: bytes,
    work_log: bool = True,
) -> dict:
    incident = conn.execute(
        "SELECT * FROM incidents WHERE run_id = ? AND id = ?", (run_id, incident_id)
    ).fetchone()
    if incident is None:
        raise KeyError(incident_id)
    scrubber = PiiScrubber(conn, names=contact_names(load_contacts(settings.generated_dir)))
    # A replay serves the recorded transcript and never reads the audio, so it must not need ffmpeg.
    wav = audio if isinstance(client.transport, ReplayTransport) else to_wav(audio, settings)
    result = client.transcribe(purpose="voice_note", audio=wav, keywords=keywords_for(conn, incident))
    transcript = scrubber.scrub(str(result.get("transcript", "")))
    line = None
    if work_log and transcript:
        response = client.responses(
            purpose="voice_work_log",
            model=settings.spark_model,
            input=WORK_LOG_PROMPT + transcript,
            effort="low",
            max_output_tokens=600,
            store=False,
        )
        line = scrubber.scrub(response.output_text.strip()) or None
    note = {
        "id": f"VN-{uuid.uuid4().hex[:8]}",
        "ts": now_iso(),
        "transcript": transcript,
        "duration_ms": result.get("audioDurationMs"),
        "call_id": client.call_ids[0] if client.call_ids else None,
        "work_log_line": line,
    }
    notes = json.loads(incident["voice_notes"] or "[]") + [note]
    ticket = json.loads(incident["ticket"]) if incident["ticket"] else None
    if ticket is not None:
        ticket["work_log"] = [*ticket.get("work_log", []), line or transcript]
    conn.execute(
        "UPDATE incidents SET voice_notes = ?, ticket = ? WHERE run_id = ? AND id = ?",
        (json.dumps(notes), json.dumps(ticket) if ticket else None, run_id, incident_id),
    )
    emit(conn, run_id, "voice_note", {"incident_id": incident_id, **note})
    return note
