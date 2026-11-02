"""The shared contract. Every table row, tool payload, generator file and vision result validates here.

Frozen at M0. Changes need a note in docs/decisions.md and docs/muse-code-log.md (DESIGN.md §16).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


DeviceType = Literal[
    "switch", "server", "firewall", "router", "ups", "pdu", "patch_panel", "storage", "kvm", "blank", "other"
]
DeviceSource = Literal["vision", "seed", "manual"]
AlertSource = Literal["rmm", "psa", "snmp", "syslog"]
Severity = Literal["info", "warning", "critical"]
ClaimKind = Literal["root_cause", "impact", "timeline", "evidence"]
IncidentStatus = Literal["draft", "verified", "failed", "approved", "escalated"]
RunKind = Literal["vision", "triage", "voice"]
RunMode = Literal["live", "record", "replay"]
RunStatus = Literal["running", "passed", "failed", "budget_exceeded", "error"]
Priority = Literal["P1", "P2", "P3", "P4"]
Tone = Literal["calm", "urgent"]
FixRisk = Literal["low", "medium", "high"]


def device_id(rack_id: str, u_start: int) -> str:
    """`dev_<rack>_<u_start>`: RACK-A at U8 is dev_A_08. Shared by the seed and vision output."""
    return f"dev_{rack_id.removeprefix('RACK-')}_{u_start:02d}"


# ---- CMDB ------------------------------------------------------------------------------


class BBox(Strict):
    x1: int
    y1: int
    x2: int
    y2: int


class Device(Strict):
    id: str
    rack_id: str
    u_start: int = Field(ge=1)  # 1 is the bottom of the rack; a 2U device has u_end = u_start + 1
    u_end: int = Field(ge=1)
    device_type: DeviceType
    make: str | None = None  # null if unreadable, never guessed
    model: str | None = None
    serial: str | None = None
    hostname: str | None = None
    mgmt_ip: str | None = None
    uplink_device_id: str | None = None  # topology; cmdb_lookup returns it, ping_sim walks it
    confidence: float = Field(default=1.0, ge=0, le=1)
    field_confidence: dict[str, float] = Field(default_factory=dict)  # {make, model, serial, u}
    bbox: BBox | None = None
    source_image: str | None = None
    source: DeviceSource = "seed"
    needs_review: bool = False  # confidence < 0.6 or serial checksum fails


class RackScanResult(Strict):
    rack_id: str
    image_path: str
    image_w: int
    image_h: int
    total_u: int
    devices: list[Device]
    overlay_path: str | None = None
    calls: list[str] = Field(default_factory=list)  # call-log ids
    accuracy: dict[str, Any] | None = None  # diff against cmdb_seed.json, when a seed is given


# ---- Alerts, logs, KB ------------------------------------------------------------------


class Alert(Strict):
    id: str
    ts: str  # ISO 8601
    source: AlertSource
    client_id: str
    device_hint: str  # hostname or IP as raw text from the monitor
    device_id: str | None = None  # resolved
    severity: Severity
    type: str  # host_down, backup_failed, voip_jitter, dns_timeout, disk_full, cpu_high, port_flap, ...
    message: str
    raw: dict[str, Any] = Field(default_factory=dict)
    scrubbed_message: str | None = None  # set at ingest by app.pii


class LogLine(Strict):
    id: str  # L-0001...
    ts: str
    device_id: str
    facility: str
    level: str
    line: str


class ApprovedFix(Strict):
    fix_id: str
    summary: str
    risk: FixRisk


class KBArticle(Strict):
    id: str
    title: str
    body: str
    approved_fixes: list[ApprovedFix] = Field(default_factory=list)


# ---- Generator-only files (agent tools never expose ground truth) ----------------------


class GroundTruthIncident(Strict):
    id: str
    root_cause_device_id: str
    alert_ids: list[str]
    required_log_ids: list[str]
    acceptable_fix_ids: list[str]


class GroundTruth(Strict):
    incidents: list[GroundTruthIncident]
    noise_alert_ids: list[str]


class DnsRecord(Strict):
    name: str
    answers: list[str]
    served_by_device_id: str | None = None  # the DNS server behind this name


class SimState(Strict):
    """Scenario state behind ping_sim and dns_sim. Written by datagen as sim_state.json.

    Holds observable facts about the network, not incident labels, so ping_sim can compute a
    path that breaks at a failed device without the agent ever seeing ground truth.
    """

    gateway_device_id: str  # every ping walks uplink_device_id from the target to here
    failed_device_ids: list[str]  # devices whose data plane is down right now
    rtt_ms: dict[str, float] = Field(default_factory=dict)  # device_id -> rtt when reachable
    dns_records: list[DnsRecord] = Field(default_factory=list)
    default_dns_server_device_id: str | None = None


class Contact(Strict):
    """A PSA contact. Names go to the PII scrubber's names list."""

    name: str
    username: str | None = None
    email: str | None = None
    phone: str | None = None


