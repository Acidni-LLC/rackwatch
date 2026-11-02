"""Deterministic verifier (DESIGN.md §11). Pure Python and no model.

The agent reaches this only through submit_incidents, cannot configure it, and never sees ground truth. R6
compares against seeded ground truth, which is fair for a demo with seeded data. With `ground_truth=None`
(production mode, an unlabeled feed) R6 and the truth half of R7 are off and everything else still applies.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import yaml
from scipy.optimize import linear_sum_assignment

from ..pii import TOKEN, find_unscrubbed
from ..schemas import (
    Alert,
    Check,
    Device,
    GroundTruth,
    Grouping,
    LogLine,
    Submission,
    SubmittedIncident,
    VerifierResult,
)

ESCALATE = "ESCALATE"
RULES = (
    "R1_citations_exist",
    "R2_citations_real",
    "R3_citation_relevance",
    "R4_device_resolves",
    "R5_coverage",
    "R6_grouping_vs_truth",
    "R7_fix_allowed",
    "R8_drafts_clean",
)
WINDOW_MARGIN = timedelta(minutes=30)
JACCARD_MIN = 0.8
EMAIL_MAX_WORDS = 180
LOG_ID = re.compile(r"\bL-\d{3,}\b")
JARGON = re.compile(
    r"%[A-Z0-9_]+-\d-[A-Z_]+|\b[TG][ei]\d+/\d+/\d+\b|\bdev_[A-Za-z0-9]+_\d+\b|\bPo\d+\b|\bdBm\b"
)
U_NUMBER = re.compile(r"\bU\s?(\d{1,2})(?:\s?[-–]\s?U?(\d{1,2}))?\b")


@dataclass
class VerifierInputs:
    run_id: str
    attempt: int
    alerts: dict[str, Alert]
    logs: dict[str, LogLine]
    devices: dict[str, Device]
    retrieved_log_ids: set[str]
    tickets: dict[str, dict]
    emails: dict[str, dict]
    allowed_fix_ids: set[str]
    known_pii_tokens: set[str] = field(default_factory=set)
    ground_truth: GroundTruth | None = None


def load_ground_truth(generated_dir: Path) -> GroundTruth | None:
    path = Path(generated_dir) / "ground_truth.json"
    return GroundTruth(**json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None


def load_allowlist(generated_dir: Path) -> set[str]:
    """allowlist.yaml holds `approved_fixes`: a list of ids or of {fix_id, ...} objects."""
    data = yaml.safe_load((Path(generated_dir) / "allowlist.yaml").read_text(encoding="utf-8")) or {}
    items = data.get("approved_fixes", data) if isinstance(data, dict) else data
    return {i["fix_id"] if isinstance(i, dict) else str(i) for i in items}


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ---- grouping metrics ------------------------------------------------------------------


def pair_counts(labels_a: list, labels_b: list) -> tuple[int, int, int, int]:
    """Pairwise (both, a_only, b_only, neither) over two labelings of the same items."""
    contingency = Counter(zip(labels_a, labels_b, strict=True))

    def comb2(n: int) -> int:
        return n * (n - 1) // 2

    both = sum(comb2(v) for v in contingency.values())
    in_a = sum(comb2(v) for v in Counter(labels_a).values())
    in_b = sum(comb2(v) for v in Counter(labels_b).values())
    total = comb2(len(labels_a))
    return both, in_a - both, in_b - both, total - in_a - in_b + both


def grouping_metrics(
    pred: dict[str, str | None], truth: dict[str, str | None], unassigned: int = 0
) -> Grouping:
    """Pairwise precision, recall, F1 and ARI. Noise alerts count as singletons, so they form no pairs."""
    ids = sorted(truth)
    pred_labels = [pred.get(a) or f"~{a}" for a in ids]
    true_labels = [truth[a] or f"~{a}" for a in ids]
    both, pred_only, true_only, neither = pair_counts(pred_labels, true_labels)
    precision = both / (both + pred_only) if both + pred_only else 1.0
    recall = both / (both + true_only) if both + true_only else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    n = both + pred_only + true_only + neither
    expected = (both + pred_only) * (both + true_only) / n if n else 0.0
    maximum = ((both + pred_only) + (both + true_only)) / 2
    ari = (both - expected) / (maximum - expected) if maximum - expected else 1.0
    return Grouping(
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        ari=round(ari, 4),
        unassigned_alerts=unassigned,
    )


# ---- the rules -------------------------------------------------------------------------


def _r1(sub: Submission) -> list[Check]:
    out = []
    for inc in sub.incidents:
        if not inc.claims:
            out.append(
                Check(rule=RULES[0], passed=False, detail=f"{inc.id} has no claims", incident_id=inc.id)
            )
        for claim in inc.claims:
            if not claim.citations:
                out.append(
                    Check(
                        rule=RULES[0],
                        passed=False,
                        detail=f"claim {claim.id} in {inc.id} has no citation",
                        incident_id=inc.id,
                    )
                )
    return out


def _r2(sub: Submission, inp: VerifierInputs) -> list[Check]:
    out = []
    for inc in sub.incidents:
        for claim in inc.claims:
            for cite in claim.citations:
                if cite not in inp.logs:
                    out.append(
                        Check(
                            rule=RULES[1], passed=False, detail=f"{cite} does not exist", incident_id=inc.id
                        )
                    )
                elif cite not in inp.retrieved_log_ids:
                    out.append(
                        Check(
                            rule=RULES[1],
                            passed=False,
                            detail=f"{cite} was never retrieved",
                            incident_id=inc.id,
                        )
                    )
    return out


def _incident_devices(inc: SubmittedIncident, inp: VerifierInputs) -> set[str]:
    devices = {inc.root_cause_device_id}
    devices.update(
        inp.alerts[a].device_id for a in inc.alert_ids if a in inp.alerts and inp.alerts[a].device_id
    )
    ticket = inp.tickets.get(inc.ticket_id or "")
    if ticket:
        devices.update(ticket.get("affected_device_ids") or [])
    uplinks = {
        inp.devices[d].uplink_device_id
        for d in devices
        if d in inp.devices and inp.devices[d].uplink_device_id
    }
    return devices | uplinks


def _r3(sub: Submission, inp: VerifierInputs) -> list[Check]:
    out = []
    for inc in sub.incidents:
        stamps = [parse_ts(inp.alerts[a].ts) for a in inc.alert_ids if a in inp.alerts]
        if not stamps:
            continue
        start, end = min(stamps) - WINDOW_MARGIN, max(stamps) + WINDOW_MARGIN
        related = _incident_devices(inc, inp)
        for claim in inc.claims:
            for cite in claim.citations:
                line = inp.logs.get(cite)
                if line is None:
                    continue
                if line.device_id not in related:
                    out.append(
                        Check(
                            rule=RULES[2],
                            passed=False,
                            detail=f"{cite} is from {line.device_id}, which is not the root cause, an affected device or their uplink in {inc.id}",
                            incident_id=inc.id,
                        )
                    )
                elif not start <= parse_ts(line.ts) <= end:
                    out.append(
                        Check(
                            rule=RULES[2],
                            passed=False,
                            detail=f"{cite} at {line.ts} is outside the alert window of {inc.id} (±30 min)",
                            incident_id=inc.id,
                        )
                    )
    return out


def _u_mentions_ok(text: str, device: Device) -> bool | None:
    """True if `text` gives the device's U correctly, False if it gives a wrong U, None if it gives none."""
    found = None
    for match in U_NUMBER.finditer(text):
        low = int(match.group(1))
        high = int(match.group(2) or low)
        if device.u_start <= low <= device.u_end or device.u_start <= high <= device.u_end:
            found = True
        elif found is None:
            found = False
    return found


