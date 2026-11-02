import { useEffect, useReducer } from "react";
import type { CallRecord, Submission, VerifierResult } from "../api";

export interface ToolEvent {
  attempt: number;
  turn: number;
  name: string;
  args: unknown;
  result: unknown;
}

export interface TurnEvent {
  attempt: number;
  turn: number;
  reasoning_summary: string;
  tool_calls: string[];
  said_text: boolean;
}

export interface RunState {
  runId: string | null;
  status: string;
  mode: string | null;
  engine: string | null;
  attempt: number;
  turnCap: number;
  turns: TurnEvent[];
  tools: ToolEvent[];
  calls: CallRecord[];
  submissions: Submission[];
  verifier: VerifierResult[];
  replans: { attempt: number; feedback: string }[];
  summary: { status: string; attempts: number; cost_usd: number; tokens_in: number; tokens_out: number } | null;
  error: string | null;
  startedAt: number | null;
  finishedAt: number | null;
  scanFinished: boolean;
  lastSeq: number;
}

const initial = (runId: string | null): RunState => ({
  runId,
  status: runId ? "running" : "idle",
  mode: null,
  engine: null,
  attempt: 0,
  turnCap: 24,
  turns: [],
  tools: [],
  calls: [],
  submissions: [],
  verifier: [],
  replans: [],
  summary: null,
  error: null,
  startedAt: null,
  finishedAt: null,
  scanFinished: false,
  lastSeq: 0,
});

type Action = { kind: "reset"; runId: string | null } | { kind: "event"; type: string; event: { seq: number; ts: string; data: any } };

function reducer(state: RunState, action: Action): RunState {
  if (action.kind === "reset") return initial(action.runId);
  const { event, type } = action;
  if (event.seq <= state.lastSeq) return state; // a reconnect replays from the top
  const next: RunState = { ...state, lastSeq: event.seq };
  const data = event.data;
  const at = Date.parse(event.ts);
  switch (type) {
    case "run_started":
      next.startedAt = at;
      next.mode = data.mode;
      break;
    case "attempt":
      next.attempt = data.attempt;
      next.turnCap = data.turn_cap;
      next.engine = data.engine ?? null;
      break;
    case "turn":
      next.turns = [...state.turns, data];
      break;
    case "tool":
      next.tools = [...state.tools, data];
      break;
    case "call":
      next.calls = [...state.calls, data];
      break;
    case "submission":
      next.submissions = [...state.submissions, data];
      break;
    case "verifier":
      next.verifier = [...state.verifier, data];
      break;
    case "replan":
      next.replans = [...state.replans, data];
      break;
    case "scan_finished":
      next.scanFinished = true;
      break;
    case "run_error":
      next.error = data.message;
      break;
    case "run_finished":
      next.status = data.status;
      next.summary = data;
      next.finishedAt = at;
      break;
  }
  return next;
}

const EVENT_TYPES = [
  "run_started",
  "attempt",
  "turn",
  "tool",
  "call",
  "submission",
  "verifier",
  "replan",
  "run_error", // not "error": that name is the EventSource connection event
  "scan_started",
  "scan_finished",
  "run_finished",
];

export function useRun(runId: string | null): RunState {
  const [state, dispatch] = useReducer(reducer, runId, initial);

  useEffect(() => {
    dispatch({ kind: "reset", runId });
    if (!runId) return;
    const source = new EventSource(`/api/runs/${runId}/events`);
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, (message) => {
        dispatch({ kind: "event", type, event: JSON.parse((message as MessageEvent).data) });
        if (type === "run_finished") source.close();
      });
    }
    source.onerror = () => source.close();
    return () => source.close();
  }, [runId]);

  return state;
}
