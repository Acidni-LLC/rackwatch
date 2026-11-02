// The backend contract, typed by hand from backend/app/schemas.py and main.py.

export type Severity = "info" | "warning" | "critical";

export interface Status {
  mode: "live" | "record" | "replay";
  key: { state: "ok" | "none" | "error"; models: string[] };
  budget: { spent: number; total: number; per_run: number };
  dataset_loaded: boolean;
  alerts: number;
  devices: number;
  pii_tokens: number;
  cassettes: string[];
  models: { spark: string; sam: string; asr: string; spark_open: string };
  open_provider: string;
  open_weight_key: boolean;
}

export interface BBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface Device {
  id: string;
  rack_id: string;
  u_start: number;
  u_end: number;
  device_type: string;
  make: string | null;
  model: string | null;
  serial: string | null;
  hostname: string | null;
  mgmt_ip: string | null;
  uplink_device_id: string | null;
  confidence: number;
  field_confidence: Record<string, number>;
  bbox: BBox | null;
  source_image: string | null;
  source: "vision" | "seed" | "manual";
  needs_review: boolean;
}

export interface RackState {
  scanned: boolean;
  devices: Device[];
  cmdb: Device[];
  image_url: string | null;
  image_w: number | null;
  image_h: number | null;
  total_u: number;
  overlay_url: string | null;
  accuracy: {
    devices_matched: number;
    devices_total: number;
    serial_matched: number;
    u_matched: number;
  } | null;
  run_id: string | null;
}

export interface AlertRow {
  id: string;
  ts: string;
  source: string;
  client_id: string;
  device_hint: string;
  device_id: string | null;
  severity: Severity;
  type: string;
  had_pii: boolean;
  text: string;
}

export interface AlertsResponse {
  total: number;
  alerts: AlertRow[];
  pii: { alerts_with_pii: number; tokens: number; by_kind: Record<string, number> };
}

export interface CallRecord {
  id: string;
  run_id: string | null;
  ts: string;
  model: string;
  endpoint: string;
  purpose: string;
  effort: string | null;
  latency_ms: number;
  tokens_in: number;
  tokens_cached: number;
  tokens_out: number;
  est_cost_usd: number;
  status: string;
  response_excerpt: string;
  replayed: boolean | number;
  diverged: boolean | number;
}

export interface Check {
  rule: string;
  passed: boolean;
  detail: string;
  incident_id: string | null;
}

export interface Grouping {
  precision: number;
  recall: number;
  f1: number;
  ari: number;
  unassigned_alerts: number;
}

export interface VerifierResult {
  run_id: string;
  attempt: number;
  passed: boolean;
  checks: Check[];
  grouping: Grouping;
  feedback_for_agent: string;
}

export interface SubmittedIncident {
  id: string;
  title: string;
  root_cause_device_id: string;
  root_cause: string;
  alert_ids: string[];
  proposed_fix_id: string;
}

export interface Submission {
  attempt: number;
  incidents: SubmittedIncident[];
  noise_alert_ids: string[];
}

export interface Claim {
  id: string;
  text: string;
  citations: string[];
  kind: string;
}

export interface Ticket {
  ticket_id: string;
  incident_id: string;
  title: string;
  priority: string;
  summary: string;
  affected_device_ids: string[];
  steps: string[];
  citations: string[];
  work_log: string[];
}

export interface ClientEmail {
  email_id: string;
  incident_id: string;
  subject: string;
  body: string;
  tone: string;
}

export interface VoiceNote {
  id: string;
  ts: string;
  transcript: string;
  duration_ms: number | null;
  work_log_line: string | null;
}

export interface LogLine {
  id: string;
  ts: string;
  device_id: string;
  facility: string;
  level: string;
  line: string;
}

export interface Incident {
  id: string;
  run_id: string;
  title: string;
  root_cause_device_id: string | null;
  root_cause: string;
  alert_ids: string[];
  claims: Claim[];
  proposed_fix_id: string;
  ticket: Ticket | null;
  client_email: ClientEmail | null;
  status: "draft" | "verified" | "failed" | "approved" | "escalated";
  voice_notes: VoiceNote[];
  confidence: number | null;
  escalation_reason: string | null;
}

export interface IncidentDetail extends Incident {
  logs: LogLine[];
  root_cause_device: Device | null;
  rack: RackState;
}

export interface RunDetail {
  run: {
    id: string;
    kind: string;
    mode: string;
    status: string;
    started_at: string;
    finished_at: string | null;
    attempts: number;
    cost_usd: number;
    cassette: string | null;
  };
  verifier: VerifierResult[];
  incidents: Incident[];
}

export interface Metrics {
  ready: boolean;
  run_id: string | null;
  alerts: number;
  pii_alerts: number;
  pii_tokens: number;
  status?: string;
  mode?: string;
  attempts?: number;
  incidents?: number;
  grouping?: Grouping | null;
  cost_usd?: number;
  cost_per_incident?: number | null;
  seconds?: number | null;
  tokens_in?: number;
  tokens_cached?: number;
  tokens_out?: number;
  by_model?: { model: string; calls: number; tokens_in: number; tokens_cached: number; tokens_out: number; cost_usd: number; latency_ms: number }[];
  by_purpose?: { purpose: string; effort: string | null; calls: number; cost_usd: number }[];
}

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // keep the status line
    }
    throw new Error(message);
  }
  return response.json();
}

export const get = <T,>(url: string) => fetch(url).then((r) => handle<T>(r));

export const post = <T,>(url: string, body: unknown = {}) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) =>
    handle<T>(r),
  );

export const patch = <T,>(url: string, body: unknown) =>
  fetch(url, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) =>
    handle<T>(r),
  );

export const postForm = <T,>(url: string, form: FormData) => fetch(url, { method: "POST", body: form }).then((r) => handle<T>(r));

export const usd = (value: number | null | undefined, digits = 3) => (value == null ? "–" : `$${value.toFixed(digits)}`);

export const compact = (value: number | null | undefined) => {
  if (value == null) return "–";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}k`;
  return String(value);
};

export const clock = (ts: string) => ts.slice(11, 19);

export function go(path: string) {
  window.location.hash = path;
}
