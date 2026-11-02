"""Synthetic data generator (DESIGN.md §9).

Deterministic: the same --seed and --size always produce byte-identical files.

    python datagen/generate.py --seed 42 --size 512 --out data/generated/

Outputs in --out: alerts.jsonl, logs.jsonl, kb/*.md, ground_truth.json,
cmdb_seed.json, allowlist.yaml, sim_state.json, contacts.json.
Every row validates against backend/app/schemas.py. All IPs are RFC 5737
documentation ranges; fake PII is planted on purpose so the scrubber test
has something to catch (docs/decisions.md D-004).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.schemas import (  # noqa: E402  # noqa: E402
    Alert,
    Contact,
    Device,
    GroundTruth,
    KBArticle,
    LogLine,
    SimState,
)

BASE_TS = datetime(2026, 11, 2, 0, 0, 0, tzinfo=UTC)
DAY = "2026-11-02"
APPROVED_FIXES = [
    "FIX-SFP-REPLACE",
    "FIX-PO-SHUT-NOSHUT",
    "FIX-NAS-PRUNE-SNAPSHOTS",
    "FIX-NAS-EXPAND-QUOTA",
    "FIX-CERT-RENEW-ACME",
    "FIX-UPLINK-FAILOVER",
    "FIX-UPS-BATTERY-TEST",
    "FIX-FW-POLICY-RELOAD",
    "FIX-DNS-FORWARDER",
    "FIX-VOIP-QOS-POLICY",
    "FIX-PATCH-RESEAT",
    "ESCALATE",
]

CONTACTS = [
    {"name": "Jane Doe", "username": "jdoe", "email": "jane.doe@clientco.example",
     "phone": "415-555-0132"},
    {"name": "Bob Smith", "username": "bsmith", "email": "bob.smith@clientco.example",
     "phone": "415-555-0147"},
    {"name": "Alice Ray", "username": "aray", "email": "alice.ray@clientco.example",
     "phone": "212-555-0176"},
    {"name": "Tom Beck", "username": "tbeck", "email": "tom.beck@clientco.example",
     "phone": "212-555-0119"},
    {"name": "Sara Kim", "username": "skim", "email": "sara.kim@clientco.example",
     "phone": "312-555-0158"},
    {"name": "Dan Cole", "username": "dcole", "email": "dan.cole@clientco.example",
     "phone": "312-555-0123"},
]

# Devices behind SW-ACC-02 (the INC-A blast radius).
BEHIND_A12 = ["dev_A_20", "dev_A_08", "dev_A_06", "dev_A_04", "dev_A_02", "dev_A_01"]


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def minute_of(incident: dict) -> int:
    start = datetime.strptime(incident["window_start"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    end = datetime.strptime(incident["window_end"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return int((end - start).total_seconds() // 60)


def ts_in_window(rng: random.Random, incident: dict) -> str:
    start = datetime.strptime(incident["window_start"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    span_min = minute_of(incident)
    return iso(start + timedelta(minutes=rng.randrange(0, span_min + 1), seconds=rng.randrange(0, 60)))


def _alert(aid: int, incident: dict, dev_by_id: dict, device_id: str, atype: str,
           severity: str, source: str, message: str) -> dict:
    dev = dev_by_id[device_id]
    hint = dev["hostname"] if dev["hostname"] else (dev["mgmt_ip"] or device_id)
    return {
        "id": f"A-{aid:04d}",
        "ts": incident["window_start"],
        "source": source,
        "client_id": "Acme Dental Group",
        "device_hint": hint,
        "device_id": device_id,
        "severity": severity,
        "type": atype,
        "message": message,
        "raw": {"monitor": source, "host": hint},
    }


def build_incident_a(rng: random.Random, incident: dict, dev_by_id: dict, host: dict,
                     start_aid: int, count: int) -> list[dict]:
    """SW-ACC-02 port-channel flap: host_down, backup_failed (network path),
    dns_timeout on the DC (red herring), voip_jitter, agent_offline."""
    out: list[dict] = []
    for i in range(count):
        pick = rng.random()
        target = rng.choice(BEHIND_A12 + ["dev_A_12"])
        name = host[target]
        if pick < 0.34:
            alert = _alert(0, incident, dev_by_id, target, "host_down", "critical",
                           "rmm", f"Host {name} is DOWN: no response for 3 polls")
        elif pick < 0.52:
            alert = _alert(0, incident, dev_by_id, target, "backup_failed", "warning",
                           "rmm", f"Backup job 'Nightly-{name}' failed: network path not found")
        elif pick < 0.68:
            alert = _alert(0, incident, dev_by_id, "dev_A_08", "dns_timeout", "warning",
                           "snmp", f"DNS query timed out on {host['dev_A_08']} after 5000ms")
        elif pick < 0.84:
            alert = _alert(0, incident, dev_by_id, "dev_A_20", "voip_jitter", "warning",
                           "snmp", f"VoIP jitter {rng.randrange(30, 90)}ms on {host['dev_A_20']}")
        else:
            alert = _alert(0, incident, dev_by_id, target, "agent_offline", "warning",
                           "rmm", f"RMM agent on {name} went offline unexpectedly")
        alert["id"] = f"A-{start_aid + i:04d}"
        # Timestamps must stay inside the window: regenerate via the incident rng stream.
        alert["ts"] = ts_in_window(rng, incident)
        out.append(alert)
    return out


def build_incident_b(rng: random.Random, incident: dict, dev_by_id: dict, host: dict,
                     start_aid: int, count: int) -> list[dict]:
    """NAS-01 volume full: backup_failed (no space), disk_full, snapshot_failed."""
    out: list[dict] = []
    for i in range(count):
        pick = rng.random()
        if pick < 0.45:
            alert = _alert(0, incident, dev_by_id, "dev_A_04", "backup_failed", "critical",
                           "rmm", "Hyper Backup task 'Nightly' failed on NAS-01: no space left on device")
        elif pick < 0.75:
            alert = _alert(0, incident, dev_by_id, "dev_A_04", "disk_full", "critical",
                           "snmp", f"Disk Volume 1 on NAS-01 at {rng.choice([97, 98, 98, 99])}% capacity")
        else:
            alert = _alert(0, incident, dev_by_id, "dev_A_04", "snapshot_failed", "warning",
                           "syslog", "Snapshot creation failed on NAS-01 Volume 1: no space")
        alert["id"] = f"A-{start_aid + i:04d}"
        alert["ts"] = ts_in_window(rng, incident)
        out.append(alert)
    return out


def build_incident_c(rng: random.Random, incident: dict, dev_by_id: dict, host: dict,
                     start_aid: int, count: int) -> list[dict]:
    """FW-01 SSL-VPN certificate expired: vpn_auth_failed, cert_expired, user tickets."""
    users = [c["username"] for c in CONTACTS]
    out: list[dict] = []
    for i in range(count):
        pick = rng.random()
        if pick < 0.45:
            alert = _alert(0, incident, dev_by_id, "dev_A_10", "vpn_auth_failed", "warning",
                           "syslog", f"VPN auth failed for user {rng.choice(users)}: certificate expired")
        elif pick < 0.75:
            alert = _alert(0, incident, dev_by_id, "dev_A_10", "cert_expired", "critical",
                           "psa", "SSL-VPN certificate on FW-01 expired 2026-11-01T23:59Z")
        else:
            alert = _alert(0, incident, dev_by_id, "dev_A_10", "user_ticket", "info",
                           "psa", "User reports VPN client cannot connect from home office")
        alert["id"] = f"A-{start_aid + i:04d}"
        alert["ts"] = ts_in_window(rng, incident)
        out.append(alert)
    return out


def build_noise(rng: random.Random, dev_by_id: dict, host: dict, device_ids: list[str],
                start_aid: int, count: int) -> list[dict]:
    """Benign flapping CPU, info events and a duplicate monitor."""
    window = {"window_start": f"{DAY}T00:00:00Z", "window_end": f"{DAY}T03:00:00Z"}
    kinds = [
        ("cpu_high", "warning", "rmm", "CPU above 90% for 5m on {h}"),
        ("info", "info", "syslog", "Scheduled task completed on {h}"),
        ("port_flap", "info", "snmp", "Port Gi1/0/7 flapped once on {h} (duplicate monitor)"),
        ("host_down", "critical", "rmm", "Host {h} is DOWN: duplicate monitor check"),
        ("cert_expiring", "info", "psa", "Certificate for {h} expires in 30 days"),
        ("disk_full", "warning", "snmp", "Disk /var at 71% on {h}"),
    ]
    out: list[dict] = []
    for i in range(count):
        atype, sev, src, tmpl = kinds[rng.randrange(len(kinds))]
        target = rng.choice(device_ids)
        alert = _alert(0, window, dev_by_id, target, atype, sev, src, tmpl.format(h=host[target]))
        alert["id"] = f"A-{start_aid + i:04d}"
        alert["ts"] = ts_in_window(rng, window)
        out.append(alert)
    return out


def plant_pii(rng: random.Random, alerts: list[dict], count: int) -> None:
    """Append fake PII to `count` alerts, spread across kinds the scrubber catches."""
    idxs = rng.sample(range(len(alerts)), count)
    laptop = 1
    for n, idx in enumerate(sorted(idxs)):
        contact = CONTACTS[(n // 4) % len(CONTACTS)]
        kind = n % 4
        if kind == 0:
            alerts[idx]["message"] += f" for user {contact['username']} (locked out)"
        elif kind == 1:
            alerts[idx]["message"] += f"; contact {contact['email']}"
        elif kind == 2:
            alerts[idx]["message"] += f"; callback {contact['phone']}"
        else:
            alerts[idx]["message"] += f" from laptop 203.0.113.{laptop}"
            laptop = (laptop % 250) + 1


SIGNATURES: list[tuple[str, str, str, str, str]] = [
    # (incident, device_id, facility, level, line)
    ("INC-A", "dev_A_12", "syslog", "err",
     "%LINK-3-UPDOWN: Interface Te1/1/1 down"),
    ("INC-A", "dev_A_12", "syslog", "err",
     "%ETHCHNL-5-PORT_STATUS: Po1 flapping between Te1/1/1 and Te1/1/2"),
    ("INC-A", "dev_A_12", "syslog", "warning",
     "SFP rx power -24.1 dBm below threshold on Te1/1/1"),
    ("INC-A", "dev_A_12", "syslog", "err",
     "Interface Port-channel1 line protocol down"),
    ("INC-A", "dev_A_08", "daemon", "err",
     "named: DNS query timed out: no route to upstream 198.51.100.8"),
    ("INC-B", "dev_A_04", "daemon", "warning",
     "storage: Volume 1 usage 98% (3.9T of 4.0T)"),
    ("INC-B", "dev_A_04", "daemon", "err",
     "Hyper Backup task 'Nightly' failed: no space left on Volume 1"),
    ("INC-B", "dev_A_04", "daemon", "err",
     "Snapshot creation failed on Volume 1: no space"),
    ("INC-B", "dev_A_04", "daemon", "warning",
     "Disk utilization alert: Volume 1 at 98%, snapshots retained: 31"),
    ("INC-C", "dev_A_10", "daemon", "err",
     "SSL-VPN certificate expired 2026-11-01T23:59Z"),
    ("INC-C", "dev_A_10", "daemon", "warning",
     "vpn: auth failed for remote user: certificate expired"),
    ("INC-C", "dev_A_10", "daemon", "err",
     "sslvpn: tunnel setup failed: server certificate not valid"),
    ("INC-C", "dev_A_10", "daemon", "warning",
     "Certificate for ssl-vpn portal expires in -1 days (already expired)"),
]

LOG_TEMPLATES = [
    ("syslog", "info", "health check passed on {h}"),
    ("daemon", "info", "scheduled job finished on {h}"),
    ("kern", "notice", "link state RX ok on {h}"),
    ("auth", "info", "session closed for monitor on {h}"),
    ("daemon", "warning", "retry 1/3 for poll on {h}"),
    ("syslog", "err", "poll failed for {h}: will retry"),
]


def build_logs(rng: random.Random, alerts: list[dict], host: dict, incidents: dict,
               total: int) -> tuple[list[dict], dict[str, list[str]]]:
    """Signature lines first (L-0001...), then per-alert lines, then noise pad."""
    logs: list[dict] = []
    required: dict[str, list[str]] = {"INC-A": [], "INC-B": [], "INC-C": []}
    lid = 1

    def add(ts: str, device_id: str, facility: str, level: str, line: str) -> str:
        nonlocal lid
        log_id = f"L-{lid:04d}"
        lid += 1
        logs.append({"id": log_id, "ts": ts, "device_id": device_id,
                     "facility": facility, "level": level, "line": line})
        return log_id

    sig_ts = {
        "INC-A": ["2026-11-02T02:03:11Z", "2026-11-02T02:04:02Z", "2026-11-02T02:05:33Z",
                  "2026-11-02T02:06:20Z", "2026-11-02T02:07:44Z"],
        "INC-B": ["2026-11-02T01:12:05Z", "2026-11-02T01:40:31Z", "2026-11-02T02:02:17Z",
                  "2026-11-02T02:11:09Z"],
        "INC-C": ["2026-11-02T00:01:12Z", "2026-11-02T00:31:40Z", "2026-11-02T01:05:22Z",
                  "2026-11-02T02:08:51Z"],
    }
    counters = {"INC-A": 0, "INC-B": 0, "INC-C": 0}
    for inc_id, device_id, facility, level, line in SIGNATURES:
        n = counters[inc_id]
        required[inc_id].append(add(sig_ts[inc_id][n], device_id, facility, level, line))
        counters[inc_id] += 1

    for alert in alerts:
        for _ in range(rng.randrange(4, 7)):
            fac, lvl, tmpl = LOG_TEMPLATES[rng.randrange(len(LOG_TEMPLATES))]
            line = tmpl.format(h=host[alert["device_id"]])
            if rng.random() < 0.25:
                line += f" [{alert['type']}: {alert['id']}]"
            add(alert["ts"], alert["device_id"], fac, lvl, line)

    noise_devs = sorted(host)
    i = 0
    while len(logs) < total:
        fac, lvl, tmpl = LOG_TEMPLATES[rng.randrange(len(LOG_TEMPLATES))]
        dev = noise_devs[rng.randrange(len(noise_devs))]
        minute = rng.randrange(0, 180)
        add(iso(BASE_TS + timedelta(minutes=minute, seconds=rng.randrange(0, 60))),
            dev, fac, lvl, tmpl.format(h=host[dev]))
        i += 1
        if i > total + 1000:  # pragma: no cover - safety only
            break
    return logs[:total], required


KB_DOCS: list[dict] = [
    {"id": "KB-001", "title": "Port-channel flapping on Cisco 9200L access switches",
     "fixes": [("FIX-PO-SHUT-NOSHUT", "Shut then re-open the port-channel member ports", "medium"),
               ("FIX-UPLINK-FAILOVER", "Fail traffic to the redundant uplink", "low")],
     "body": ("Symptom: %ETHCHNL-5-PORT_STATUS flaps and hosts behind the switch drop.\n"
              "Check the SFP optics first; a bad optic mimics a config fault.\n"
              "Do NOT factory reset the switch: it wipes the access VLANs and is never approved.\n")},
    {"id": "KB-002", "title": "Bad SFP optic: low rx power on Te interfaces",
     "fixes": [("FIX-SFP-REPLACE", "Replace the SFP optic on the flapping interface", "medium")],
     "body": ("Symptom: SFP rx power below threshold (for instance -24.1 dBm).\n"
              "Reseat the fiber, then replace the optic with a known-good spare.\n"
              "A factory reset does not fix optics and is not an approved fix.\n")},
    {"id": "KB-003", "title": "NAS volume full: backups and snapshots fail",
     "fixes": [("FIX-NAS-PRUNE-SNAPSHOTS", "Prune old snapshots on the full volume", "low"),
               ("FIX-NAS-EXPAND-QUOTA", "Expand the volume quota", "medium")],
     "body": ("Symptom: Volume usage above 95%, Hyper Backup fails with no space.\n"
              "Prune snapshots first; expanding quota needs a free bay.\n")},
    {"id": "KB-004", "title": "Telling backup failures apart: network path vs no space",
     "fixes": [("FIX-SFP-REPLACE", "Replace the SFP optic on the flapping interface", "medium"),
               ("FIX-NAS-PRUNE-SNAPSHOTS", "Prune old snapshots on the full volume", "low")],
     "body": ("A backup that fails with 'network path not found' points at the network path,\n"
              "while 'no space' points at the NAS volume itself. Read the message text.\n")},
    {"id": "KB-005", "title": "Expired SSL-VPN certificate on FortiGate",
     "fixes": [("FIX-CERT-RENEW-ACME", "Renew the SSL-VPN certificate via ACME", "low")],
     "body": ("Symptom: SSL-VPN certificate expired, remote users cannot connect.\n"
              "Renew through ACME and re-apply to the portal. Never factory reset the firewall.\n")},
    {"id": "KB-006", "title": "DNS timeouts that are really a switching fault",
     "fixes": [("FIX-SFP-REPLACE", "Replace the SFP optic on the flapping interface", "medium"),
               ("FIX-DNS-FORWARDER", "Point the forwarder at the surviving resolver", "low")],
     "body": ("DNS timeouts on a healthy DC often mean the path to it is down.\n"
              "Ping the DC and walk the uplinks before touching DNS config.\n")},
    {"id": "KB-007", "title": "VoIP jitter behind a congested access switch",
     "fixes": [("FIX-VOIP-QOS-POLICY", "Apply the voice VLAN QoS policy", "low")],
     "body": "Jitter above 30ms on the voice VLAN usually follows packet loss upstream.\n"},
    {"id": "KB-008", "title": "UPS battery self-test procedure",
     "fixes": [("FIX-UPS-BATTERY-TEST", "Run the UPS battery self-test", "low")],
     "body": "Run the self-test from the UPS panel; replace the pack if it fails.\n"},
    {"id": "KB-009", "title": "FortiGate policy reload after config edits",
     "fixes": [("FIX-FW-POLICY-RELOAD", "Reload the firewall policy package", "medium")],
     "body": "Reload the policy package after edits; a reset to factory defaults is not approved.\n"},
    {"id": "KB-010", "title": "Duplicate monitor alerts in the RMM",
     "fixes": [("ESCALATE", "Escalate to the service manager", "low")],
     "body": ("A second monitor may re-fire host_down for a host that is already up.\n"
              "If the cause is unclear, escalate rather than guessing.\n")},
    {"id": "KB-011", "title": "Patch panel reseat for continuity faults",
     "fixes": [("FIX-PATCH-RESEAT", "Reseat the patch leads on the panel", "low")],
     "body": "Reseat both ends of the patch lead before replacing hardware.\n"},
    {"id": "KB-012", "title": "When to escalate instead of acting",
     "fixes": [("ESCALATE", "Escalate to the service manager", "low")],
     "body": ("If no approved fix fits the evidence, set the fix to ESCALATE.\n"
              "Guessing, and especially a factory reset, is never acceptable.\n")},
]


def kb_markdown(doc: dict) -> str:
    fixes = "\n".join(f"  - fix_id: {fid}\n    summary: {json.dumps(summary)}\n    risk: {risk}"
                      for fid, summary, risk in doc["fixes"])
    return (f"---\nid: {json.dumps(doc['id'])}\ntitle: {json.dumps(doc['title'])}\n"
            f"approved_fixes:\n{fixes}\n---\n# {doc['title']}\n\n{doc['body']}")


def parse_kb_article(text: str) -> dict:
    front, _, body = text.split("---\n", 2)[1].partition("---\n")
    meta = yaml.safe_load(front)
    return {"id": meta["id"], "title": meta["title"], "body": body.strip(),
            "approved_fixes": meta["approved_fixes"]}


def scaled_counts(size: int, incidents: list[dict]) -> tuple[list[int], int]:
    counts = [round(size * inc["alert_count_512"] / 512) for inc in incidents]
    noise = size - sum(counts)
    return counts, noise


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate RackWatch synthetic data.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "generated")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    scenario = yaml.safe_load((REPO_ROOT / "datagen" / "scenario.yaml").read_text(encoding="utf-8"))
    devices = scenario["devices"]
    dev_by_id = {d["id"]: d for d in devices}
    host = {d["id"]: d["hostname"] or d["mgmt_ip"] or d["id"] for d in devices}
    incidents = scenario["incidents"]
    by_id = {inc["id"]: inc for inc in incidents}

    counts, noise_count = scaled_counts(args.size, incidents)
    builders = {"INC-A": build_incident_a, "INC-B": build_incident_b, "INC-C": build_incident_c}
    alerts: list[dict] = []
    incident_alert_ids: dict[str, list[str]] = {}
    aid = 1
    for inc, count in zip(incidents, counts, strict=True):
        group = builders[inc["id"]](rng, inc, dev_by_id, host, aid, count)
        alerts.extend(group)
        incident_alert_ids[inc["id"]] = [a["id"] for a in group]
        aid += count
    noise_alerts = build_noise(rng, dev_by_id, host, sorted(dev_by_id), aid, noise_count)
    alerts.extend(noise_alerts)
    noise_ids = [a["id"] for a in noise_alerts]

    pii_count = max(4, round(30 * args.size / 512))
    plant_pii(rng, alerts, min(pii_count, len(alerts)))

    log_total = round(3000 * args.size / 512)
    logs, required = build_logs(rng, alerts, host, by_id, log_total)

    # Validate every row against the shared contract before writing anything.
    for alert in alerts:
        Alert.model_validate(alert)
    for log in logs:
        LogLine.model_validate(log)
    articles = [parse_kb_article(kb_markdown(doc)) for doc in KB_DOCS]
    for article in articles:
        KBArticle.model_validate(article)
    seed_devices = [{**d, "rack_id": scenario["rack_id"], "confidence": 1.0,
                     "field_confidence": {}, "bbox": None, "source_image": None,
                     "source": "seed", "needs_review": False} for d in devices]
    for dev in seed_devices:
        Device.model_validate(dev)
    ground_truth = GroundTruth.model_validate({
        "incidents": [
            {"id": inc["id"], "root_cause_device_id": inc["root_cause_device_id"],
             "alert_ids": incident_alert_ids[inc["id"]],
             "required_log_ids": required[inc["id"]],
             "acceptable_fix_ids": inc["acceptable_fix_ids"]}
            for inc in incidents
        ],
        "noise_alert_ids": noise_ids,
    })
    rtts = {d["id"]: round(rng.uniform(0.4, 3.0), 2) for d in devices if d["id"] != "dev_A_12"}
    sim_state = SimState.model_validate({
        "gateway_device_id": scenario["gateway_device_id"],
        "failed_device_ids": ["dev_A_12"],
        "rtt_ms": rtts,
        "dns_records": [
            {"name": "ad.acme.example", "answers": ["198.51.100.8"],
             "served_by_device_id": "dev_A_08"},
            {"name": "vpn.acme.example", "answers": ["192.0.2.1"],
             "served_by_device_id": "dev_A_10"},
            {"name": "nas.acme.example", "answers": ["198.51.100.4"],
             "served_by_device_id": "dev_A_08"},
        ],
        "default_dns_server_device_id": "dev_A_08",
    })
    contacts = [Contact.model_validate(c) for c in CONTACTS]

    out: Path = args.out
    (out / "kb").mkdir(parents=True, exist_ok=True)
    with (out / "alerts.jsonl").open("w", encoding="utf-8") as f:
        for alert in alerts:
            f.write(json.dumps(alert, sort_keys=True) + "\n")
    with (out / "logs.jsonl").open("w", encoding="utf-8") as f:
        for log in logs:
            f.write(json.dumps(log, sort_keys=True) + "\n")
    for doc in KB_DOCS:
        (out / "kb" / f"{doc['id']}.md").write_text(kb_markdown(doc), encoding="utf-8")
    (out / "ground_truth.json").write_text(
        json.dumps(ground_truth.model_dump(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "cmdb_seed.json").write_text(
        json.dumps(seed_devices, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "allowlist.yaml").write_text(
        yaml.safe_dump({"approved_fixes": APPROVED_FIXES}, sort_keys=False), encoding="utf-8")
    (out / "sim_state.json").write_text(
        json.dumps(sim_state.model_dump(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "contacts.json").write_text(
        json.dumps([c.model_dump() for c in contacts], indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"wrote {len(alerts)} alerts, {len(logs)} logs, {len(articles)} kb articles to {out}")


if __name__ == "__main__":
    main()
