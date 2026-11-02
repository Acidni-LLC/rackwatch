import { useEffect, useMemo, useRef, useState } from "react";
import type { AlertRow, SubmittedIncident, VerifierResult } from "../api";
import { go } from "../api";
import { Lamp } from "./bits";

const HEADER = 62;
const BIN_GAP = 14;
const NOISE_HEADER = 26;
const MIN_TILE = 6;

interface Placement {
  x: number;
  y: number;
  noise: boolean;
  binned: boolean;
  delay: number;
}

interface Bin {
  id: string;
  y: number;
  incident: SubmittedIncident | null;
  count: number;
}

function spacing(step: number) {
  return Math.max(1, Math.round(step * 0.1));
}

function binsHeight(counts: number[], noise: number, width: number, step: number) {
  const columns = Math.max(4, Math.floor(width / step));
  const rows = (n: number) => Math.max(1, Math.ceil(n / columns));
  return counts.reduce((sum, n) => sum + HEADER + rows(n) * step + BIN_GAP, 0) + NOISE_HEADER + rows(noise) * step;
}

// The annunciator: every alert is one lit window. Before triage they fill the field. When the agent submits, each
// tile flies to its incident's tray and the noise goes dark. Tiles always grow to use all the space they have.
// Colour means severity on the wall and "outage" in a tray; green is only ever a verdict, never a device.
export function TileField({
  alerts,
  incidents,
  verdict,
  runId,
  hostnames,
  onHover,
  selectedId,
}: {
  alerts: AlertRow[];
  incidents?: SubmittedIncident[];
  verdict?: VerifierResult | null;
  runId?: string | null;
  hostnames?: Record<string, { hostname: string | null; u: string }>;
  onHover?: (alert: AlertRow | null) => void;
  selectedId?: string | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setBox({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const hasBins = Boolean(incidents && incidents.length);

  const step = useMemo(() => {
    if (!box.width || !box.height || !alerts.length) return 15;
    const known = new Set(alerts.map((a) => a.id));
    const counts = (incidents ?? []).map((i) => i.alert_ids.filter((id) => known.has(id)).length);
    const noise = alerts.length - counts.reduce((a, b) => a + b, 0);
    let candidate = Math.floor(Math.sqrt((box.width * box.height) / alerts.length));
    while (candidate > MIN_TILE) {
      const fits = hasBins
        ? binsHeight(counts, noise, box.width, candidate) <= box.height
        : Math.floor(box.width / candidate) * Math.floor(box.height / candidate) >= alerts.length;
      if (fits) break;
      candidate--;
    }
    return candidate;
  }, [alerts, box.height, box.width, hasBins, incidents]);

  const gap = spacing(step);
  const tile = step - gap;
  const columns = Math.max(4, Math.floor(box.width / step));

  const { placements, bins } = useMemo(() => {
    const places = new Map<string, Placement>();
    const binList: Bin[] = [];
    if (!hasBins) {
      alerts.forEach((a, i) => {
        places.set(a.id, { x: (i % columns) * step, y: Math.floor(i / columns) * step, noise: false, binned: false, delay: 0 });
      });
      return { placements: places, bins: binList };
    }
    const known = new Set(alerts.map((a) => a.id));
    const ordered = [...incidents!].sort((a, b) => b.alert_ids.length - a.alert_ids.length);
    let y = 0;
    let order = 0;
    const assigned = new Set<string>();
    for (const incident of ordered) {
      const ids = incident.alert_ids.filter((id) => known.has(id));
      const rows = Math.max(1, Math.ceil(ids.length / columns));
      ids.forEach((id, i) => {
        assigned.add(id);
        places.set(id, { x: (i % columns) * step, y: y + HEADER + Math.floor(i / columns) * step, noise: false, binned: true, delay: 40 + (order++ % 37) * 11 });
      });
      binList.push({ id: incident.id, y, incident, count: ids.length });
      y += HEADER + rows * step + BIN_GAP;
    }
    const noise = alerts.filter((a) => !assigned.has(a.id));
    noise.forEach((a, i) => {
      places.set(a.id, { x: (i % columns) * step, y: y + NOISE_HEADER + Math.floor(i / columns) * step, noise: true, binned: true, delay: 20 + (order++ % 29) * 9 });
    });
    binList.push({ id: "noise", y, incident: null, count: noise.length });
    return { placements: places, bins: binList };
  }, [alerts, columns, hasBins, incidents, step]);

  const passed = verdict?.passed;
  const rootOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const incident of incidents ?? []) for (const id of incident.alert_ids) map.set(id, incident.root_cause_device_id);
    return map;
  }, [incidents]);

  // Movement means one thing: the agent sorted the alerts. Measuring the field or resizing the window is instant.
  const signature = (incidents ?? []).map((i) => `${i.id}:${i.alert_ids.length}`).join("|");
  const [moving, setMoving] = useState(false);
  const previous = useRef("");
  useEffect(() => {
    if (signature === previous.current) return;
    const firstPaint = previous.current === "" && box.width === 0;
    previous.current = signature;
    if (firstPaint || !signature) return;
    setMoving(true);
    const timer = setTimeout(() => setMoving(false), 1800);
    return () => clearTimeout(timer);
  }, [signature, box.width]);

  return (
    <div ref={ref} className="relative h-full w-full" onMouseLeave={() => onHover?.(null)}>
      {bins.map((bin) =>
        bin.incident ? (
          <button
            key={bin.id}
            type="button"
            onClick={() => runId && go(`/incident/${runId}/${bin.incident!.id}`)}
            className="group row-in absolute right-0 left-0 flex items-center justify-between gap-4 text-left"
            style={{ top: bin.y, height: HEADER - 8 }}
            aria-label={`Review ${bin.incident.title}`}
          >
            <span className="flex min-w-0 items-center gap-3">
              <span className="w-[56px] flex-none text-right font-mono text-[30px] leading-none font-semibold text-alarm tabular-nums">{bin.count}</span>
              <span className="min-w-0">
                <span className="block truncate text-[16px] font-bold text-ink">{bin.incident.title}</span>
                <span className="mt-0.5 block truncate font-mono text-[12.5px] text-dim">
                  {hostnames?.[bin.incident.root_cause_device_id]?.u ?? bin.incident.root_cause_device_id}
                  {bin.incident.proposed_fix_id === "ESCALATE" ? " · no approved fix, needs a person" : ` · ${bin.incident.proposed_fix_id}`}
                </span>
              </span>
            </span>
            {passed ? (
              <span className="flex flex-none items-center gap-2 border border-go px-3 py-1.5 font-mono text-[13px] text-go transition group-hover:bg-go group-hover:text-black">
                <Lamp state="go" size={8} /> Review and approve
              </span>
            ) : passed === false ? (
              <span className="flex flex-none items-center gap-2 font-mono text-[13px] text-alarm">
                <Lamp state="alarm" size={8} /> Rejected
              </span>
            ) : null}
          </button>
        ) : (
          <div key={bin.id} className="absolute right-0 left-0 font-mono text-[12.5px] text-dim" style={{ top: bin.y + 3 }}>
            Noise: {bin.count} alerts, no incident
          </div>
        ),
      )}
      {alerts.map((alert) => {
        const place = placements.get(alert.id);
        if (!place) return null;
        const owner = place.binned && !place.noise ? rootOf.get(alert.id) : undefined;
        const tone = place.noise ? "is-noise" : place.binned ? (owner === alert.device_id ? "is-critical" : "is-downstream") : `is-${alert.severity}`;
        const corner = Math.max(4, Math.round(tile * 0.26));
        return (
          <div
            key={alert.id}
            className={`tile ${tone} ${moving ? "is-moving" : ""}`}
            onMouseEnter={() => onHover?.(alert)}
            style={{
              width: tile,
              height: tile,
              transform: `translate(${place.x}px, ${place.y}px)`,
              transitionDelay: `${place.delay}ms`,
              outline: selectedId === alert.id ? "2px solid #fff" : undefined,
            }}
          >
            {alert.had_pii && !place.binned ? <span style={{ position: "absolute", right: 0, top: 0, width: corner, height: corner, background: "#efeee6" }} /> : null}
          </div>
        );
      })}
    </div>
  );
}
