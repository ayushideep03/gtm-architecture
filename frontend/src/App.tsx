import { useContext, useEffect, useState } from "react";
import { Overview as OverviewData } from "./api";
import { LiveContext, useData } from "./ui";
import { useAuth } from "./auth";
import Prospects from "./views/Prospects";
import ControlCentre from "./views/ControlCentre";
import Pipeline from "./views/Pipeline";
import Approvals from "./views/Approvals";
import RevOps from "./views/RevOps";
import ProspectDrawer from "./views/ProspectDrawer";
import Dashboard from "./views/Dashboard";
import Campaigns from "./views/Campaigns";
import Accounts from "./views/Accounts";
import Contacts from "./views/Contacts";
import Qualification from "./views/Qualification";
import Research from "./views/Research";
import Drafts from "./views/Drafts";
import FollowUps from "./views/FollowUps";
import Inbox from "./views/Inbox";
import Meetings from "./views/Meetings";
import Integrations from "./views/Integrations";
import Organisation from "./views/Organisation";
import DemoCentre from "./views/DemoCentre";

export type View =
  | "overview" | "campaigns" | "accounts" | "contacts" | "qualification" | "research" | "drafts" | "approvals"
  | "followups" | "inbox" | "meetings" | "prospects" | "pipeline" | "control" | "revops" | "integrations"
  | "organisation" | "demo";

export interface RouteOpts { status?: string; campaign?: string; lead?: string }
export interface Nav {
  go: (v: View, opts?: RouteOpts) => void;
  openLead: (id: string) => void;
  params: URLSearchParams;      // current view's query params (e.g. ?campaign=...)
}

const GROUPS: { label?: string; items: { id: View; label: string; stage?: string; demoOnly?: boolean }[] }[] = [
  { items: [{ id: "overview", label: "Dashboard" }, { id: "demo", label: "Demo Control Centre", demoOnly: true }] },
  { label: "Pipeline", items: [
    { id: "campaigns", label: "Campaigns" },
    { id: "accounts", label: "Target Accounts", stage: "1–2" },
    { id: "contacts", label: "Contacts", stage: "3" },
    { id: "qualification", label: "Qualification", stage: "4" },
    { id: "research", label: "Research", stage: "5" },
    { id: "drafts", label: "AI Drafts", stage: "6" },
    { id: "approvals", label: "Approval Centre", stage: "7" },
    { id: "followups", label: "Follow-ups", stage: "8" },
    { id: "inbox", label: "Inbox", stage: "9–10" },
    { id: "meetings", label: "Meetings", stage: "11" },
  ] },
  { label: "Operations", items: [
    { id: "revops", label: "RevOps", stage: "12" },
    { id: "pipeline", label: "Pipeline Board" },
    { id: "prospects", label: "Lead Intelligence" },
    { id: "control", label: "Workflow Engine" },
  ] },
  { label: "Settings", items: [{ id: "integrations", label: "Integrations" }, { id: "organisation", label: "Organisation & Users" }] },
];
const ALL_VIEWS = GROUPS.flatMap((g) => g.items.map((i) => i.id));

function readHash(): { view: View; status?: string; lead?: string; params: URLSearchParams } {
  const [v, q] = window.location.hash.replace(/^#\/?/, "").split("?");
  const params = new URLSearchParams(q ?? "");
  const view = (ALL_VIEWS.includes(v as View) ? v : "overview") as View;
  return { view, status: params.get("status") ?? undefined, lead: params.get("lead") ?? undefined, params };
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
      const p = new URLSearchParams();
      if (opts?.status) p.set("status", opts.status);
      if (opts?.campaign) p.set("campaign", opts.campaign);
      if (opts?.lead) p.set("lead", opts.lead);
      const q = p.toString();
      window.location.hash = `/${v}${q ? `?${q}` : ""}`;
    },
    params: route.params,
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
  const demoMode = auth.demoLogin || scripted;

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
        <nav className="side-scroll" aria-label="Main">
          {GROUPS.map((g, gi) => (
            <div key={gi} style={{ display: "contents" }}>
              {g.label && <div className="nav-group">{g.label}</div>}
              {g.items.filter((v) => !v.demoOnly || demoMode).map((v) => (
                <button key={v.id} className={`nav ${route.view === v.id ? "active" : ""}`} onClick={() => nav.go(v.id)}
                        aria-current={route.view === v.id ? "page" : undefined}>
                  <span>{v.label}</span>
                  {v.id === "approvals" && pending > 0 ? <span className="badge warn">{pending}</span>
                    : v.stage ? <span className="stage-no">{v.stage}</span> : null}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="side-foot">
          <span>
            <span className={`live-dot ${live.connected ? "" : "off"}`} /> {live.connected ? "Live updates on" : "Live updates offline"}
          </span>
          <span>Engine: {engineLabel(engine)}</span>
          <span>Autonomy: {ov ? (ov.autonomy.paused ? "paused" : "running") : "—"}</span>
          {auth.organisation && <span title="Your organisation (data is isolated per organisation)">{auth.organisation}</span>}
          {auth.required ? (
            <span>{auth.principal} · <span className="badge">{auth.role}</span> · <a href="#" onClick={(e) => { e.preventDefault(); auth.signOut(); }}>sign out</a></span>
          ) : (
            <span title="No OXYGEN_API_KEYS set; allowed only in development/test profiles">Local mode: no sign-in</span>
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
        {route.view === "overview" && <Dashboard nav={nav} />}
        {route.view === "demo" && <DemoCentre nav={nav} />}
        {route.view === "campaigns" && <Campaigns nav={nav} />}
        {route.view === "accounts" && <Accounts nav={nav} />}
        {route.view === "contacts" && <Contacts nav={nav} />}
        {route.view === "qualification" && <Qualification nav={nav} />}
        {route.view === "research" && <Research nav={nav} />}
        {route.view === "drafts" && <Drafts nav={nav} />}
        {route.view === "approvals" && <Approvals nav={nav} />}
        {route.view === "followups" && <FollowUps nav={nav} />}
        {route.view === "inbox" && <Inbox nav={nav} />}
        {route.view === "meetings" && <Meetings nav={nav} />}
        {route.view === "prospects" && <Prospects nav={nav} initialStatus={route.status} />}
        {route.view === "control" && <ControlCentre nav={nav} />}
        {route.view === "pipeline" && <Pipeline nav={nav} />}
        {route.view === "revops" && <RevOps />}
        {route.view === "integrations" && <Integrations nav={nav} />}
        {route.view === "organisation" && <Organisation nav={nav} />}
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
