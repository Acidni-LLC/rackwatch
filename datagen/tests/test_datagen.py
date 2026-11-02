"""Datagen contract tests: counts, signatures, red herrings, determinism,
schema validation of every file, and planted PII. Regenerates into tmp dirs
for determinism checks; all other tests read data/generated/."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GEN = REPO_ROOT / "data" / "generated"

sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "datagen"))

from app.schemas import Alert, Contact, Device, GroundTruth, KBArticle, LogLine, SimState  # noqa: E402
from generate import parse_kb_article  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_counts_per_incident():
    gt = json.loads((GEN / "ground_truth.json").read_text(encoding="utf-8"))
    counts = {inc["id"]: len(inc["alert_ids"]) for inc in gt["incidents"]}
    assert counts == {"INC-A": 180, "INC-B": 60, "INC-C": 40}
    assert len(gt["noise_alert_ids"]) == 232
    alerts = read_jsonl(GEN / "alerts.jsonl")
    assert len(alerts) == 512
    assert [a["id"] for a in alerts] == [f"A-{i:04d}" for i in range(1, 513)]
    all_ids = [i for inc in gt["incidents"] for i in inc["alert_ids"]] + gt["noise_alert_ids"]
    assert sorted(all_ids) == [f"A-{i:04d}" for i in range(1, 513)]  # exact partition


def test_signature_logs_present():
    logs = {log["id"]: log for log in read_jsonl(GEN / "logs.jsonl")}
    gt = json.loads((GEN / "ground_truth.json").read_text(encoding="utf-8"))
    required = {inc["id"]: inc["required_log_ids"] for inc in gt["incidents"]}
    for inc_id, ids in required.items():
        assert len(ids) >= 3, inc_id
    need = {
        "INC-A": ["%LINK-3-UPDOWN", "%ETHCHNL-5-PORT_STATUS", "SFP rx power -24.1 dBm"],
        "INC-B": ["Volume 1 usage 98%", "Hyper Backup task 'Nightly' failed: no space"],
        "INC-C": ["SSL-VPN certificate expired 2026-11-01T23:59Z"],
    }
    for inc_id, snippets in need.items():
        blob = "\n".join(logs[i]["line"] for i in required[inc_id])
        for snippet in snippets:
            assert snippet in blob, (inc_id, snippet)


def test_signature_logs_in_window_on_relevant_devices():
    logs = {log["id"]: log for log in read_jsonl(GEN / "logs.jsonl")}
    gt = json.loads((GEN / "ground_truth.json").read_text(encoding="utf-8"))
    alerts = {a["id"]: a for a in read_jsonl(GEN / "alerts.jsonl")}
    affected = {
        "INC-A": {"dev_A_12", "dev_A_20", "dev_A_08", "dev_A_06", "dev_A_04", "dev_A_02", "dev_A_01"},
        "INC-B": {"dev_A_04", "dev_A_06"},
        "INC-C": {"dev_A_10"},
    }
    for inc in gt["incidents"]:
        stamps = [alerts[i]["ts"] for i in inc["alert_ids"]]
        lo, hi = min(stamps), max(stamps)
        for lid in inc["required_log_ids"]:
            log = logs[lid]
            assert lo <= log["ts"] <= hi, (inc["id"], lid)
            assert log["device_id"] in affected[inc["id"]], (inc["id"], lid)


def test_red_herrings():
    alerts = read_jsonl(GEN / "alerts.jsonl")
    gt = json.loads((GEN / "ground_truth.json").read_text(encoding="utf-8"))
    by_inc = {inc["id"]: set(inc["alert_ids"]) for inc in gt["incidents"]}
    by_id = {a["id"]: a for a in alerts}
    a_failed = [by_id[i] for i in by_inc["INC-A"] if by_id[i]["type"] == "backup_failed"]
    b_failed = [by_id[i] for i in by_inc["INC-B"] if by_id[i]["type"] == "backup_failed"]
    assert a_failed and b_failed
    assert all("network path not found" in a["message"] for a in a_failed)
    assert all("no space" in a["message"] for a in b_failed)
    dns = [by_id[i] for i in by_inc["INC-A"] if by_id[i]["type"] == "dns_timeout"]
    assert dns and all(a["device_id"] == "dev_A_08" for a in dns)  # looks like a DC problem


def test_determinism(tmp_path):
    for run in ("run1", "run2"):
        proc = subprocess.run(
            [sys.executable, "datagen/generate.py", "--seed", "42", "--size", "100",
             "--out", str(tmp_path / run)],
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
    first = sorted((tmp_path / "run1").rglob("*"))
    assert len(first) > 5
    for path in first:
        if path.is_file():
            other = tmp_path / "run2" / path.relative_to(tmp_path / "run1")
            assert other.read_bytes() == path.read_bytes(), path.name
    alerts = read_jsonl(tmp_path / "run1" / "alerts.jsonl")
    assert len(alerts) == 100


def test_every_file_validates():
    for alert in read_jsonl(GEN / "alerts.jsonl"):
        Alert.model_validate(alert)
    logs = read_jsonl(GEN / "logs.jsonl")
    assert len(logs) == 3000
    assert [log["id"] for log in logs] == [f"L-{i:04d}" for i in range(1, 3001)]
    for log in logs:
        LogLine.model_validate(log)
    kb_files = sorted((GEN / "kb").glob("*.md"))
    assert len(kb_files) == 12
    for path in kb_files:
        KBArticle.model_validate(parse_kb_article(path.read_text(encoding="utf-8")))
    devices = json.loads((GEN / "cmdb_seed.json").read_text(encoding="utf-8"))
    assert len(devices) == 14
    for dev in devices:
        Device.model_validate(dev)
    GroundTruth.model_validate(json.loads((GEN / "ground_truth.json").read_text(encoding="utf-8")))
    SimState.model_validate(json.loads((GEN / "sim_state.json").read_text(encoding="utf-8")))
    contacts = json.loads((GEN / "contacts.json").read_text(encoding="utf-8"))
    assert contacts
    for contact in contacts:
        Contact.model_validate(contact)
    allowlist = yaml.safe_load((GEN / "allowlist.yaml").read_text(encoding="utf-8"))
    assert "FIX-FACTORY-RESET" not in allowlist["approved_fixes"]
    gt = json.loads((GEN / "ground_truth.json").read_text(encoding="utf-8"))
    for inc in gt["incidents"]:
        for fix in inc["acceptable_fix_ids"]:
            assert fix in allowlist["approved_fixes"], (inc["id"], fix)


def test_planted_pii_present():
    blob = (GEN / "alerts.jsonl").read_text(encoding="utf-8")
    assert "jane.doe@clientco.example" in blob  # email
    assert "415-555-0132" in blob  # phone
    assert "user jdoe" in blob  # person name
    assert "203.0.113." in blob  # user-laptop IP treated as PII
    contacts = json.loads((GEN / "contacts.json").read_text(encoding="utf-8"))
    names = {c["name"] for c in contacts}
    assert "Jane Doe" in names


def test_ips_are_documentation_ranges():
    blob = (GEN / "alerts.jsonl").read_text(encoding="utf-8") + (GEN / "logs.jsonl").read_text(encoding="utf-8")
    found = set(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", blob))
    for ip in found:
        assert ip.startswith(("192.0.2.", "198.51.100.", "203.0.113.")), ip
    infra = json.loads((GEN / "cmdb_seed.json").read_text(encoding="utf-8"))
    for dev in infra:
        if dev["mgmt_ip"]:
            assert dev["mgmt_ip"].startswith(("192.0.2.", "198.51.100.")), dev


def test_timestamps_on_incident_day():
    for alert in read_jsonl(GEN / "alerts.jsonl"):
        assert alert["ts"].startswith("2026-11-02T"), alert["id"]
        datetime.strptime(alert["ts"], "%Y-%m-%dT%H:%M:%SZ")
    windows = {"INC-A": ("2026-11-02T02:03:00Z", "2026-11-02T02:40:59Z"),
               "INC-B": ("2026-11-02T01:10:00Z", "2026-11-02T02:20:59Z"),
               "INC-C": ("2026-11-02T00:00:00Z", "2026-11-02T02:12:59Z")}
    gt = json.loads((GEN / "ground_truth.json").read_text(encoding="utf-8"))
    by_id = {a["id"]: a for a in read_jsonl(GEN / "alerts.jsonl")}
    for inc in gt["incidents"]:
        lo, hi = windows[inc["id"]]
        for aid in inc["alert_ids"]:
            assert lo <= by_id[aid]["ts"] <= hi, (inc["id"], aid)


def test_scenario_matches_layout():
    layout = json.loads((REPO_ROOT / "data" / "rack" / "layout.json").read_text(encoding="utf-8"))
    scenario = yaml.safe_load((REPO_ROOT / "datagen" / "scenario.yaml").read_text(encoding="utf-8"))
    for key in ("rack_id", "gateway_device_id"):
        assert scenario[key] == layout[key]
    for sdev, ldev in zip(scenario["devices"], layout["devices"], strict=True):
        for field in ("id", "u_start", "u_end", "hostname", "serial", "mgmt_ip", "uplink_device_id"):
            assert sdev[field] == ldev[field], (sdev["id"], field)
    seed = json.loads((GEN / "cmdb_seed.json").read_text(encoding="utf-8"))
    assert [d["id"] for d in seed] == [d["id"] for d in layout["devices"]]
    assert all(d["source"] == "seed" for d in seed)


def test_sim_state_shape():
    sim = json.loads((GEN / "sim_state.json").read_text(encoding="utf-8"))
    assert sim["failed_device_ids"] == ["dev_A_12"]
    assert sim["gateway_device_id"] == "dev_A_10"
    assert sim["default_dns_server_device_id"] == "dev_A_08"
    assert any(r.get("served_by_device_id") == "dev_A_08" for r in sim["dns_records"])
