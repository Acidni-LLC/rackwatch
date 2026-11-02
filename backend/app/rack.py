"""Rack scan orchestration: run the vision pipeline, merge its output into the CMDB, remember the scan.

The vision package is imported only when a scan runs, so the API works without it (for example in tests).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .config import DATA_DIR, RUNS_DIR, Settings
from .events import emit, now_iso
from .meta_client import MetaClient
from .schemas import Device, RackScanResult
from .store import all_devices, row_to_device, upsert_devices

SAMPLE_IMAGE = DATA_DIR / "rack" / "sample_rack.jpg"
RACK_ID = "RACK-A"


def _seed_match(conn: sqlite3.Connection, device: Device) -> sqlite3.Row | None:
    """The seed row for a scanned device: by id, then by the name or serial on its label."""
    row = conn.execute("SELECT * FROM devices WHERE id = ?", (device.id,)).fetchone()
    if row is not None:
        return row
    for column, value in (("hostname", device.hostname), ("serial", device.serial)):
        if value:
            row = conn.execute(f"SELECT * FROM devices WHERE {column} = ?", (value,)).fetchone()
            if row is not None:
                return row
    return None


def merge_scan(conn: sqlite3.Connection, scan: RackScanResult) -> list[Device]:
    """Vision supplies what it saw; the seed keeps what a camera cannot see (management IP and uplink).

    A scanned device matched to a seed row by label but placed at a different U keeps the seed's id and position
    and is flagged for review, so a misread rail never forks the CMDB. A device with no match is added as it stands.
    """
    merged: dict[str, Device] = {}
    for device in scan.devices:
        row = _seed_match(conn, device)
        if row is None:
            merged[device.id] = device
            continue
        seed = row_to_device(row)
        moved = seed.id != device.id or (seed.u_start, seed.u_end) != (device.u_start, device.u_end)
        merged[seed.id] = device.model_copy(
            update={
                "id": seed.id,
                "u_start": seed.u_start,
                "u_end": seed.u_end,
                "make": device.make or seed.make,
                "model": device.model or seed.model,
                "serial": device.serial or seed.serial,
                "hostname": device.hostname or seed.hostname,
                "mgmt_ip": device.mgmt_ip or seed.mgmt_ip,
                "uplink_device_id": device.uplink_device_id or seed.uplink_device_id,
                "needs_review": device.needs_review or moved,
            }
        )
    upsert_devices(conn, merged.values())
    return list(merged.values())


def scan_and_store(
    conn: sqlite3.Connection, client: MetaClient, settings: Settings, run_id: str, image_path: str
) -> dict:
    from vision.pipeline import run_rack_pipeline  # noqa: PLC0415 - Muse Code's package, imported at use

    emit(conn, run_id, "scan_started", {"image_path": _public_path(image_path)})
    seed_path = settings.generated_dir / "cmdb_seed.json"
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    scan = run_rack_pipeline(
        image_path,
        client,
        RACK_ID,
        str(seed_path) if seed_path.exists() else None,
        overlay_path=str(RUNS_DIR / f"{run_id}_overlay.png"),
    )
    merged = merge_scan(conn, scan)
    overlay = scan.overlay_path or ""
    conn.execute(
        "INSERT OR REPLACE INTO rack_scans (run_id, rack_id, image_path, image_w, image_h, total_u, overlay_path, accuracy, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            scan.rack_id,
            scan.image_path,
            scan.image_w,
            scan.image_h,
            scan.total_u,
            overlay,
            json.dumps(scan.accuracy),
            now_iso(),
        ),
    )
    state = current_state(conn)
    emit(conn, run_id, "scan_finished", {"devices": len(merged), "accuracy": scan.accuracy})
    return state


def _public_path(path: str | None) -> str | None:
    """Turn a file under data/ into its /files URL. Anything else has no URL."""
    if not path:
        return None
    try:
        relative = Path(path).resolve().relative_to(DATA_DIR.resolve())
    except ValueError:
        try:
            relative = (RUNS_DIR / Path(path).name).relative_to(DATA_DIR)
        except ValueError:
            return None
    return "/files/" + relative.as_posix()


def current_state(conn: sqlite3.Connection) -> dict:
    """The CMDB as the UI shows it: the seeded rack until a scan has happened."""
    scan = conn.execute("SELECT * FROM rack_scans ORDER BY created_at DESC LIMIT 1").fetchone()
    devices = [d.model_dump() for d in all_devices(conn)]
    if scan is None:
        return {
            "scanned": False,
            "devices": devices,
            "image_url": "/files/rack/sample_rack.jpg" if SAMPLE_IMAGE.exists() else None,
            "image_w": None,
            "image_h": None,
            "total_u": 42,
            "overlay_url": None,
            "accuracy": None,
            "run_id": None,
        }
    return {
        "scanned": True,
        "devices": devices,
        "image_url": _public_path(scan["image_path"]),
        "image_w": scan["image_w"],
        "image_h": scan["image_h"],
        "total_u": scan["total_u"],
        "overlay_url": _public_path(scan["overlay_path"]),
        "accuracy": json.loads(scan["accuracy"]) if scan["accuracy"] else None,
        "run_id": scan["run_id"],
    }
