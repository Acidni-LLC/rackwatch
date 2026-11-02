// A real seven-segment readout: lit segments over ghost segments, like the LED counters on rack gear.

const SEGMENTS: Record<string, string> = {
  "0": "abcdef",
  "1": "bc",
  "2": "abged",
  "3": "abgcd",
  "4": "fgbc",
  "5": "afgcd",
  "6": "afgedc",
  "7": "abc",
  "8": "abcdefg",
  "9": "abcdfg",
  "-": "g",
  " ": "",
  E: "afged",
  F: "afge",
};

const POLY: Record<string, string> = {
  a: "12,4 48,4 42,10 18,10",
  b: "50,6 50,44 44,38 44,12",
  c: "50,50 50,88 44,82 44,56",
  d: "12,90 48,90 42,84 18,84",
  e: "10,50 10,88 16,82 16,56",
  f: "10,6 10,44 16,38 16,12",
  g: "14,47 20,42 40,42 46,47 40,52 20,52",
};

function Digit({ ch, color, ghost }: { ch: string; color: string; ghost: string }) {
  const lit = SEGMENTS[ch] ?? "";
  return (
    <svg viewBox="0 0 60 94" style={{ height: "100%", width: "auto", display: "block", transform: "skewX(-6deg)" }} aria-hidden>
      {Object.entries(POLY).map(([segment, points]) => (
        <polygon
          key={segment}
          points={points}
          fill={lit.includes(segment) ? color : ghost}
          style={lit.includes(segment) ? { filter: `drop-shadow(0 0 3px ${color}aa)` } : undefined}
        />
      ))}
    </svg>
  );
}

export function SevenSeg({
  value,
  height = 44,
  color = "#ffb000",
  ghost = "#1f2224",
  label,
}: {
  value: string;
  height?: number;
  color?: string;
  ghost?: string;
  label?: string;
}) {
  const parts = value.split("");
  return (
    <span
      role="img"
      aria-label={label ?? value}
      style={{ display: "inline-flex", alignItems: "flex-end", height, gap: height * 0.04, background: "#07080a", padding: `${height * 0.12}px ${height * 0.18}px`, boxSizing: "content-box", border: "1px solid #24272b", boxShadow: "inset 0 2px 6px #000" }}
    >
      {parts.map((ch, i) => {
        if (ch === "." || ch === ":") {
          return (
            <span
              key={i}
              style={{
                width: height * 0.09,
                height: ch === "." ? height * 0.09 : height * 0.5,
                marginBottom: ch === "." ? 0 : height * 0.25,
                backgroundImage: ch === ":" ? `radial-gradient(circle, ${color} 45%, transparent 50%)` : undefined,
                backgroundSize: ch === ":" ? `${height * 0.09}px ${height * 0.25}px` : undefined,
                backgroundColor: ch === "." ? color : undefined,
                boxShadow: ch === "." ? `0 0 3px ${color}` : undefined,
              }}
            />
          );
        }
        return <Digit key={i} ch={ch} color={color} ghost={ghost} />;
      })}
    </span>
  );
}
