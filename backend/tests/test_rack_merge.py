"""Vision output merges into the CMDB without forking it, and the agent's view of the seed stays stable."""

from __future__ import annotations

from app import rack
from app.agent.tools import run_tool
from app.schemas import BBox, Device, RackScanResult


def scanned(id, u_start, u_end, hostname, serial="READ-SN", make="CISCO"):
    return Device(
        id=id,
        rack_id="RACK-A",
        u_start=u_start,
        u_end=u_end,
        device_type="switch",
        make=make,
        hostname=hostname,
        serial=serial,
        confidence=0.8,
        field_confidence={"serial": 0.8},
        bbox=BBox(x1=1, y1=2, x2=3, y2=4),
        source="vision",
    )


def scan_of(*devices):
    return RackScanResult(
        rack_id="RACK-A", image_path="x.jpg", image_w=10, image_h=10, total_u=42, devices=list(devices)
    )


def test_seeded_rack_keeps_seed_values_and_gains_the_box(world):
    before = run_tool(world.ctx, "cmdb_lookup", {"query": "SW-ACC-02"})
    out = rack.merge_scan(world.conn, scan_of(scanned("dev_A_12", 12, 12, "SW-ACC-02")), seeded_rack=True)
    assert out[0].serial == "READ-SN"  # the table shows what vision read
    row = world.conn.execute("SELECT * FROM devices WHERE id = 'dev_A_12'").fetchone()
    assert row["serial"] == "SN12" and row["source"] == "vision" and row["bbox"]
    assert run_tool(world.ctx, "cmdb_lookup", {"query": "SW-ACC-02"}) == before  # the agent sees no change


def test_misread_rail_matches_by_label_and_is_flagged(world):
    out = rack.merge_scan(world.conn, scan_of(scanned("dev_A_30", 30, 30, "SW-ACC-02")), seeded_rack=True)
    assert out[0].id == "dev_A_12" and out[0].needs_review is True
    assert world.conn.execute("SELECT COUNT(*) AS n FROM devices").fetchone()["n"] == 5


def test_match_by_serial_when_hostname_is_unread(world):
    out = rack.merge_scan(
        world.conn, scan_of(scanned("dev_A_30", 30, 30, None, serial="SN12")), seeded_rack=True
    )
    assert out[0].id == "dev_A_12"


def test_unmatched_device_stays_out_of_the_seeded_cmdb(world):
    rack.merge_scan(
        world.conn, scan_of(scanned("dev_A_20", 20, 20, "PHANTOM", serial=None)), seeded_rack=True
    )
    assert world.conn.execute("SELECT 1 FROM devices WHERE id = 'dev_A_20'").fetchone() is None


def test_unmatched_device_joins_the_cmdb_for_a_real_photo(world):
    rack.merge_scan(
        world.conn, scan_of(scanned("dev_A_20", 20, 20, "NEW-SW", serial=None)), seeded_rack=False
    )
    assert (
        world.conn.execute("SELECT hostname FROM devices WHERE id = 'dev_A_20'").fetchone()["hostname"]
        == "NEW-SW"
    )


def test_span_matched_needs_the_whole_span(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(
        '[{"id": "dev_A_20", "u_start": 20, "u_end": 21}, {"id": "dev_A_12", "u_start": 12, "u_end": 12}]',
        encoding="utf-8",
    )
    devices = [scanned("dev_A_20", 20, 20, "SRV-VOIP01"), scanned("dev_A_12", 12, 12, "SW-ACC-02")]
    assert rack.span_matched(devices, seed) == 1
    devices.append(scanned("dev_A_37", 37, 37, "SW-ACC-01"))
    assert rack.span_matched(devices, seed) == 1