def _r4(sub: Submission, inp: VerifierInputs) -> list[Check]:
    out = []
    for inc in sub.incidents:
        device = inp.devices.get(inc.root_cause_device_id)
        if device is None:
            out.append(
                Check(
                    rule=RULES[3],
                    passed=False,
                    detail=f"{inc.root_cause_device_id} is not in the CMDB",
                    incident_id=inc.id,
                )
            )
            continue
        mentions = [_u_mentions_ok(c.text, device) for c in inc.claims]
        mentions = [m for m in mentions if m is not None]
        if not any(mentions):
            wrong = bool(mentions)
            detail = (
                f"claims in {inc.id} give the wrong U position for {device.hostname or device.id}"
                if wrong
                else f"claims in {inc.id} do not state the rack U position of {device.hostname or device.id}"
            )
            out.append(Check(rule=RULES[3], passed=False, detail=detail, incident_id=inc.id))
    return out


def _r5(sub: Submission, inp: VerifierInputs) -> tuple[list[Check], int]:
    out: list[Check] = []
    seen: Counter[str] = Counter()
    for inc in sub.incidents:
        seen.update(inc.alert_ids)
    seen.update(sub.noise_alert_ids)
    unknown = sorted(a for a in seen if a not in inp.alerts)
    duplicates = sorted(a for a, n in seen.items() if n > 1 and a in inp.alerts)
    unassigned = sorted(a for a in inp.alerts if a not in seen)
    for inc in sub.incidents:
        if not inc.alert_ids:
            out.append(
                Check(
                    rule=RULES[4],
                    passed=False,
                    detail=f"{inc.id} has no alert_ids; list its alerts or selectors",
                    incident_id=inc.id,
                )
            )
    if unassigned:
        out.append(
            Check(
                rule=RULES[4],
                passed=False,
                detail=f"{len(unassigned)} alerts unassigned: {', '.join(unassigned[:20])}",
            )
        )
    if duplicates:
        out.append(
            Check(
                rule=RULES[4],
                passed=False,
                detail=f"{len(duplicates)} alerts assigned twice: {', '.join(duplicates[:20])}",
            )
        )
    if unknown:
        out.append(
            Check(
                rule=RULES[4],
                passed=False,
                detail=f"{len(unknown)} unknown alert ids: {', '.join(unknown[:20])}",
            )
        )
    return out, len(unassigned)


