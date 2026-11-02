import type { CallRecord } from "../api";
import { compact, usd } from "../api";

const SHORT: Record<string, string> = {
  "muse-spark-1.3": "spark-1.3",
  "meta-models/muse-spark-open": "spark-open (open weights)",
  "accounts/fireworks/models/muse-spark-open": "spark-open (open weights)",
  "sam-3.1": "sam-3.1",
  "muse-voice-transcribe-1.0": "voice-1.0",
};

export function BudgetBar({ spent, limit, label }: { spent: number; limit: number; label: string }) {
  const pct = Math.min(100, (spent / limit) * 100);
  const color = pct > 85 ? "var(--color-alarm)" : pct > 60 ? "var(--color-amber)" : "var(--color-go)";
  return (
    <div>
      <div className="flex justify-between font-mono text-[11px] text-dim">
        <span>{label}</span>
        <span>
          {usd(spent)} / {usd(limit, 2)}
        </span>
      </div>
      <div className="mt-1 h-1.5 bg-black">
        <div style={{ width: `${pct}%`, background: color, height: "100%", transition: "width 300ms" }} />
      </div>
    </div>
  );
}

// The visible proof that every model call is Meta's. One row per request, newest first.
export function CallLog({ calls, limit }: { calls: CallRecord[]; limit: number }) {
  const total = calls.reduce((sum, c) => sum + c.est_cost_usd, 0);
  const ordered = [...calls].reverse();
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex-none border-b border-seam px-3 py-2">
        <BudgetBar spent={total} limit={limit} label={`${calls.length} calls this run · est.`} />
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto">
        {ordered.map((c) => (
          <li key={c.id} className="row-in border-b border-seam/50 px-3 py-1.5 font-mono text-[11.5px] leading-snug">
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate text-ink">
                {c.purpose}
                {c.effort ? <span className="text-faint"> {c.effort}</span> : null}
              </span>
              <span className={c.status === "ok" ? "text-dim" : "text-alarm"}>{c.status === "ok" ? usd(c.est_cost_usd, 4) : c.status}</span>
            </div>
            <div className="flex items-baseline justify-between gap-2 text-faint">
              <span className="truncate">{SHORT[c.model] ?? c.model}</span>
              <span className="flex-none">
                {c.tokens_in ? `${compact(c.tokens_in)}↑ ${compact(c.tokens_out)}↓ ` : ""}
                {(c.latency_ms / 1000).toFixed(1)}s{c.diverged ? " diverged" : c.replayed ? " replay" : ""}
              </span>
            </div>
          </li>
        ))}
        {!calls.length ? <li className="px-3 py-3 font-mono text-[12px] text-faint">No calls yet.</li> : null}
      </ul>
    </div>
  );
}
