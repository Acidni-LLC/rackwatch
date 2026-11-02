import { useEffect, useRef, useState } from "react";
import type { RunState, ToolEvent } from "../hooks/useRun";

function preview(tool: ToolEvent): string {
  const args = (tool.args ?? {}) as Record<string, unknown>;
  if (tool.name === "submit_incidents") return `${(args.incidents as unknown[] | undefined)?.length ?? 0} incidents`;
  if (tool.name === "draft_ticket" || tool.name === "draft_client_email") return String(args.incident_id ?? "");
  const value = args.query ?? args.target ?? args.name ?? (args.filter ? JSON.stringify(args.filter) : "");
  return String(value ?? "");
}

function ToolRow({ tool }: { tool: ToolEvent }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="row-in">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-baseline gap-2 px-3 py-1 text-left font-mono text-[12px] hover:bg-raised">
        <span className="flex-none text-amber">{tool.name}</span>
        <span className="truncate text-dim">{preview(tool)}</span>
      </button>
      {open ? (
        <pre className="mx-3 mb-2 max-h-56 overflow-auto bg-black p-2 font-mono text-[11px] leading-snug whitespace-pre-wrap text-dim">
          {JSON.stringify(tool.args, null, 1)}
          {"\n→ "}
          {typeof tool.result === "string" ? tool.result : JSON.stringify(tool.result, null, 1)}
        </pre>
      ) : null}
    </li>
  );
}

// Turns and tool calls as they happen, grouped by attempt, so a re-plan is visible as a new block.
export function Timeline({ run }: { run: RunState }) {
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [run.tools.length, run.replans.length]);

  const attempts = Array.from(new Set(run.turns.map((t) => t.attempt).concat(run.attempt ? [run.attempt] : [])));
  return (
    <div className="h-full overflow-y-auto pb-2">
      {attempts.map((attempt) => {
        const replan = run.replans.find((r) => r.attempt === attempt);
        const turns = run.turns.filter((t) => t.attempt === attempt);
        return (
          <div key={attempt}>
            <div className="sticky top-0 z-10 bg-panel px-3 py-1.5 font-mono text-[11px] tracking-widest text-faint uppercase">
              Attempt {attempt} · {turns.length} turn{turns.length === 1 ? "" : "s"}
            </div>
            <ul>
              {turns.map((turn) => (
                <li key={`${attempt}-${turn.turn}`}>
                  {turn.reasoning_summary ? <p className="row-in px-3 py-1 text-[12.5px] leading-snug text-dim italic">{turn.reasoning_summary}</p> : null}
                  <ul>
                    {run.tools
                      .filter((t) => t.attempt === attempt && t.turn === turn.turn)
                      .map((tool, i) => (
                        <ToolRow key={i} tool={tool} />
                      ))}
                  </ul>
                </li>
              ))}
            </ul>
            {replan ? (
              <div className="row-in mx-3 my-2 border-l-2 border-alarm bg-black px-2 py-1.5 font-mono text-[11.5px] leading-snug whitespace-pre-wrap text-alarm">
                {replan.feedback}
              </div>
            ) : null}
          </div>
        );
      })}
      {run.status === "running" && !attempts.length ? <p className="px-3 py-3 font-mono text-[12px] text-faint">Waiting for the first turn.</p> : null}
      <div ref={bottom} />
    </div>
  );
}
