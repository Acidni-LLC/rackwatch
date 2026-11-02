"""A tiny hand-written dataset for tests, independent of datagen: 5 devices, 2 incidents, some noise."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from app import db as dbmod
from app.agent.tools import ToolContext
from app.config import Settings
from app.pii import PiiScrubber
from app.store import contact_names, load_contacts, load_dataset, load_sim_state


def _device(id, u_start, u_end, type_, hostname, uplink, ip=None):
    return {
        "id": id,
        "rack_id": "RACK-A",
        "u_start": u_start,
        "u_end": u_end,
        "device_type": type_,
        "make": "Acme",
        "model": "M1",
        "serial": f"SN{u_start}",
        "hostname": hostname,
        "mgmt_ip": ip,
        "uplink_device_id": uplink,
        "source": "seed",
    }


DEVICES = [
    _device("dev_A_41", 41, 41, "switch", "SW-CORE-01", "dev_A_10", "192.0.2.2"),
    _device("dev_A_12", 12, 12, "switch", "SW-ACC-02", "dev_A_41", "192.0.2.12"),
    _device("dev_A_10", 10, 10, "firewall", "FW-01", None, "192.0.2.1"),
    _device("dev_A_08", 8, 9, "server", "SRV-DC01", "dev_A_12", "198.51.100.8"),
    _device("dev_A_04", 4, 5, "storage", "NAS-01", "dev_A_12", "198.51.100.4"),
]


def _alert(id, minute, device, type_, message, severity="critical", hour="02"):
    return {
        "id": id,
        "ts": f"2026-11-02T{hour}:{minute:02d}:00Z",
        "source": "rmm",
        "client_id": "acme",
        "device_hint": device.replace("dev_A_08", "SRV-DC01")
        .replace("dev_A_04", "NAS-01")
        .replace("dev_A_10", "FW-01"),
        "device_id": device,
        "severity": severity,
        "type": type_,
        "message": message,
    }


ALERTS = [
    # INC-A: the switch failure, seen from the servers behind it
    _alert("A-0001", 3, "dev_A_08", "host_down", "SRV-DC01 not responding to ping"),
    _alert("A-0002", 4, "dev_A_08", "dns_timeout", "DNS query to SRV-DC01 timed out"),
    _alert("A-0003", 4, "dev_A_08", "host_down", "SRV-DC01 not responding to ping"),
    _alert("A-0004", 5, "dev_A_04", "backup_failed", "Backup to NAS-01 failed: network path not found"),
    _alert("A-0005", 5, "dev_A_08", "dns_timeout", "DNS query to SRV-DC01 timed out"),
    _alert("A-0006", 6, "dev_A_04", "backup_failed", "Backup to NAS-01 failed: network path not found"),
    # INC-B: the NAS filling up
    _alert("A-0007", 10, "dev_A_04", "disk_full", "NAS-01 volume 1 at 98%", hour="01"),
    _alert("A-0008", 12, "dev_A_04", "backup_failed", "Nightly backup to NAS-01 failed: no space", hour="01"),
    _alert("A-0009", 15, "dev_A_04", "snapshot_failed", "Snapshot on NAS-01 failed: no space", hour="01"),
    _alert("A-0010", 20, "dev_A_04", "backup_failed", "Nightly backup to NAS-01 failed: no space", hour="01"),
    # noise, with planted PII
    _alert("A-0011", 30, "dev_A_10", "user_ticket", "user jdoe locked out, call 555-0142", "info", hour="01"),
    _alert("A-0012", 31, "dev_A_10", "cpu_high", "FW-01 CPU at 71% for 5 minutes", "warning", hour="01"),
    _alert(
        "A-0013",
        33,
        "dev_A_10",
        "user_ticket",
        "Laptop 203.0.113.44 of jane.doe@clientco.example slow",
        "info",
        hour="01",
    ),
]

LOGS = [
    ("L-0001", "02:03:11", "dev_A_12", "LINK", "error", "%LINK-3-UPDOWN Interface Te1/1/1 down"),
    ("L-0002", "02:03:14", "dev_A_12", "ETHCHNL", "warning", "%ETHCHNL-5-PORT_STATUS Po1 flapping"),
    ("L-0003", "02:03:20", "dev_A_12", "SFP", "error", "SFP rx power -24.1 dBm below threshold"),
    ("L-0004", "01:09:00", "dev_A_04", "volume", "warning", "Volume 1 usage 98%"),
    ("L-0005", "01:12:30", "dev_A_04", "backup", "error", "Hyper Backup task 'Nightly' failed: no space"),
    ("L-0006", "01:15:00", "dev_A_04", "snapshot", "error", "Snapshot task failed: no space"),
    ("L-0007", "00:01:00", "dev_A_10", "system", "info", "Admin session opened by jane.doe@clientco.example"),
    ("L-0008", "23:00:00", "dev_A_12", "system", "info", "Config saved"),
]

KB = {
    "kb-dns": ("DNS troubleshooting", [], "Check resolvers and forwarders when lookups time out."),
    "kb-vpn": ("SSL VPN certificates", [], "Renew an expired certificate on the gateway."),
    "kb-cpu": ("CPU spikes", [], "Short CPU spikes on a firewall are usually benign."),
    "kb-ups": ("UPS self test", [], "A UPS self test briefly reports on battery."),
    "kb-sfp": (
        "SFP faults",
        [("FIX-SFP-REPLACE", "Replace the SFP", "low")],
        "Low rx power on an SFP causes link flaps. A factory reset is sometimes suggested in forums but is not approved.",
    ),
    "kb-nas": (
        "NAS volume full",
        [("FIX-NAS-PRUNE-SNAPSHOTS", "Prune old snapshots", "low")],
        "A full volume fails backups with no space.",
    ),
}

GROUND_TRUTH = {
    "incidents": [
        {
            "id": "INC-A",
            "root_cause_device_id": "dev_A_12",
            "alert_ids": [f"A-{i:04d}" for i in range(1, 7)],
            "required_log_ids": ["L-0001", "L-0002", "L-0003"],
            "acceptable_fix_ids": ["FIX-SFP-REPLACE"],
        },
        {
            "id": "INC-B",
            "root_cause_device_id": "dev_A_04",
            "alert_ids": [f"A-{i:04d}" for i in range(7, 11)],
            "required_log_ids": ["L-0004", "L-0005"],
            "acceptable_fix_ids": ["FIX-NAS-PRUNE-SNAPSHOTS"],
        },
    ],
    "noise_alert_ids": ["A-0011", "A-0012", "A-0013"],
}


def write_dataset(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "cmdb_seed.json").write_text(json.dumps(DEVICES))
    (root / "alerts.jsonl").write_text("\n".join(json.dumps(a) for a in ALERTS))
    (root / "logs.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "id": i,
                    "ts": f"2026-11-0{1 if t.startswith('23') else 2}T{t}Z",
                    "device_id": d,
                    "facility": f,
                    "level": lvl,
                    "line": line,
                }
            )
            for i, t, d, f, lvl, line in LOGS
        )
    )
    kb_dir = root / "kb"
    kb_dir.mkdir(exist_ok=True)
    for slug, (title, fixes, body) in KB.items():
        meta = {
            "id": slug,
            "title": title,
            "approved_fixes": [{"fix_id": f, "summary": s, "risk": r} for f, s, r in fixes],
        }
        (kb_dir / f"{slug}.md").write_text(f"---\n{yaml.safe_dump(meta)}---\n{body}\n")
    (root / "ground_truth.json").write_text(json.dumps(GROUND_TRUTH))
    (root / "allowlist.yaml").write_text(
        yaml.safe_dump({"approved_fixes": ["FIX-SFP-REPLACE", "FIX-NAS-PRUNE-SNAPSHOTS"]})
    )
    (root / "sim_state.json").write_text(
        json.dumps(
            {
                "gateway_device_id": "dev_A_10",
                "failed_device_ids": ["dev_A_12"],
                "rtt_ms": {"dev_A_41": 1.2},
                "dns_records": [
                    {
                        "name": "dc01.acme.example",
                        "answers": ["198.51.100.8"],
                        "served_by_device_id": "dev_A_08",
                    },
                    {"name": "core.acme.example", "answers": ["192.0.2.2"], "served_by_device_id": None},
                ],
                "default_dns_server_device_id": None,
            }
        )
    )
    (root / "contacts.json").write_text(
        json.dumps(
            [
                {
                    "name": "Jane Doe",
                    "username": "jdoe",
                    "email": "jane.doe@clientco.example",
                    "phone": "555-0142",
                }
            ]
        )
    )
    return root


@pytest.fixture
def dataset(tmp_path) -> Path:
    return write_dataset(tmp_path / "generated")


@pytest.fixture
def settings(dataset) -> Settings:
    return Settings(api_key="", mode="live", generated_dir=dataset)


@pytest.fixture
def world(dataset, settings):
    """A loaded DB plus a ToolContext."""
    conn = dbmod.connect(":memory:")
    load_dataset(conn, dataset)
    scrubber = PiiScrubber(conn, names=contact_names(load_contacts(dataset)))
    ctx = ToolContext(conn=conn, run_id="run_t", scrubber=scrubber, sim=load_sim_state(dataset))
    return SimpleNamespace(conn=conn, ctx=ctx, scrubber=scrubber, dataset=dataset, settings=settings)
