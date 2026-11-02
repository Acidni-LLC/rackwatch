import { useState } from "react";
import type { VerifierResult } from "../api";
import { Lamp } from "./bits";

const RULES: [string, string][] = [
  ["R1_citations_exist", "Cited"],
  ["R2_citations_real", "Real logs"],
  ["R3_citation_relevance", "Relevant"],
  ["R4_device_resolves", "Device, U"],
  ["R5_coverage", "Coverage"],
  ["R6_grouping_vs_truth", "Grouping"],
  ["R7_fix_allowed", "Fix approved"],
  ["R8_drafts_clean", "Drafts clean"],
];

// One lamp matrix: a column per rule, a row per attempt. The checks are plain Python, so the agent can fail them
// but cannot talk to them. A failure stays open only while it is the latest word.
export function VerifierPanel({ results, running }: { results: VerifierResult[]; running: boolean }) {
  const [opened, setOpened] = useState<number | null>(null);
  const rows: (VerifierResult | null)[] = results.length ? results : [null];
  const latest = results[results.length - 1];
  const open = opened ?? (latest && !latest.passed ? latest.attempt : null);
  const shownFailures = results.find((r) => r.attempt === open)?.checks.filter((c) => !c.passed) ?? [];

  return (
    <div className="px-3 py-2">
      <div className="grid items-center gap-x-1 gap-y-1.5" style={{ gridTemplateColumns: "72px repeat(8, minmax(0, 1fr))" }}>
        <span />
        {RULES.map(([rule, label]) => (
          <span key={rule} title={rule} className="truncate text-center font-mono text-[11px] text-dim">
            {label}
          </span>
        ))}
        {rows.map((result) => (
          <div key={result?.attempt ?? "waiting"} className="contents">
            <button
              type="button"
              disabled={!result}
              onClick={() => result && setOpened(open === result.attempt ? -1 : result.attempt)}
              className="text-left font-mono text-[11.5px] text-dim hover:text-ink disabled:hover:text-dim"
            >
              {result ? `Attempt ${result.attempt}` : "Waiting"}
            </button>
            {RULES.map(([rule, label]) => {
              const checks = result?.checks.filter((c) => c.rule === rule) ?? [];
              const bad = checks.some((c) => !c.passed);
              const state = !result || !checks.length ? "off" : bad ? "alarm" : "go";
              return (
                <span key={rule} className="flex justify-center" aria-label={`${label}: ${state === "go" ? "pass" : state === "alarm" ? "fail" : "not run"}`} role="img">
                  <Lamp state={state} size={11} pulse={!result && running} />
                </span>
              );
            })}
          </div>
        ))}
      </div>
      {shownFailures.length ? (
        <ul className="mt-2 border-t border-seam pt-2 font-mono text-[12px] leading-snug text-alarm">
          {shownFailures.slice(0, 6).map((check, i) => (
            <li key={i} className="truncate" title={check.detail}>
              {check.detail}
            </li>
          ))}
          {shownFailures.length > 6 ? <li className="text-dim">and {shownFailures.length - 6} more</li> : null}
        </ul>
      ) : null}
    </div>
  );
}
