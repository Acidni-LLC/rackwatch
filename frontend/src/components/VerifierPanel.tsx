import { useState } from "react";
import type { VerifierResult } from "../api";
import { Lamp } from "./bits";

const RULES: [string, string][] = [
  ["R1_citations_exist", "Cited"],
  ["R2_citations_real", "Real logs"],
  ["R3_citation_relevance", "Relevant"],
  ["R4_device_resolves", "Device + U"],
  ["R5_coverage", "Coverage"],
  ["R6_grouping_vs_truth", "Grouping"],
  ["R7_fix_allowed", "Fix approved"],
  ["R8_drafts_clean", "Drafts clean"],
];

// Eight lamps per attempt. The checks are plain Python: the agent can fail them but cannot talk to them.
export function VerifierPanel({ results, running }: { results: VerifierResult[]; running: boolean }) {
  const [openAttempt, setOpenAttempt] = useState<number | null>(null);
  const shown = results.length ? results : [null];
  return (
    <div className="px-3 py-2">
      {shown.map((result, row) => {
        const failed = result?.checks.filter((c) => !c.passed) ?? [];
        const expanded = result && (openAttempt === result.attempt || (openAttempt === null && !result.passed && row === results.length - 1));
        return (
          <div key={result?.attempt ?? "waiting"} className="py-1">
            <button
              type="button"
              disabled={!result}
              onClick={() => result && setOpenAttempt(openAttempt === result.attempt ? -1 : result.attempt)}
              className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-left"
            >
              <span className="w-[62px] flex-none font-mono text-[11px] tracking-widest text-faint uppercase">{result ? `Try ${result.attempt}` : "Waiting"}</span>
              {RULES.map(([rule, label]) => {
                const checks = result?.checks.filter((c) => c.rule === rule) ?? [];
                const bad = checks.some((c) => !c.passed);
                const state = !result || !checks.length ? "off" : bad ? "alarm" : "go";
                return (
                  <span key={rule} className="flex items-center gap-1.5 font-mono text-[11.5px]" title={rule}>
                    <Lamp state={state} pulse={!result && running} />
                    <span className={state === "alarm" ? "text-alarm" : state === "go" ? "text-ink" : "text-faint"}>{label}</span>
                  </span>
                );
              })}
            </button>
            {expanded && failed.length ? (
              <ul className="mt-1.5 ml-[74px] space-y-0.5 font-mono text-[11.5px] leading-snug text-alarm">
                {failed.slice(0, 8).map((check, i) => (
                  <li key={i}>{check.detail}</li>
                ))}
                {failed.length > 8 ? <li className="text-faint">and {failed.length - 8} more</li> : null}
              </ul>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
