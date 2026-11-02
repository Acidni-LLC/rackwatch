import { useEffect, useState } from "react";
import type { Metrics } from "../api";
import { compact, get, go, usd } from "../api";
import { Btn, Section } from "../components/bits";
import { SevenSeg } from "../components/SevenSeg";

function Readout({ value, label, color }: { value: string; label: string; color?: string }) {
  return (
    <div>
      <SevenSeg value={value} height={40} color={color} label={`${label}: ${value}`} />
      <p className="mt-1.5 font-mono text-[12px] text-dim">{label}</p>
    </div>
  );
}

// Everything on this card is read from the call log and the verifier for one run.
export default function MetricsPage({ runId }: { runId: string | null }) {
  const [m, setM] = useState<Metrics | null>(null);
  useEffect(() => {
    get<Metrics>(`/api/metrics${runId ? `?run_id=${runId}` : ""}`).then(setM);
  }, [runId]);

  if (!m) return <p className="p-4 font-mono text-[13px] text-faint">Loading metrics.</p>;
  if (!m.ready) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="chassis max-w-md p-6">
          <p className="text-[18px] font-bold">Nothing to measure yet.</p>
          <p className="mt-1 text-[14px] text-dim">Run a triage first.</p>
          <div className="mt-4">
            <Btn tone="amber" onClick={() => go("/alerts")}>
              Go to alerts
            </Btn>
          </div>
        </div>
      </div>
    );
  }

  const seconds = m.seconds ?? 0;
  const time = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;
  const f1 = m.grouping ? m.grouping.f1.toFixed(2) : "--";
  const cached = m.tokens_in ? Math.round(((m.tokens_cached ?? 0) / m.tokens_in) * 100) : 0;

  return (
    <div className="h-full overflow-y-auto p-3">
      <section className="chassis px-5 py-5">
        <div className="flex flex-wrap items-center gap-6">
          <SevenSeg value={String(m.alerts)} height={92} color="#5a5f5a" label={`${m.alerts} alerts`} />
          <span className="text-[40px] leading-none text-dim" aria-hidden>
            →
          </span>
          <SevenSeg value={String(m.incidents ?? 0)} height={92} color="#4be08a" label={`${m.incidents} incidents`} />
          <p className="max-w-[26ch] text-[20px] leading-tight font-bold">
            {m.alerts} alerts became {m.incidents} incidents{m.mode === "replay" ? " in this recorded run" : ""}.
          </p>
        </div>
        <div className="mt-6 flex flex-wrap gap-x-10 gap-y-5">
          <Readout value={f1} label="Grouping F1 vs seeded truth" color="#4be08a" />
          <Readout value={String(m.attempts ?? 0)} label="Verifier attempts" />
          <Readout value={time} label={m.mode === "replay" ? "Replay time" : "Wall clock"} />
          <Readout value={`$${(m.cost_usd ?? 0).toFixed(2)}`} label={m.mode === "replay" ? "Est. cost when recorded" : "Est. model cost"} />
          <Readout value={`$${(m.cost_per_incident ?? 0).toFixed(2)}`} label="Est. cost per incident" />
          <Readout value={String(m.pii_tokens)} label="PII items scrubbed" />
        </div>
      </section>

      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Section tag="TOKENS BY MODEL" right={<span className="font-mono text-[12px] text-dim">{cached}% of input served from cache</span>}>
          <table className="w-full text-left font-mono text-[12.5px]">
            <thead className="text-[11px] tracking-wider text-faint uppercase">
              <tr>
                {["Model", "Calls", "In", "Cached", "Out", "Cost"].map((h) => (
                  <th key={h} className="border-b border-seam px-3 py-1.5 font-normal">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {m.by_model?.map((row) => (
                <tr key={row.model} className="border-b border-seam/50">
                  <td className="px-3 py-1.5">{row.model}</td>
                  <td className="px-3 py-1.5">{row.calls}</td>
                  <td className="px-3 py-1.5">{compact(row.tokens_in)}</td>
                  <td className="px-3 py-1.5">{compact(row.tokens_cached)}</td>
                  <td className="px-3 py-1.5">{compact(row.tokens_out)}</td>
                  <td className="px-3 py-1.5">{usd(row.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
        <Section tag="WHERE THE EFFORT WENT">
          <table className="w-full text-left font-mono text-[12.5px]">
            <thead className="text-[11px] tracking-wider text-faint uppercase">
              <tr>
                {["Purpose", "Effort", "Calls", "Cost"].map((h) => (
                  <th key={h} className="border-b border-seam px-3 py-1.5 font-normal">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {m.by_purpose?.map((row, i) => (
                <tr key={i} className="border-b border-seam/50">
                  <td className="px-3 py-1.5">{row.purpose}</td>
                  <td className="px-3 py-1.5 text-amber">{row.effort ?? "–"}</td>
                  <td className="px-3 py-1.5">{row.calls}</td>
                  <td className="px-3 py-1.5">{usd(row.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      </div>
    </div>
  );
}
