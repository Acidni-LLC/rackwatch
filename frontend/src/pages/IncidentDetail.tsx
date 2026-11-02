import { useCallback, useEffect, useRef, useState } from "react";
import type { IncidentDetail as Detail, LogLine, Status, VoiceNote } from "../api";
import { clock, get, go, patch, post, postForm } from "../api";
import { Btn, ErrorLine, Lamp, Section } from "../components/bits";
import { RackMini } from "../components/RackMini";

const STATUS_TEXT: Record<string, string> = {
  verified: "Verified. Waiting for your approval.",
  approved: "Approved. Ticket and email are ready to export.",
  escalated: "Escalated to a person.",
  failed: "The verifier rejected this. Escalate it.",
  draft: "Draft. The verifier has not passed it.",
};

function LogDrawer({ log, onClose }: { log: LogLine | null; onClose: () => void }) {
  const [context, setContext] = useState<LogLine[]>([]);
  useEffect(() => {
    if (log) get<{ context: LogLine[] }>(`/api/logs/${log.id}?context=4`).then((r) => setContext(r.context));
  }, [log]);
  if (!log) return null;
  return (
    <div className="chassis fixed inset-x-3 bottom-3 z-30 max-h-[40%] overflow-y-auto p-3" role="dialog" aria-label={`Log line ${log.id}`}>
      <div className="mb-2 flex items-center justify-between">
        <span className="tag">{log.id}</span>
        <Btn onClick={onClose}>Close</Btn>
      </div>
      <ul className="font-mono text-[12.5px] leading-relaxed">
        {context.map((line) => (
          <li key={line.id} className={line.id === log.id ? "bg-raised text-amber" : "text-dim"}>
            <span className="text-faint">{line.id}</span> {clock(line.ts)} {line.device_id} {line.level} {line.line}
          </li>
        ))}
      </ul>
    </div>
  );
}

function VoiceRecorder({ canUpload, onDone, url }: { canUpload: boolean; onDone: (note: VoiceNote) => void; url: string }) {
  const [state, setState] = useState<"idle" | "recording" | "sending">("idle");
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  async function send(form: FormData) {
    setState("sending");
    setError(null);
    try {
      onDone(await postForm<VoiceNote>(url, form));
    } catch (e) {
      setError((e as Error).message);
    }
    setState("idle");
  }

  async function record() {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const media = new MediaRecorder(stream);
      chunks.current = [];
      media.ondataavailable = (event) => chunks.current.push(event.data);
      media.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const form = new FormData();
        form.append("file", new Blob(chunks.current, { type: "audio/webm" }), "note.webm");
        void send(form);
      };
      media.start();
      recorder.current = media;
      setState("recording");
    } catch {
      setError("The browser could not open a microphone.");
    }
  }

  function sample() {
    const form = new FormData();
    form.append("sample", "true");
    void send(form);
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {canUpload ? (
        state === "recording" ? (
          <Btn tone="alarm" onClick={() => recorder.current?.stop()}>
            <Lamp state="alarm" pulse /> Stop and transcribe
          </Btn>
        ) : (
          <Btn disabled={state === "sending"} onClick={record}>
            Record note
          </Btn>
        )
      ) : null}
      <Btn disabled={state !== "idle"} onClick={sample}>
        {canUpload ? "Use sample note" : "Play recorded note"}
      </Btn>
      {state === "sending" ? <span className="font-mono text-[12px] text-amber">Muse Voice Transcribe…</span> : null}
      {error ? <span className="font-mono text-[12px] text-alarm">{error}</span> : null}
    </div>
  );
}

