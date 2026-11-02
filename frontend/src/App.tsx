import { useCallback, useEffect, useState } from "react";
import type { Status } from "./api";
import { get, usd } from "./api";
import { Lamp } from "./components/bits";
import Alerts from "./pages/Alerts";
import Calls from "./pages/Calls";
import IncidentDetail from "./pages/IncidentDetail";
import Incidents from "./pages/Incidents";
import MetricsPage from "./pages/Metrics";
import Rack from "./pages/Rack";

const NAV = [
  { path: "/rack", label: "Rack" },
  { path: "/alerts", label: "Alerts" },
  { path: "/run", label: "Triage" },
  { path: "/metrics", label: "Metrics" },
  { path: "/calls", label: "Call log" },
];

function useHashRoute(): string {
  const read = () => window.location.hash.replace(/^#/, "") || "/alerts";
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

function ModeLamp({ status }: { status: Status | null }) {
  if (!status) return null;
  const live = status.mode !== "replay";
  const keyText = status.key.state === "ok" ? `key ok · ${status.key.models.length}/3 models` : status.key.state === "none" ? "no key · recorded runs only" : "key error";
  return (
    <div className="flex items-center gap-4 font-mono text-[12px]">
      <span className="flex items-center gap-2 border border-seam px-2 py-1">
        <Lamp state={live ? "go" : "off"} />
        <span className={live ? "text-go" : "text-dim"}>{status.mode === "record" ? "RECORD" : live ? "LIVE" : "REPLAY"}</span>
      </span>
      <span className="hidden text-dim lg:inline">{keyText}</span>
      <span className="hidden text-dim xl:inline">
        spent {usd(status.budget.spent, 2)} of {usd(status.budget.total, 0)}
      </span>
    </div>
  );
}

export default function App() {
  const route = useHashRoute();
  const [status, setStatus] = useState<Status | null>(null);

  const refresh = useCallback(() => {
    get<Status>("/api/status").then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 8000);
    return () => clearInterval(timer);
  }, [refresh]);

  const parts = route.split("/").filter(Boolean);
  let page;
  switch (parts[0]) {
    case "rack":
      page = <Rack status={status} />;
      break;
    case "run":
      page = <Incidents status={status} runId={parts[1] ?? null} />;
      break;
    case "incident":
      page = <IncidentDetail status={status} runId={parts[1]} incidentId={parts[2]} />;
      break;
    case "metrics":
      page = <MetricsPage runId={parts[1] ?? null} />;
      break;
    case "calls":
      page = <Calls status={status} />;
      break;
    default:
      page = <Alerts status={status} />;
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* One rack unit of faceplate. */}
      <header className="chassis relative flex flex-none items-center justify-between gap-4 px-4 py-2">
        <div className="flex min-w-0 items-center gap-5">
          <a href="#/alerts" className="engraved flex-none text-[15px]">
            RackWatch
          </a>
          <nav className="flex items-center gap-0.5" aria-label="Sections">
            {NAV.map((item) => {
              const active = `/${parts[0] ?? "alerts"}` === item.path || (parts[0] === "incident" && item.path === "/run");
              return (
                <a key={item.path} href={`#${item.path}`} aria-current={active ? "page" : undefined} className={`border-b-2 px-3 py-1 font-mono text-[13px] transition ${active ? "border-amber text-ink" : "border-transparent text-dim hover:text-ink"}`}>
                  {item.label}
                </a>
              );
            })}
          </nav>
        </div>
        <ModeLamp status={status} />
      </header>
      <main className="min-h-0 flex-1 overflow-hidden">{page}</main>
    </div>
  );
}
