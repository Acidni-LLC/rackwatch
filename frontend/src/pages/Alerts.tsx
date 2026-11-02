import { useEffect, useMemo, useState } from "react";
import type { AlertRow, AlertsResponse, Status } from "../api";
import { clock, get, go, post } from "../api";
import { Btn, ErrorLine } from "../components/bits";
import { SevenSeg } from "../components/SevenSeg";
import { TileField } from "../components/TileField";

// The wall of red. One window per alert, and one decision: start the triage.
export default function Alerts({ status }: { status: Status | null }) {
  const [data, setData] = useState<AlertsResponse | null>(null);
  const [raw, setRaw] = useState(false);
  const [hover, setHover] = useState<AlertRow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [speed, setSpeed] = useState(4);

  useEffect(() => {
    get<AlertsResponse>(`/api/alerts?view=${raw ? "raw" : "scrubbed"}`).then(setData).catch((e) => setError(String(e.message)));
  }, [raw]);

  const counts = useMemo(() => {
    const by = { critical: 0, warning: 0, info: 0 };
    for (const a of data?.alerts ?? []) by[a.severity]++;
    return by;
  }, [data]);

  const liveAvailable = status && status.mode !== "replay";
  const hasRecording = status?.cassettes.includes("triage_pass_after_replan");

  async function start(replay: boolean, strict = false) {
    setStarting(true);
    setError(null);
    try {
      const { run_id } = await post<{ run_id: string }>("/api/triage", { replay, demo_strict: strict, speed: replay ? speed : 1 });
      go(`/run/${run_id}`);
    } catch (e) {
      setError((e as Error).message);
      setStarting(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      <div className="chassis flex flex-none flex-wrap items-center justify-between gap-4 px-4 py-3">
        <div className="flex items-center gap-5">
          <SevenSeg value={String(data?.total ?? 0).padStart(3, "0")} height={54} color="#ff3b30" label={`${data?.total ?? 0} open alerts`} />
          <div>
            <p className="text-[22px] font-bold leading-tight">Open alerts at Acme Dental Group</p>
            <p className="mt-0.5 font-mono text-[13px] text-dim">
              <span className="text-alarm">{counts.critical} critical</span> · <span className="text-amber">{counts.warning} warning</span> · {counts.info} info
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {liveAvailable ? (
            <>
              <Btn tone="amber" disabled={starting} onClick={() => start(false)}>
                Triage live
              </Btn>
              <Btn disabled={starting} onClick={() => start(false, true)} title="First attempt at low effort with a 6-turn cap, which often fails the verifier and forces a re-plan">
                Live, rushed first try
              </Btn>
            </>
          ) : null}
          <Btn tone={liveAvailable ? "plain" : "amber"} disabled={starting || !hasRecording} onClick={() => start(true)} title={hasRecording ? "Replays a recorded run. The tools and the verifier run for real." : "No recording found"}>
            Replay recorded run
          </Btn>
          <button type="button" onClick={() => setSpeed(speed === 4 ? 1 : 4)} className="font-mono text-[12.5px] text-dim hover:text-ink" title="Replay speed">
            ×{speed}
          </button>
        </div>
      </div>
      <ErrorLine message={error} />
      <div className="chassis min-h-0 flex-1 overflow-hidden p-3">{data ? <TileField alerts={data.alerts} onHover={setHover} selectedId={hover?.id} /> : <p className="font-mono text-[13px] text-faint">Loading alerts.</p>}</div>
      <div className="chassis flex flex-none flex-wrap items-center justify-between gap-4 px-4 py-2.5">
        <p className="min-h-[20px] min-w-0 flex-1 truncate font-mono text-[13px]">
          {hover ? (
            <>
              <span className="text-faint">{hover.id}</span> <span className="text-dim">{clock(hover.ts)}</span> <span className={hover.severity === "critical" ? "text-alarm" : hover.severity === "warning" ? "text-amber" : "text-dim"}>{hover.type}</span> <span className="text-dim">{hover.device_hint}</span> {hover.text}
            </>
          ) : (
            <span className="text-faint">Hover a window to read the alert. White corners carry personal data.</span>
          )}
        </p>
        <div className="flex flex-none items-center gap-3 font-mono text-[12px]">
          <span className="text-dim">
            <span className="text-ink">{data?.pii.tokens ?? 0}</span> PII items scrubbed before any model call
          </span>
          <Btn onClick={() => setRaw(!raw)}>{raw ? "Show what the model sees" : "Show raw"}</Btn>
        </div>
      </div>
    </div>
  );
}
