import { useContext, useEffect, useState } from "react";
import { Overview as OverviewData } from "./api";
import { LiveContext, useData } from "./ui";
import { useAuth } from "./auth";
import Overview from "./views/Overview";
import Prospects from "./views/Prospects";
import ControlCentre from "./views/ControlCentre";
import Pipeline from "./views/Pipeline";
import Approvals from "./views/Approvals";
import RevOps from "./views/RevOps";
import ProspectDrawer from "./views/ProspectDrawer";

export type View = "overview" | "prospects" | "control" | "pipeline" | "approvals" | "revops";
export interface Nav {
  go: (v: View, opts?: { status?: string }) => void;
  openLead: (id: string) => void;
}

const VIEWS: { id: View; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "prospects", label: "Lead Intelligence" },
  { id: "control", label: "Oxygen Control Centre" },
  { id: "pipeline", label: "Pipeline" },
  { id: "approvals", label: "Approval Centre" },
  { id: "revops", label: "RevOps" },
];

function readHash(): { view: View; status?: string; lead?: string } {
  const [v, q] = window.location.hash.replace(/^#\/?/, "").split("?");
  const params = new URLSearchParams(q ?? "");
  const view = (VIEWS.find((x) => x.id === v)?.id ?? "overview") as View;
  return { view, status: params.get("status") ?? undefined, lead: params.get("lead") ?? undefined };
}

export default function App() {
  const [route, setRoute] = useState(readHash());
  const live = useContext(LiveContext);
  const auth = useAuth();
  const { data: ov } = useData<OverviewData>("/overview");
  useEffect(() => {
    const on = () => setRoute(readHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const nav: Nav = {
    go: (v, opts) => {
      window.location.hash = `/${v}${opts?.status ? `?status=${opts.status}` : ""}`;
    },
    openLead: (id) => {
      const params = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
      params.set("lead", id);
      window.location.hash = `/${route.view}?${params}`;
    },
  };
  const closeLead = () => {
    const params = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
    params.delete("lead");
    const q = params.toString();
    window.location.hash = `/${route.view}${q ? `?${q}` : ""}`;
  };

  const engine = ov?.engine;
  const llmProfiles: any[] = engine?.llm?.profiles ?? [];
  const scripted = llmProfiles.some((p) => p.vendor === "demo" && p.available);
  const pending = ov?.kpis.pending_approvals ?? 0;

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <div>
            <b>Oxygen</b>
            <small>Autonomous Sales OS</small>
          </div>
        </div>
        {VIEWS.map((v) => (
          <button key={v.id} className={`nav ${route.view === v.id ? "active" : ""}`} onClick={() => nav.go(v.id)}>
            <span>{v.label}</span>
            {v.id === "approvals" && pending > 0 && <span className="badge warn">{pending}</span>}
          </button>
        ))}
        <div className="side-foot">
          <span>
            <span className={`live-dot ${live.connected ? "" : "off"}`} /> {live.connected ? "Live updates on" : "Live updates offline"}
          </span>
          <span>Engine: {engineLabel(engine)}</span>
          <span>Autonomy: {ov ? (ov.autonomy.paused ? "paused" : "running") : "—"}</span>
          {auth.required ? (
            <span>Signed in as {auth.principal} · <a href="#" onClick={(e) => { e.preventDefault(); auth.signOut(); }}>sign out</a></span>
          ) : (
            <span title="No OXYGEN_API_KEYS set; allowed only in development/demo profiles">Local mode: no sign-in</span>
          )}
        </div>
      </aside>
      <main className="main">
        {scripted && (
          <div className="banner warn">
            <span>⚠</span>
            <div>
              <b>Demo mode — scripted AI.</b> Decisions, research, drafts and reply classification come from a transparent scripted
              playbook (<span className="mono">scripted-demo-playbook</span>), not a language model. Everything still passes the real
              guardrails, compliance checks and human approvals. Add an OpenAI or Anthropic key and use the development profile for real AI.
            </div>
          </div>
        )}
        {engine && !scripted && engine.effective === "rules_fallback" && (
          <div className="banner info">
            <span>ℹ</span>
            <div>
              <b>No language model configured.</b> Oxygen is using its deterministic rules fallback (enrich → qualify). Set{" "}
              <span className="mono">OPENAI_API_KEY</span> or <span className="mono">ANTHROPIC_API_KEY</span> for the AI Sales Controller.
            </div>
          </div>
        )}
        {ov?.autonomy.paused && (
          <div className="banner bad">
            <span>⏸</span>
            <div>
              <b>Autonomy is paused.</b> No background decisions or task execution until resumed from the Control Centre.
            </div>
          </div>
        )}
        {route.view === "overview" && <Overview nav={nav} />}
        {route.view === "prospects" && <Prospects nav={nav} initialStatus={route.status} />}
        {route.view === "control" && <ControlCentre nav={nav} />}
        {route.view === "pipeline" && <Pipeline nav={nav} />}
        {route.view === "approvals" && <Approvals nav={nav} />}
        {route.view === "revops" && <RevOps />}
      </main>
      {route.lead && <ProspectDrawer leadId={route.lead} onClose={closeLead} />}
    </div>
  );
}

function engineLabel(engine: any): string {
  if (!engine) return "—";
  const p = (engine.llm?.profiles ?? []).find((x: any) => x.available);
  if (p?.vendor === "demo") return "Demo AI (scripted)";
  if (engine.effective === "llm" && p) return `AI · ${p.model}`;
  return "Rules fallback";
}
