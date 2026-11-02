import type { Device } from "../api";

const TYPE_COLOR: Record<string, string> = {
  switch: "#3f7fb8",
  server: "#7d8690",
  firewall: "#d8473d",
  storage: "#b7a26a",
  ups: "#6b9e6b",
  pdu: "#8a7a5a",
  patch_panel: "#4a4f55",
  kvm: "#9a6fb0",
};

// A rack elevation drawn from the CMDB. The root-cause device is the one lit unit.
export function RackMini({ devices, highlightId, totalU = 42 }: { devices: Device[]; highlightId?: string | null; totalU?: number }) {
  const unit = 11;
  const width = 190;
  const rail = 22;
  return (
    <svg viewBox={`0 0 ${width} ${totalU * unit + 2}`} className="block w-full" role="img" aria-label="Rack elevation">
      <rect x="0" y="0" width={width} height={totalU * unit + 2} fill="#07080a" />
      {Array.from({ length: totalU }, (_, i) => {
        const u = totalU - i;
        return u % 2 === 0 ? (
          <text key={u} x={rail - 5} y={i * unit + unit - 2} textAnchor="end" fontSize="7.5" fill="#5a5f5a" fontFamily="Chivo Mono, monospace">
            {u}
          </text>
        ) : null;
      })}
      {devices.map((d) => {
        const y = (totalU - d.u_end) * unit + 1;
        const h = (d.u_end - d.u_start + 1) * unit - 1;
        const lit = d.id === highlightId;
        return (
          <g key={d.id}>
            <rect
              x={rail}
              y={y}
              width={width - rail - 2}
              height={h}
              fill={lit ? "#ff3b30" : TYPE_COLOR[d.device_type] ?? "#555"}
              opacity={lit ? 1 : 0.55}
              style={lit ? { filter: "drop-shadow(0 0 4px #ff3b30)" } : undefined}
            />
            <text x={rail + 4} y={y + h / 2 + 3} fontSize="7.5" fontWeight={lit ? 700 : 400} fill={lit ? "#000" : "#dcded6"} fontFamily="Chivo Mono, monospace">
              {d.hostname ?? d.device_type}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