export default function IncidentDetail({ status, runId, incidentId }: { status: Status | null; runId: string; incidentId: string }) {
  const [incident, setIncident] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<LogLine | null>(null);
  const [escalating, setEscalating] = useState(false);
  const [reason, setReason] = useState("");
  const base = `/api/incidents/${runId}/${incidentId}`;

  const load = useCallback(() => {
    get<Detail>(base).then(setIncident).catch((e) => setError(e.message));
  }, [base]);
  useEffect(load, [load]);

  if (error && !incident) return <div className="p-4"><ErrorLine message={error} /></div>;
  if (!incident) return <p className="p-4 font-mono text-[13px] text-faint">Loading incident.</p>;

  const device = incident.root_cause_device;
  const logById = new Map(incident.logs.map((l) => [l.id, l]));
  const decided = incident.status === "approved" || incident.status === "escalated";
  const emailWords = incident.client_email?.body.trim().split(/\s+/).filter(Boolean).length ?? 0;

  async function act(path: string, body?: unknown) {
    setError(null);
    try {
      await post(`${base}/${path}`, body);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const saveTicket = (field: "summary", value: string) => incident.ticket && patch(base, { ticket: { ...incident.ticket, [field]: value } }).then(load);
  const saveEmail = (field: "subject" | "body", value: string) => incident.client_email && patch(base, { client_email: { ...incident.client_email, [field]: value } }).then(load);

  return (
    <div className="h-full overflow-y-auto p-3">
      <div className="chassis mb-3 flex flex-wrap items-center justify-between gap-4 px-4 py-3">
        <div className="min-w-0">
          <button type="button" className="font-mono text-[12px] text-dim hover:text-ink" onClick={() => go(`/run/${runId}`)}>
            ← Back to the run
          </button>
          <h1 className="mt-0.5 text-[24px] leading-tight font-bold">{incident.title}</h1>
          <p className="mt-1 flex items-center gap-2 font-mono text-[13px] text-dim">
            <Lamp state={incident.status === "approved" || incident.status === "verified" ? "go" : incident.status === "escalated" ? "amber" : "alarm"} />
            {STATUS_TEXT[incident.status]} {incident.alert_ids.length} alerts · {incident.proposed_fix_id}
          </p>
          {incident.escalation_reason ? <p className="mt-1 font-mono text-[13px] text-amber">Reason: {incident.escalation_reason}</p> : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {decided ? (
            <>
              <Btn href={`${base}/export/ticket.json`}>Ticket JSON</Btn>
              {incident.status === "approved" ? <Btn href={`${base}/export/email.eml`}>Client email .eml</Btn> : null}
            </>
          ) : (
            <>
              <Btn tone="go" disabled={incident.status !== "verified"} onClick={() => act("approve")}>
                Approve
              </Btn>
              <Btn tone="alarm" onClick={() => setEscalating(!escalating)}>
                Escalate
              </Btn>
            </>
          )}
        </div>
      </div>
      <ErrorLine message={error} />
      {escalating && !decided ? (
        <div className="chassis mb-3 flex flex-wrap items-end gap-2 p-3">
          <label className="min-w-[280px] flex-1 font-mono text-[12px] text-dim">
            Why does a person need to take this?
            <input className="field mt-1" value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <Btn tone="alarm" disabled={!reason.trim()} onClick={() => act("escalate", { reason })}>
            Send to the service manager
          </Btn>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[210px_minmax(0,1.1fr)_minmax(0,1fr)]">
        <Section tag={device ? `${device.rack_id} U${device.u_start === device.u_end ? device.u_start : `${device.u_start}-${device.u_end}`}` : "RACK"}>
          <div className="p-2">
            <RackMini devices={incident.rack.cmdb} highlightId={incident.root_cause_device_id} totalU={incident.rack.total_u} />
            {device ? (
              <p className="mt-2 font-mono text-[11.5px] leading-snug text-dim">
                <span className="text-ink">{device.hostname}</span>
                <br />
                {device.make} {device.model}
                <br />
                S/N {device.serial}
                <br />
                {device.mgmt_ip}
              </p>
            ) : null}
          </div>
        </Section>

        <div className="flex min-w-0 flex-col gap-3">
          <Section tag="ROOT CAUSE AND EVIDENCE">
            <div className="p-3">
              <p className="text-[15px] leading-snug font-bold">{incident.root_cause}</p>
              <ul className="mt-3 space-y-2">
                {incident.claims.map((claim) => (
                  <li key={claim.id} className="border-l-2 border-seam pl-3 text-[14px] leading-snug">
                    {claim.text}{" "}
                    <span className="inline-flex flex-wrap gap-1 align-middle">
                      {claim.citations.map((cite) => (
                        <button key={cite} type="button" className="tag !text-[10px] hover:!bg-amber" onClick={() => logById.get(cite) && setDrawer(logById.get(cite)!)}>
                          {cite}
                        </button>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </Section>
          <Section tag="VOICE NOTES" right={<VoiceRecorder url={`${base}/voice`} canUpload={status?.mode !== "replay"} onDone={load} />}>
            <ul className="p-3">
              {incident.voice_notes.map((note) => (
                <li key={note.id} className="row-in border-l-2 border-amber pl-3 text-[14px] leading-snug">
                  “{note.transcript}”
                  {note.work_log_line ? <p className="mt-1 font-mono text-[12px] text-dim">Work log: {note.work_log_line}</p> : null}
                </li>
              ))}
              {!incident.voice_notes.length ? <li className="font-mono text-[12.5px] text-faint">Onsite? Say what you did and it lands in the ticket.</li> : null}
            </ul>
          </Section>
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          {incident.ticket ? (
            <Section tag={`TICKET ${incident.ticket.ticket_id}`} right={<span className="font-mono text-[12px] text-amber">{incident.ticket.priority}</span>}>
              <div className="space-y-2 p-3">
                <p className="text-[14px] font-bold">{incident.ticket.title}</p>
                <textarea className="field" rows={4} disabled={decided} defaultValue={incident.ticket.summary} onBlur={(e) => saveTicket("summary", e.target.value)} aria-label="Ticket summary" />
                <ol className="list-decimal pl-5 text-[13px] leading-snug text-dim">
                  {incident.ticket.steps.map((step, i) => (
                    <li key={i}>{step}</li>
                  ))}
                </ol>
                {incident.ticket.work_log.length ? (
                  <ul className="border-t border-seam pt-2 font-mono text-[12px] text-dim">
                    {incident.ticket.work_log.map((line, i) => (
                      <li key={i}>{line}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            </Section>
          ) : null}
          {incident.client_email ? (
            <Section tag="EMAIL TO CLIENT" right={<span className="font-mono text-[11px] text-dim">{emailWords} words</span>}>
              <div className="space-y-2 p-3">
                <input className="field" disabled={decided} defaultValue={incident.client_email.subject} onBlur={(e) => saveEmail("subject", e.target.value)} aria-label="Email subject" />
                <textarea className="field" rows={9} disabled={decided} defaultValue={incident.client_email.body} onBlur={(e) => saveEmail("body", e.target.value)} aria-label="Email body" />
              </div>
            </Section>
          ) : null}
        </div>
      </div>
      <LogDrawer log={drawer} onClose={() => setDrawer(null)} />
    </div>
  );
}
