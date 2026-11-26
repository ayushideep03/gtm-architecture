import { useState } from "react";
import { ActivityEvent, api, Overview as OverviewData } from "../api";
import type { Nav } from "../App";
import { ago, BarList, ErrorNote, human, useData, useToast } from "../ui";

const KPIS: { key: string; label: string; go?: [any, string?] }[] = [
  { key: "accounts", label: "Accounts" },
  { key: "prospects", label: "Prospects", go: ["prospects"] },
  { key: "qualified", label: "Qualified / engaged", go: ["pipeline"] },
  { key: "under_research", label: "Under research", go: ["control"] },
  { key: "active_workflows", label: "Active workflows", go: ["control"] },
  { key: "outreach_drafts", label: "Drafts ready", go: ["control"] },
  { key: "pending_approvals", label: "Pending approvals", go: ["approvals"] },
  { key: "meetings_booked", label: "Meetings booked", go: ["prospects", "meeting_booked"] },
  { key: "tasks_completed", label: "Tasks completed" },
  { key: "tasks_failed", label: "Tasks failed" },
  { key: "needs_review", label: "Needs human review", go: ["control"] },
];

export default function Overview({ nav }: { nav: Nav }) {
  const { data, error } = useData<OverviewData>("/overview");
  const { data: activity } = useData<ActivityEvent[]>("/activity?limit=15");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p>Live state of the autonomous sales pipeline, from persisted data.</p>
        </div>
      </div>
      <ErrorNote error={error} />
      <div className="grid kpis" style={{ marginBottom: 14 }}>
        {KPIS.map((k) => (
          <div
            key={k.key}
            className={`card kpi ${k.go ? "clickable" : ""}`}
            onClick={() => k.go && nav.go(k.go[0], { status: k.go[1] })}
            role={k.go ? "button" : undefined}
          >
            <div className="v">{data ? data.kpis[k.key] ?? 0 : "…"}</div>
            <div className="l">{k.label}</div>
          </div>
        ))}
      </div>
      <DemoPanel nav={nav} />
      <div className="grid g2">
        <div className="card">
          <h2>Prospects by stage <small>click a stage to filter</small></h2>
          <div onClick={() => nav.go("pipeline")} style={{ cursor: "pointer" }}>
            <BarList rows={Object.entries(data?.lead_status ?? {}).map(([label, value]) => ({ label, value }))} />
          </div>
        </div>
        <div className="card">
          <h2>Recent activity <a href="#/control" className="sub">Control Centre →</a></h2>
          {!activity?.length && <div className="empty">No activity yet</div>}
          <div className="feed" style={{ maxHeight: 320 }}>
            {activity?.map((e) => (
              <div className="feed-item" key={e.id}>
                <span className="t">{ago(e.at)}</span>
                <span>
                  <b>{human(e.type)}</b>{" "}
                  {e.prospect && (
                    <a href="#" onClick={(ev) => { ev.preventDefault(); e.lead_id && nav.openLead(e.lead_id); }}>{e.prospect}</a>
                  )}
                  {e.company && <span className="muted"> · {e.company}</span>}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

function DemoPanel({ nav }: { nav: Nav }) {
  const { data, error } = useData<any>("/demo/scenarios");
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (error || !data) return null; // demo endpoints are hidden in production
  const seed = async () => {
    setBusy(true);
    try {
      const r = await api.post<any>("/demo/seed");
      const made = r.seeded.filter((x: any) => x.created).length;
      toast(made ? `Loaded ${made} sample prospects. Oxygen picks them up on the next worker tick.` : "Sample prospects already loaded.");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  };
  const tick = async () => {
    setBusy(true);
    try {
      const r = await api.post<any>("/autonomy/tick");
      toast(r.paused ? "Autonomy is paused." : `Tick: ${r.cycles.length} decision(s), ${r.tasks_executed} task(s) executed.`);
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <h2>
        Demo scenarios <small>fictional SAMPLE prospects on reserved .example domains</small>
      </h2>
      <div className="grid g2" style={{ marginBottom: 12 }}>
        {data.scenarios.map((s: any) => (
          <div key={s.key} className="decision">
            <div className="row"><b>{s.title}</b><span className="sub">{s.prospect} · {s.company}</span></div>
            <div className="sub">{s.story}</div>
          </div>
        ))}
      </div>
      <div className="row">
        <button className="btn primary" disabled={busy} onClick={seed}>Load demo scenarios</button>
        <button className="btn" disabled={busy} onClick={tick}>Run one autonomy tick now</button>
        <button className="btn" onClick={() => nav.go("prospects")}>Open prospects</button>
        <span className="sub">The background worker also ticks automatically. Replies can be simulated from a prospect's page.</span>
      </div>
    </div>
  );
}
