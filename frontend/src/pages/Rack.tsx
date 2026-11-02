import { useCallback, useEffect, useRef, useState } from "react";
import type { Device, RackState, Status } from "../api";
import { get, patch, postForm, usd } from "../api";
import { Btn, ErrorLine, Lamp } from "../components/bits";
import { useRun } from "../hooks/useRun";

const TYPE_COLOR: Record<string, string> = {
  switch: "#37a3ff",
  server: "#4be08a",
  firewall: "#ff3b30",
  storage: "#ffb000",
  ups: "#c7e04b",
  pdu: "#e8a05a",
  patch_panel: "#d57bff",
  kvm: "#efeee6",
};

const u = (d: Device) => (d.u_start === d.u_end ? `U${d.u_start}` : `U${d.u_start}-${d.u_end}`);

function EditCell({ device, field, onSaved }: { device: Device; field: "make" | "model" | "serial" | "hostname"; onSaved: () => void }) {
  const value = device[field] ?? "";
  const confidence = device.field_confidence?.[field];
  const low = device.source === "vision" && (value === "" || (confidence !== undefined && confidence < 0.6));
  return (
    <input
      defaultValue={value}
      key={value}
      aria-label={`${field} of ${device.hostname ?? device.id}`}
      className={`w-full border-0 bg-transparent px-1 py-0.5 font-mono text-[12.5px] focus:bg-black ${low ? "text-amber" : "text-ink"}`}
      placeholder="unreadable"
      onClick={(e) => e.stopPropagation()}
      onBlur={(e) => {
        const next = e.target.value.trim();
        if (next !== value) patch(`/api/devices/${device.id}`, { [field]: next || null }).then(onSaved);
      }}
    />
  );
}

