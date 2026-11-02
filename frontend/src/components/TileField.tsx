import { useEffect, useMemo, useRef, useState } from "react";
import type { AlertRow, SubmittedIncident, VerifierResult } from "../api";
import { go } from "../api";
import { SevenSeg } from "./SevenSeg";

const DEFAULT_TILE = 13;
const HEADER = 74;
const BIN_GAP = 12;
const NOISE_HEADER = 26;

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
  height: number;
  incident: SubmittedIncident | null;
  count: number;
}

// The annunciator: every alert is one lit window. Before triage they fill the field. When the agent submits,
// each tile flies to its incident's tray, and the noise goes dark. With `fill`, tiles grow to cover the space.
export function TileField({
  alerts,
  incidents,
  noiseIds,
  verdict,
  runId,
  hostnames,
  onHover,
  selectedId,
  fill = false,
}: {
  alerts: AlertRow[];
  incidents?: SubmittedIncident[];
  noiseIds?: string[];
  verdict?: VerifierResult | null;
  runId?: string | null;
  hostnames?: Record<string, { hostname: string | null; u: string }>;
  onHover?: (alert: AlertRow | null) => void;
  selectedId?: string | null;
  fill?: boolean;
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
  const { tile, gap } = useMemo(() => {
    if (!fill || !box.width || !box.height || !alerts.length) return { tile: DEFAULT_TILE, gap: 2 };
    // The largest square step that still fits every alert inside the box.
    let step = Math.floor(Math.sqrt((box.width * box.height) / alerts.length));
    while (step > 6 && Math.floor(box.width / step) * Math.floor(box.height / step) < alerts.length) step--;
    const spacing = Math.max(2, Math.round(step * 0.09));
    return { tile: step - spacing, gap: spacing };
  }, [alerts.length, box.height, box.width, fill]);

  const step = tile + gap;
  const columns = Math.max(8, Math.floor((box.width + gap) / step));

  const { placements, bins, height } = useMemo(() => {
    const places = new Map<string, Placement>();
    const binList: Bin[] = [];
    if (!hasBins) {
      alerts.forEach((a, i) => {
        places.set(a.id, { x: (i % columns) * step, y: Math.floor(i / columns) * step, noise: false, binned: false, delay: 0 });
      });
      return { placements: places, bins: binList, height: Math.ceil(alerts.length / columns) * step };
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
        places.set(id, {
          x: (i % columns) * step,
          y: y + HEADER + Math.floor(i / columns) * step,
          noise: false,
          binned: true,
          delay: 40 + (order++ % 37) * 11,
        });
      });
      const binHeight = HEADER + rows * step;
      binList.push({ id: incident.id, y, height: binHeight, incident, count: ids.length });
      y += binHeight + BIN_GAP;
    }
    const noise = alerts.filter((a) => !assigned.has(a.id));
    const noiseRows = Math.max(1, Math.ceil(noise.length / columns));
    noise.forEach((a, i) => {
      places.set(a.id, {
        x: (i % columns) * step,
        y: y + NOISE_HEADER + Math.floor(i / columns) * step,
        noise: true,
        binned: true,
        delay: 20 + (order++ % 29) * 9,
      });
    });
    binList.push({ id: "noise", y, height: NOISE_HEADER + noiseRows * step, incident: null, count: noise.length });
    return { placements: places, bins: binList, height: y + NOISE_HEADER + noiseRows * step };
  }, [alerts, columns, hasBins, incidents, noiseIds, step]);

  const passed = verdict?.passed;

  return (
    <div ref={ref} className="relative w-full" style={fill ? { height: "100%" } : { height }} onMouseLeave={() => onHover?.(null)}>
      {bins.map((bin) =>
        bin.incident ? (
          <button
            key={bin.id}
            type="button"
            onClick={() => runId && go(`/incident/${runId}/${bin.incident!.id}`)}
            className="row-in absolute left-0 right-0 flex items-start justify-between gap-4 border-t text-left"
            style={{ top: bin.y, height: HEADER - 6, borderColor: passed ? "var(--color-go)" : passed === false ? "var(--color-alarm)" : "var(--color-seam)" }}
          >
            <span className="min-w-0 pt-1.5">
              <span className="block truncate text-[15px] font-bold text-ink">{bin.incident.title}</span>
              <span className="mt-1 block truncate font-mono text-[12px] text-dim">
                {hostnames?.[bin.incident.root_cause_device_id]?.hostname ?? bin.incident.root_cause_device_id}{" "}
                {hostnames?.[bin.incident.root_cause_device_id]?.u ?? ""} · {bin.incident.proposed_fix_id}
              </span>
            </span>
            <span className="flex-none pt-1.5">
              <SevenSeg value={String(bin.count)} height={32} color={passed ? "#4be08a" : passed === false ? "#ff3b30" : "#ffb000"} label={`${bin.count} alerts`} />
            </span>
          </button>
        ) : (
          <div key={bin.id} className="absolute left-0 right-0 font-mono text-[11px] uppercase tracking-widest text-faint" style={{ top: bin.y + 4 }}>
            noise · {bin.count} alerts
          </div>
        ),
      )}
      {alerts.map((alert) => {
        const place = placements.get(alert.id);
        if (!place) return null;
        const tone = place.noise ? "is-noise" : place.binned && passed ? "is-resolved" : `is-${alert.severity}`;
        const corner = Math.max(4, Math.round(tile * 0.26));
        return (
          <div
            key={alert.id}
            className={`tile ${tone}`}
            onMouseEnter={() => onHover?.(alert)}
            style={{
              width: tile,
              height: tile,
              transform: `translate(${place.x}px, ${place.y}px)`,
              transitionDelay: `${place.delay}ms`,
              outline: selectedId === alert.id ? "2px solid #fff" : undefined,
            }}
          >
            {alert.had_pii && !place.noise ? <span style={{ position: "absolute", right: 0, top: 0, width: corner, height: corner, background: "#efeee6" }} /> : null}
          </div>
        );
      })}
    </div>
  );
}