# ---- Incidents -------------------------------------------------------------------------


class Claim(Strict):
    id: str
    text: str
    citations: list[str]  # log ids
    kind: ClaimKind


class Ticket(Strict):
    ticket_id: str
    incident_id: str
    title: str
    priority: Priority
    summary: str
    affected_device_ids: list[str]
    steps: list[str]
    citations: list[str]
    work_log: list[str] = Field(default_factory=list)


class ClientEmail(Strict):
    email_id: str
    incident_id: str
    subject: str
    body: str
    tone: Tone


class VoiceNote(Strict):
    id: str
    ts: str
    transcript: str  # scrubbed
    duration_ms: int | None = None
    call_id: str | None = None
    work_log_line: str | None = None


class Incident(Strict):
    id: str
    run_id: str
    title: str
    root_cause_device_id: str | None = None
    root_cause: str
    alert_ids: list[str]
    claims: list[Claim]
    proposed_fix_id: str
    ticket: Ticket | None = None
    client_email: ClientEmail | None = None
    status: IncidentStatus = "draft"
    voice_notes: list[VoiceNote] = Field(default_factory=list)
    confidence: float | None = None
    escalation_reason: str | None = None
    created_at: str | None = None


class SubmittedIncident(Strict):
    """One incident in the submit_incidents tool payload."""

    id: str
    title: str
    root_cause_device_id: str
    root_cause: str
    alert_ids: list[str]  # explicit ids and/or selector strings, expanded by the harness before verifying
    claims: list[Claim]
    proposed_fix_id: str
    ticket_id: str | None = None
    email_id: str | None = None
    confidence: float | None = None


class Submission(Strict):
    incidents: list[SubmittedIncident]
    noise_alert_ids: list[str] = Field(default_factory=list)


# ---- Verifier --------------------------------------------------------------------------


class Check(Strict):
    rule: str
    passed: bool
    detail: str = ""
    incident_id: str | None = None


class Grouping(Strict):
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    ari: float = 0.0
    unassigned_alerts: int = 0


class VerifierResult(Strict):
    run_id: str
    attempt: int
    passed: bool
    checks: list[Check]
    grouping: Grouping = Field(default_factory=Grouping)
    feedback_for_agent: str = ""


# ---- Runs and the visible call log -----------------------------------------------------


class Run(Strict):
    id: str
    kind: RunKind
    mode: RunMode
    started_at: str
    finished_at: str | None = None
    attempts: int = 0
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    status: RunStatus = "running"
    cassette: str | None = None


class CallRecord(Strict):
    """One row of the visible call log. Never holds the key or auth headers."""

    id: str
    run_id: str | None = None
    ts: str
    model: str
    endpoint: str
    purpose: str
    effort: str | None = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_cached: int = 0
    tokens_out: int = 0
    est_cost_usd: float = 0.0
    status: str = "ok"
    request_digest: str = ""  # sha256 of the scrubbed, normalized request
    response_excerpt: str = ""  # at most 500 chars
    replayed: bool = False
    diverged: bool = False
