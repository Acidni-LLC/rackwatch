import { Fragment, useEffect, useState } from "react";
import type { CallRecord, Status } from "../api";
import { clock, compact, get, usd } from "../api";
import { BudgetBar } from "../components/CallLog";

// Every model request this install has made, with no keys and no prompts stored.
export default function Calls({ status }: { status: Status | null }) {
  const [calls, setCalls] = useState<CallRecord[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    const load = () => get<CallRecord[]>("/api/calls?limit=500").then(setCalls);
    load();
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      <div className="chassis flex flex-none flex-wrap items-center justify-between gap-6 px-4 py-3">
        <p className="text-[18px] font-bold">Every model call is Meta's. Here they all are.</p>
        <div className="w-[320px] max-w-full">{status ? <BudgetBar spent={status.budget.spent} limit={status.budget.total} label="Total live spend, est." /> : null}</div>
      </div>
      <div className="chassis min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-left font-mono text-[12px]">
          <thead className="sticky top-0 bg-panel text-[11px] tracking-wider text-faint uppercase">
            <tr>
              {["Time", "Model", "Purpose", "Effort", "In", "Cached", "Out", "Latency", "Cost", "Status"].map((h) => (
                <th key={h} className="border-b border-seam px-2 py-1.5 font-normal">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {calls.map((c) => (
              <Fragment key={c.id}>
                <tr className="cursor-pointer border-b border-seam/40 hover:bg-raised" onClick={() => setOpen(open === c.id ? null : c.id)}>
                  <td className="px-2 py-1 text-dim">{clock(c.ts)}</td>
                  <td className="px-2 py-1">{c.model}</td>
                  <td className="px-2 py-1">{c.purpose}</td>
                  <td className="px-2 py-1 text-amber">{c.effort ?? "–"}</td>
                  <td className="px-2 py-1">{compact(c.tokens_in)}</td>
                  <td className="px-2 py-1">{compact(c.tokens_cached)}</td>
                  <td className="px-2 py-1">{compact(c.tokens_out)}</td>
                  <td className="px-2 py-1">{(c.latency_ms / 1000).toFixed(1)}s</td>
                  <td className="px-2 py-1">{usd(c.est_cost_usd, 4)}</td>
                  <td className={`px-2 py-1 ${c.status === "ok" ? "text-dim" : "text-alarm"}`}>
                    {c.status}
                    {c.replayed ? " · replayed" : ""}
                  </td>
                </tr>
                {open === c.id ? (
                  <tr>
                    <td colSpan={10} className="bg-black px-3 py-2 whitespace-pre-wrap text-dim">
                      {c.response_excerpt || "(no text)"}
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            ))}
          </tbody>
        </table>
        {!calls.length ? <p className="p-3 text-[13px] text-faint">No calls yet.</p> : null}
      </div>
    </div>
  );
}
