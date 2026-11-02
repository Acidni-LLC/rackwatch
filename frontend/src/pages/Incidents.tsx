import { useEffect, useMemo, useState } from "react";
import type { AlertRow, AlertsResponse, Device, RackState, Status } from "../api";
import { get, go } from "../api";
import { CallLog } from "../components/CallLog";
import { Btn, ErrorLine, Lamp, Section } from "../components/bits";
import { SevenSeg } from "../components/SevenSeg";
import { TileField } from "../components/TileField";
import { Timeline } from "../components/Timeline";
import { VerifierPanel } from "../components/VerifierPanel";
import { useRun } from "../hooks/useRun";

function elapsed(from: number | null, to: number | null, now: number): string {
  if (!from) return "00:00";
  const seconds = Math.max(0, Math.round(((to ?? now) - from) / 1000));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

// The hero screen. Left: what the agent is doing. Centre: the alerts collapsing. Right: every Meta call it made.
export default function Incidents({ status, runId }: { status: Status | null; runId: string | null }) {
  const [latest, setLatest] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (runId) return;
    get<{ id: string }[]>("/api/runs?kind=triage&limit=1")
      .then((rows) => {
        setLatest(rows[0]?.id ?? null);
        setChecked(true);
      })
      .catch(() => setChecked(true));
  }, [runId]);

  const activeId = runId ?? latest;
  const run = useRun(activeId);
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    get<AlertsResponse>("/api/alerts").then((d) => setAlerts(d.alerts));
    get<RackState>("/api/rack").then((d) => setDevices(d.cmdb));
  }, []);

  useEffect(() => {
    if (run.status !== "running") return;
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, [run.status]);

  const hostnames = useMemo(() => Object.fromEntries(devices.map((d) => [d.id, { hostname: d.hostname, u: d.u_start === d.u_end ? `U${d.u_start}` : `U${d.u_start}-${d.u_end}` }])), [devices]);

  if (!activeId) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        {checked ? (
          <div className="chassis max-w-md p-6">
            <p className="text-[18px] font-bold">No triage has run yet.</p>
            <p className="mt-1 text-[14px] text-dim">Start one from the alert wall.</p>
            <div className="mt-4">
              <Btn tone="amber" onClick={() => go("/alerts")}>
                Go to alerts
              </Btn>
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  const submission = run.submissions[run.submissions.length - 1];
  const verdict = run.verifier[run.verifier.length - 1] ?? null;
  const incidentCount = submission?.incidents.length ?? 0;
  const running = run.status === "running";

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(220px,260px)_minmax(0,1fr)_minmax(220px,250px)] grid-rows-[auto_minmax(0,1fr)] gap-2 p-2">
      <div className="chassis col-span-3 flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5">
        <div className="flex items-center gap-4">
          <SevenSeg value={String(alerts.length).padStart(3, "0")} height={58} color="#ff3b30" label={`${alerts.length} alerts`} />
          <span className="text-[28px] leading-none text-dim" aria-hidden>
            →
          </span>
          <SevenSeg value={incidentCount ? String(incidentCount) : "-"} height={58} color={verdict?.passed ? "#4be08a" : "#ffb000"} label={`${incidentCount} incidents`} />
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex items-center gap-2 text-[17px] font-bold">
            <Lamp state={running ? "amber" : run.status === "passed" ? "go" : "alarm"} pulse={running} />
            {running ? (
              <span>
                Attempt {run.attempt || 1} of 3, {run.tools.length} tool calls
              </span>
            ) : run.status === "passed" ? (
              <span>Verified on attempt {run.summary?.attempts}. Waiting for your approval.</span>
            ) : run.status === "failed" ? (
              <span className="text-alarm">The verifier rejected 3 attempts. Escalate to a person.</span>
            ) : run.status === "budget_exceeded" ? (
              <span className="text-alarm">Stopped at the budget cap.</span>
            ) : (
              <span className="text-alarm">The run stopped.</span>
            )}
          </div>
          <span className="font-mono text-[12.5px] text-dim">{elapsed(run.startedAt, run.finishedAt, now)} elapsed</span>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {!running && incidentCount ? <Btn onClick={() => go(`/metrics/${activeId}`)}>Metrics</Btn> : null}
          <Btn onClick={() => go("/alerts")}>New run</Btn>
        </div>
      </div>

      <Section tag="Agent" className="min-h-0">
        <Timeline run={run} />
      </Section>

      <div className="flex min-h-0 flex-col gap-2">
        <ErrorLine message={run.error} />
        <section className="chassis flex-none">
          <VerifierPanel results={run.verifier} running={running} />
        </section>
        <section className="chassis min-h-0 flex-1 overflow-hidden p-3">
          <TileField alerts={alerts} incidents={submission?.incidents} verdict={verdict} runId={activeId} hostnames={hostnames} />
        </section>
      </div>

      <Section tag="Meta calls" className="min-h-0">
        <CallLog calls={run.calls} limit={status?.budget.per_run ?? 1.5} />
      </Section>
    </div>
  );
}