// Photo in, CMDB out. Boxes come from SAM 3.1 and the labels from Muse Spark.
export default function Rack({ status }: { status: Status | null }) {
  const [rack, setRack] = useState<RackState | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [localImage, setLocalImage] = useState<string | null>(null);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const run = useRun(runId);

  const load = useCallback(() => {
    get<RackState>("/api/rack").then(setRack).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  useEffect(() => {
    if (run.status !== "running" && run.status !== "idle") load();
  }, [run.status, load]);

  const scanning = run.status === "running" && Boolean(runId);
  const replayOnly = status?.mode === "replay";
  const hasRecording = status?.cassettes.includes("vision_sample_rack");

  async function scan(file?: File, replay = false) {
    setError(null);
    setSelected(null);
    const form = new FormData();
    if (file) form.append("file", file);
    form.append("replay", String(replay));
    form.append("speed", "4");
    try {
      const { run_id } = await postForm<{ run_id: string }>("/api/rack/scan", form);
      if (file && file.type.startsWith("image/")) setLocalImage(URL.createObjectURL(file));
      setRunId(run_id);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const devices = rack?.devices ?? [];
  const boxed = rack?.scanned && !scanning ? devices.filter((d) => d.bbox && d.source !== "seed") : [];
  const accuracy = rack?.accuracy;
  const spent = run.calls.reduce((sum, c) => sum + c.est_cost_usd, 0);
  const image = rack?.scanned && rack.image_url ? rack.image_url : localImage ?? rack?.image_url;
  const width = (rack?.scanned && rack.image_w) || natural?.w || 1;
  const height = (rack?.scanned && rack.image_h) || natural?.h || 1;

  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-3 p-3 lg:grid-cols-[minmax(0,540px)_minmax(0,1fr)]">
      <section className="chassis relative flex min-h-0 flex-col">
        <div className="flex flex-none flex-wrap items-center justify-between gap-2 border-b border-seam px-3 py-2">
          <span className="tag">RACK-A PHOTO</span>
          <div className="flex items-center gap-2">
            <input
              ref={fileInput}
              type="file"
              accept="image/*,video/*"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void scan(file);
              }}
            />
            {!replayOnly ? (
              <>
                <Btn disabled={scanning} onClick={() => fileInput.current?.click()}>
                  Upload photo or video
                </Btn>
                <Btn tone="amber" disabled={scanning} onClick={() => scan()}>
                  Scan live
                </Btn>
              </>
            ) : null}
            <Btn tone={replayOnly ? "amber" : "plain"} disabled={scanning || !hasRecording} onClick={() => scan(undefined, true)} title={hasRecording ? "Replays a recorded scan" : "No recording found"}>
              Replay scan
            </Btn>
          </div>
        </div>
        <div className="relative min-h-0 flex-1 overflow-auto bg-black">
          {image ? (
            <div className="relative mx-auto" style={{ width: "fit-content" }}>
              <img
                src={image}
                alt="Server rack"
                className="block h-auto max-h-[calc(100vh-140px)] w-auto"
                onLoad={(e) => setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
                style={{ filter: scanning ? "brightness(0.55)" : undefined, transition: "filter 300ms" }}
              />
              <svg viewBox={`0 0 ${width} ${height}`} className="absolute inset-0 h-full w-full" aria-label="Detected devices">
                {boxed.map((d) => {
                  const b = d.bbox!;
                  const color = TYPE_COLOR[d.device_type] ?? "#fff";
                  const active = selected === d.id;
                  const label = `${u(d)} ${d.hostname ?? d.device_type}`;
                  return (
                    <g key={d.id} onClick={() => setSelected(active ? null : d.id)} style={{ cursor: "pointer" }} className="row-in">
                      <rect x={b.x1} y={b.y1} width={b.x2 - b.x1} height={b.y2 - b.y1} fill={active ? `${color}33` : "transparent"} stroke={color} strokeWidth={active ? 4 : 2.5} />
                      <rect x={b.x1} y={b.y1 - 20} width={label.length * 9.2 + 10} height={20} fill={color} />
                      <text x={b.x1 + 5} y={b.y1 - 5} fontSize="15" fontWeight="600" fontFamily="Chivo Mono, monospace" fill="#000">
                        {label}
                      </text>
                    </g>
                  );
                })}
              </svg>
            </div>
          ) : null}
          {scanning ? (
            <div className="absolute inset-x-3 top-3 border border-amber bg-black/85 px-3 py-2 font-mono text-[12.5px]">
              <div className="flex items-center gap-2 text-amber">
                <Lamp state="amber" pulse /> Scanning · {run.calls.length} Meta calls · {usd(spent)}
              </div>
              {run.calls.slice(-3).map((c) => (
                <div key={c.id} className="truncate text-dim">
                  {c.model} · {c.purpose}
                  {c.effort ? ` · ${c.effort}` : ""}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </section>

      <section className="chassis flex min-h-0 flex-col">
        <div className="flex flex-none flex-wrap items-center justify-between gap-3 border-b border-seam px-3 py-2">
          <span className="tag">CMDB</span>
          <p className="font-mono text-[13px] text-dim">
            {rack?.scanned && accuracy ? (
              <>
                Accuracy vs seed: <span className="text-ink">{accuracy.devices_matched}/{accuracy.devices_total}</span> devices, serial{" "}
                <span className="text-ink">{accuracy.serial_matched}/{accuracy.devices_total}</span>, U position <span className="text-ink">{accuracy.u_matched}/{accuracy.devices_total}</span>
              </>
            ) : rack?.scanned ? (
              `${devices.length} devices from the photo`
            ) : (
              `${devices.length} devices from the seeded CMDB. Scan the rack to rebuild it from the photo.`
            )}
          </p>
        </div>
        <ErrorLine message={error ?? run.error} />
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full border-collapse text-left">
            <thead className="sticky top-0 z-10 bg-panel font-mono text-[11px] tracking-wider text-faint uppercase">
              <tr>
                {["U", "Type", "Hostname", "Make", "Model", "Serial", "Conf"].map((h) => (
                  <th key={h} className="border-b border-seam px-2 py-1.5 font-normal">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {devices.map((d) => (
                <tr key={d.id} onClick={() => setSelected(selected === d.id ? null : d.id)} className={`cursor-pointer border-b border-seam/50 ${selected === d.id ? "bg-raised" : "hover:bg-raised/60"}`}>
                  <td className="px-2 py-0.5 font-mono text-[12.5px] whitespace-nowrap text-amber">{u(d)}</td>
                  <td className="px-2 py-0.5 font-mono text-[12px] whitespace-nowrap">
                    <span className="mr-1.5 inline-block h-2 w-2" style={{ background: TYPE_COLOR[d.device_type] ?? "#888" }} />
                    {d.device_type.replace("_", " ")}
                  </td>
                  <td className="px-1"><EditCell device={d} field="hostname" onSaved={load} /></td>
                  <td className="px-1"><EditCell device={d} field="make" onSaved={load} /></td>
                  <td className="px-1"><EditCell device={d} field="model" onSaved={load} /></td>
                  <td className="px-1"><EditCell device={d} field="serial" onSaved={load} /></td>
                  <td className={`px-2 py-0.5 font-mono text-[12px] ${d.needs_review ? "text-amber" : "text-dim"}`}>{d.source === "seed" ? "seed" : d.source === "manual" ? "edited" : d.confidence.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
