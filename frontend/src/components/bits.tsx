import type { ReactNode } from "react";

export function Lamp({ state, size = 10, pulse = false, label }: { state: "off" | "go" | "alarm" | "amber"; size?: number; pulse?: boolean; label?: string }) {
  const colors = { off: "#23272a", go: "var(--color-go)", alarm: "var(--color-alarm)", amber: "var(--color-amber)" };
  return (
    <span
      aria-label={label}
      className={pulse ? "pulse" : undefined}
      style={{
        display: "inline-block",
        width: size,
        height: size,
        borderRadius: "50%",
        flex: "none",
        background: colors[state],
        boxShadow: state === "off" ? "inset 0 1px 2px #000" : `0 0 ${size}px ${colors[state]}, inset 0 1px 1px rgb(255 255 255 / 0.4)`,
      }}
    />
  );
}

export function Section({ tag, right, children, className = "" }: { tag: string; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`chassis flex min-h-0 flex-col ${className}`}>
      <header className="flex flex-none items-center justify-between gap-3 border-b border-seam px-3 py-2">
        <span className="font-mono text-[11.5px] tracking-widest text-dim uppercase">{tag}</span>
        {right}
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </section>
  );
}

export function Btn({
  children,
  onClick,
  disabled,
  tone = "plain",
  title,
  href,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  tone?: "plain" | "amber" | "alarm" | "go";
  title?: string;
  href?: string;
}) {
  const tones = {
    plain: "border-seam text-ink hover:border-dim",
    amber: "border-amber bg-amber text-black font-bold hover:brightness-110",
    alarm: "border-alarm text-alarm hover:bg-alarm hover:text-black",
    go: "border-go bg-go text-black font-bold hover:brightness-110",
  };
  const cls = `inline-flex items-center gap-2 border px-3 py-1.5 font-mono text-[13px] transition disabled:opacity-40 ${tones[tone]}`;
  if (href) {
    return (
      <a className={cls} href={href} title={title}>
        {children}
      </a>
    );
  }
  return (
    <button type="button" className={cls} onClick={onClick} disabled={disabled} title={title}>
      {children}
    </button>
  );
}

export function ErrorLine({ message }: { message: string | null }) {
  if (!message) return null;
  return <p className="border border-alarm px-3 py-2 font-mono text-[13px] text-alarm">{message}</p>;
}
