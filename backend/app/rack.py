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


def merge_scan(conn: sqlite3.Connection, scan: RackScanResult, seeded_rack: bool) -> list[Device]:
    """Keep what vision read for the UI, and keep the agent's CMDB stable (DESIGN.md §8.2).

    On the seeded rack the CMDB devices are the ground-truth devices, so vision output is stored and diffed
    rather than written over them: a seeded row only gains its box and confidence. Scanned devices that match
    a seed row by label take its id, so the photo, the table and the agent all agree on identity. On any other
    photo, unmatched devices are new hardware and join the CMDB as they stand.
    """
    scanned: list[Device] = []
    for device in scan.devices:
        row = _seed_match(conn, device)
        if row is None:
            scanned.append(device)
            if not seeded_rack:
                upsert_devices(conn, [device])
            continue
        seed = row_to_device(row)
        moved = (seed.u_start, seed.u_end) != (device.u_start, device.u_end)
        scanned.append(
            device.model_copy(
                update={
                    "id": seed.id,
                    "mgmt_ip": seed.mgmt_ip,
                    "uplink_device_id": seed.uplink_device_id,
                    "needs_review": device.needs_review or moved,
                }
            )
        )
        conn.execute(
            "UPDATE devices SET bbox = ?, confidence = ?, field_confidence = ?, source_image = ?, source = 'vision' WHERE id = ?",
            (
                json.dumps(device.bbox.model_dump()) if device.bbox else None,
                device.confidence,
                json.dumps(device.field_confidence),
                device.source_image,
                seed.id,
            ),
        )
    return scanned


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
    scanned = merge_scan(conn, scan, seeded_rack=Path(image_path).resolve() == SAMPLE_IMAGE.resolve())
    conn.execute(
        "INSERT OR REPLACE INTO rack_scans (run_id, rack_id, image_path, image_w, image_h, total_u, overlay_path, accuracy, devices, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            scan.rack_id,
            scan.image_path,
            scan.image_w,
            scan.image_h,
            scan.total_u,
            scan.overlay_path or "",
            json.dumps(scan.accuracy),
            json.dumps([d.model_dump() for d in scanned]),
            now_iso(),
        ),
    )
    state = current_state(conn)
    emit(conn, run_id, "scan_finished", {"devices": len(scanned), "accuracy": scan.accuracy})
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
    """`devices` is what the table shows: the latest scan's readings, or the seed. `cmdb` is what the agent uses."""
    scan = conn.execute("SELECT * FROM rack_scans ORDER BY created_at DESC LIMIT 1").fetchone()
    cmdb = [d.model_dump() for d in all_devices(conn)]
    if scan is None:
        return {
            "scanned": False,
            "devices": cmdb,
            "cmdb": cmdb,
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
        "devices": sorted(json.loads(scan["devices"]), key=lambda d: -d["u_start"]),
        "cmdb": cmdb,
        "image_url": _public_path(scan["image_path"]),
        "image_w": scan["image_w"],
        "image_h": scan["image_h"],
        "total_u": scan["total_u"],
        "overlay_url": _public_path(scan["overlay_path"]),
        "accuracy": json.loads(scan["accuracy"]) if scan["accuracy"] else None,
        "run_id": scan["run_id"],
    }


def edit_device(conn: sqlite3.Connection, device_id: str, fields: dict) -> dict | None:
    """A person's correction wins everywhere: in the CMDB and in the scan the table is showing."""
    row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    scan = conn.execute("SELECT run_id, devices FROM rack_scans ORDER BY created_at DESC LIMIT 1").fetchone()
    updated = None
    if row is not None and fields:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE devices SET {assignments}, source = 'manual', needs_review = 0 WHERE id = ?",
            [*fields.values(), device_id],
        )
    if row is not None:
        updated = row_to_device(
            conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        ).model_dump()
    if scan is not None:
        rows = json.loads(scan["devices"])
        for item in rows:
            if item["id"] == device_id:
                item.update(fields, source="manual", needs_review=False)
                updated = updated or item
        conn.execute("UPDATE rack_scans SET devices = ? WHERE run_id = ?", (json.dumps(rows), scan["run_id"]))
    return updated