def _signature(alert: Alert) -> str:
    return (alert.scrubbed_message or alert.type)[:90]


def _r6(sub: Submission, inp: VerifierInputs) -> tuple[list[Check], Grouping]:
    """Feedback names the conflict, never the answer: no ground-truth ids and no expected device."""
    truth = inp.ground_truth
    pred_label = {a: inc.id for inc in sub.incidents for a in inc.alert_ids}
    if truth is None:
        return [], Grouping()
    true_label: dict[str, str | None] = {a: None for a in truth.noise_alert_ids}
    for t in truth.incidents:
        true_label.update({a: t.id for a in t.alert_ids})
    grouping = grouping_metrics(
        pred_label,
        true_label,
        sum(1 for a in inp.alerts if a not in pred_label and a not in sub.noise_alert_ids),
    )
    out: list[Check] = []
    if not sub.incidents or not truth.incidents:
        if truth.incidents:
            out.append(
                Check(
                    rule=RULES[5],
                    passed=False,
                    detail="no incidents submitted, but the alerts are not all noise",
                )
            )
        return out, grouping

    pred_sets = [set(i.alert_ids) for i in sub.incidents]
    true_sets = [set(t.alert_ids) for t in truth.incidents]
    jaccard = np.array(
        [[len(p & t) / len(p | t) if p | t else 0.0 for t in true_sets] for p in pred_sets], dtype=float
    )
    rows, cols = linear_sum_assignment(-jaccard)
    matched = dict(zip(rows.tolist(), cols.tolist(), strict=True))

    for i, inc in enumerate(sub.incidents):
        p = pred_sets[i]
        if not p:  # R5 already says it is empty; there is nothing here to compare
            continue
        mixed = [true_sets[j] & p for j in range(len(true_sets)) if true_sets[j] & p]
        noise_in = p & set(truth.noise_alert_ids)
        if len(mixed) > 1:
            samples = [inp.alerts[sorted(m)[0]] for m in sorted(mixed, key=len, reverse=True)[:3]]
            examples = "; ".join(f"{s.id} ({_signature(s)})" for s in samples)
            out.append(
                Check(
                    rule=RULES[5],
                    passed=False,
                    detail=f"{inc.id} merges alerts with different failure signatures. Compare: {examples}",
                    incident_id=inc.id,
                )
            )
        elif noise_in and len(noise_in) / max(len(p), 1) > 1 - JACCARD_MIN:
            sample = inp.alerts[sorted(noise_in)[0]]
            out.append(
                Check(
                    rule=RULES[5],
                    passed=False,
                    detail=f"{inc.id} includes {len(noise_in)} alerts unrelated to its cause, e.g. {sample.id} ({_signature(sample)})",
                    incident_id=inc.id,
                )
            )
        j = matched.get(i)
        if j is None:
            if not mixed:
                out.append(
                    Check(
                        rule=RULES[5],
                        passed=False,
                        detail=f"{inc.id} does not look like a real incident",
                        incident_id=inc.id,
                    )
                )
            continue
        if jaccard[i, j] < JACCARD_MIN and len(mixed) <= 1:
            missing = true_sets[j] - p
            elsewhere = Counter(pred_label.get(a, "noise") for a in missing)
            where = ", ".join(f"{k} ({v})" for k, v in elsewhere.most_common(3))
            sample = inp.alerts[sorted(missing)[0]] if missing else None
            hint = f"e.g. {sample.id} ({_signature(sample)})" if sample else ""
            out.append(
                Check(
                    rule=RULES[5],
                    passed=False,
                    detail=f"{inc.id} is incomplete: {len(missing)} alerts with the same cause were placed in {where}, {hint}",
                    incident_id=inc.id,
                )
            )
        if inc.root_cause_device_id != truth.incidents[j].root_cause_device_id:
            out.append(
                Check(
                    rule=RULES[5],
                    passed=False,
                    detail=f"the root-cause device of {inc.id} does not fit the evidence. Trace the path with ping_sim and check the upstream logs",
                    incident_id=inc.id,
                )
            )
    if len(sub.incidents) < len(truth.incidents):
        unmatched = [j for j in range(len(true_sets)) if j not in matched.values()]
        for j in unmatched:
            missed = sorted(true_sets[j])
            if not missed:
                continue
            sample = inp.alerts[missed[0]]
            out.append(
                Check(
                    rule=RULES[5],
                    passed=False,
                    detail=f"{len(missed)} related alerts have no incident of their own, e.g. {sample.id} ({_signature(sample)})",
                )
            )
    return out, grouping


def _r7(sub: Submission, inp: VerifierInputs) -> list[Check]:
    out = []
    truth = inp.ground_truth
    pred_sets = [set(i.alert_ids) for i in sub.incidents]
    for i, inc in enumerate(sub.incidents):
        fix = inc.proposed_fix_id
        if fix == ESCALATE:
            continue
        if fix not in inp.allowed_fix_ids:
            out.append(
                Check(rule=RULES[6], passed=False, detail=f"{fix} is not approved", incident_id=inc.id)
            )
            continue
        if truth is None or not truth.incidents:
            continue
        overlaps = [len(pred_sets[i] & set(t.alert_ids)) for t in truth.incidents]
        best = truth.incidents[int(np.argmax(overlaps))]
        if max(overlaps) and fix not in best.acceptable_fix_ids:
            out.append(
                Check(
                    rule=RULES[6],
                    passed=False,
                    detail=f"{fix} is approved but does not address the cause of {inc.id}",
                    incident_id=inc.id,
                )
            )
    return out


def _r8(sub: Submission, inp: VerifierInputs) -> list[Check]:
    out = []

    def fail(inc, detail) -> None:
        out.append(Check(rule=RULES[7], passed=False, detail=detail, incident_id=inc.id))

    for inc in sub.incidents:
        ticket = inp.tickets.get(inc.ticket_id or "")
        email = inp.emails.get(inc.email_id or "")
        if ticket is None:
            fail(inc, f"{inc.id} has no ticket draft (ticket_id {inc.ticket_id!r})")
        else:
            citations = [
                c for c in ticket.get("citations") or [] if c in inp.logs and c in inp.retrieved_log_ids
            ]
            if len(set(citations)) < 2:
                fail(
                    inc,
                    f"the ticket for {inc.id} must cite at least 2 retrieved logs, and cites {len(set(citations))}",
                )
        if email is None:
            fail(inc, f"{inc.id} has no client email draft (email_id {inc.email_id!r})")
            continue
        body = f"{email.get('subject', '')}\n{email.get('body', '')}"
        kinds = find_unscrubbed(body)
        if kinds:
            fail(inc, f"the email for {inc.id} contains raw PII ({', '.join(kinds)})")
        if LOG_ID.search(body):
            fail(inc, f"the email for {inc.id} contains log ids; a client should not see them")
        words = len(str(email.get("body", "")).split())
        if words > EMAIL_MAX_WORDS:
            fail(inc, f"the email for {inc.id} has {words} words; the limit is {EMAIL_MAX_WORDS}")
        jargon = JARGON.search(body)
        if jargon:
            fail(inc, f"the email for {inc.id} contains jargon ({jargon.group()}); use plain English")
        unknown = sorted(t for t in TOKEN.findall(body) if t not in inp.known_pii_tokens)
        if unknown:
            fail(inc, f"the email for {inc.id} uses placeholders that do not exist: {', '.join(unknown[:5])}")
    return out


def verify(sub: Submission, inp: VerifierInputs) -> VerifierResult:
    r6_checks, grouping = _r6(sub, inp)
    r5_checks, unassigned = _r5(sub, inp)
    grouping.unassigned_alerts = unassigned
    failed = (
        _r1(sub)
        + _r2(sub, inp)
        + _r3(sub, inp)
        + _r4(sub, inp)
        + r5_checks
        + r6_checks
        + _r7(sub, inp)
        + _r8(sub, inp)
    )
    failed_rules = {c.rule for c in failed}
    passed = [Check(rule=rule, passed=True) for rule in RULES if rule not in failed_rules]
    if inp.ground_truth is None:
        passed = [c for c in passed if c.rule != RULES[5]]  # production mode: R6 is off, not green
    checks = sorted(passed + failed, key=lambda c: (RULES.index(c.rule), c.passed))
    return VerifierResult(
        run_id=inp.run_id,
        attempt=inp.attempt,
        passed=not failed,
        checks=checks,
        grouping=grouping,
        feedback_for_agent=_feedback(failed),
    )


def _feedback(failed: list[Check], max_lines: int = 25) -> str:
    if not failed:
        return ""
    lines = [f"- {c.rule}: {c.detail}" for c in failed[:max_lines]]
    if len(failed) > max_lines:
        lines.append(f"- and {len(failed) - max_lines} more of the same kinds")
    return "Fix:\n" + "\n".join(lines)
