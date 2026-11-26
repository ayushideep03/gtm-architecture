import { useContext, useState } from "react";
import { ActivityEvent, api, Decision } from "../api";
import type { Nav } from "../App";
import { ago, clock, ErrorNote, human, LiveContext, useData, useToast } from "../ui";
import DecisionCard from "./DecisionCard";

export default function ControlCentre({ nav }: { nav: Nav }) {
  const live = useContext(LiveContext);
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const { data: status, reload: reloadStatus } = useData<any>("/autonomy/status");
  const { data: engine } = useData<any>("/oxygen/engine");
  const { data: decisions, error } = useData<Decision[]>("/decisions?limit=25");
  const { data: history } = useData<ActivityEvent[]>("/activity?limit=60");
  const { data: providers } = useData<any>("/execution/providers");
  const { data: work } = useData<any[]>("/memory/work-items");

  const act = async (path: string, label: string) => {
    setBusy(true);
    try {
      const r = await api.post<any>(path, path.endsWith("pause") || path.endsWith("resume") ? { actor: "dashboard" } : {});
      if (path.endsWith("tick")) toast(r.paused ? "Autonomy is paused." : `Tick: ${r.cycles.length} decision(s), ${r.tasks_executed} task(s), ${r.reactions} reaction(s).`);
      else toast(label);
      reloadStatus();
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  };

  // merge live SSE events (newest) with the persisted history
  const seen = new Set<string>();
  const feed = [...live.events, ...(history ?? [])].filter((e) => (seen.has(e.id) ? false : (seen.add(e.id), true))).slice(0, 80);
  const profiles: any[] = engine?.llm?.profiles ?? [];
  const humanWork = (work ?? []).filter((w) => w.kind === "human_review" || w.kind.startsWith("rep_task"));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Oxygen Control Centre</h1>
          <p>What the AI Sales Controller is deciding, why, and what is executing — live.</p>
        </div>
        <div className="row">
          <span className="sub"><span className={`live-dot ${live.connected ? "" : "off"}`} /> {live.connected ? "live" : "offline"}</span>
          {status?.paused
            ? <button className="btn good" disabled={busy} onClick={() => act("/autonomy/resume", "Autonomy resumed")}>Resume autonomy</button>
            : <button className="btn danger" disabled={busy} onClick={() => act("/autonomy/pause", "Autonomy paused — nothing will run until resumed")}>Pause autonomy</button>}
          <button className="btn primary" disabled={busy} onClick={() => act("/autonomy/tick", "")}>Run tick now</button>
        </div>
      </div>
      <ErrorNote error={error} />
      <div className="grid g3" style={{ marginBottom: 14 }}>
        <div className="card">
          <h2>Decision engine</h2>
          <dl className="kv">
            <dt>Mode</dt><dd>{human(engine?.effective)}</dd>
            {profiles.map((p) => (
              <><dt key={p.profile}>{p.profile}</dt><dd key={p.profile + "v"}>{p.vendor}/{p.model} {p.available ? <span className="badge good">ready</span> : <span className="badge" title={p.reason}>unavailable</span>}</dd></>
            ))}
            <dt>Prompt</dt><dd className="mono">{engine?.prompt_version ?? "—"}</dd>
          </dl>
        </div>
        <div className="card">
          <h2>Campaign</h2>
          <dl className="kv">
            <dt>Goal</dt><dd>{engine?.campaign?.goal ?? "—"}</dd>
            <dt>ICP</dt><dd className="sub">{engine?.campaign?.ideal_customer_profile ?? "—"}</dd>
            <dt>Min. fit score</dt><dd>{engine?.campaign?.qualification_min_score ?? "—"}</dd>
          </dl>
        </div>
        <div className="card">
          <h2>Autonomy</h2>
          <dl className="kv">
            <dt>State</dt><dd>{status ? (status.paused ? <span className="badge bad">paused</span> : <span className="badge good">running</span>) : "—"}</dd>
            <dt>Worker tick</dt><dd>every {status?.tick_interval_s ?? "—"}s</dd>
            <dt>Daily cap</dt><dd>{status?.max_cycles_per_lead_per_day ?? "—"} decisions / prospect</dd>
            <dt>Wakes on</dt><dd className="sub">{Object.keys(status?.reactions ?? {}).map(human).join(", ")}</dd>
          </dl>
        </div>
      </div>
      <div className="grid g2" style={{ alignItems: "start" }}>
        <div className="grid">
          <div className="card">
            <h2>Latest decisions <small>proposals are not permission: guardrails, compliance and approvals apply</small></h2>
            <div className="grid">
              {!decisions?.length && <div className="empty">No decisions yet</div>}
              {decisions?.map((d) => <DecisionCard key={d.id} d={d} onLead={nav.openLead} />)}
            </div>
          </div>
        </div>
        <div className="grid">
          <div className="card">
            <h2>Live activity <small>{feed.length} events</small></h2>
            <div className="feed">
              {feed.map((e) => (
                <div className="feed-item" key={e.id}>
                  <span className="t" title={e.at}>{clock(e.at)}</span>
                  <span>
                    <b>{human(e.type)}</b>{" "}
                    {e.prospect && <a href="#" onClick={(ev) => { ev.preventDefault(); e.lead_id && nav.openLead(e.lead_id); }}>{e.prospect}</a>}
                    {(e.payload?.action || e.payload?.provider) && <span className="sub"> · {[e.payload.action && human(e.payload.action), e.payload.provider].filter(Boolean).join(" · ")}</span>}
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div className="card">
            <h2>Waiting on people <small>{humanWork.length}</small></h2>
            {!humanWork.length && <div className="empty">Nothing needs a person right now</div>}
            {humanWork.map((w) => (
              <div className="feed-item" key={w.id}>
                <span className="t">{ago(w.due_at)}</span>
                <span><b>{human(w.kind)}</b> <a href="#" onClick={(ev) => { ev.preventDefault(); nav.openLead(w.lead_id); }}>open</a><div className="sub">{w.reason}</div></span>
              </div>
            ))}
          </div>
          <div className="card" style={{ padding: 0 }}>
            <h2 style={{ padding: "16px 16px 0" }}>Providers <small>selected by availability, health, rate limits, cost and outcomes</small></h2>
            <table>
              <thead><tr><th>Provider</th><th>Capability</th><th>State</th></tr></thead>
              <tbody>
                {providers?.providers?.map((p: any) => (
                  <tr key={p.provider_name}>
                    <td>{p.provider_name}{p.mode === "dry_run" && <span className="badge" style={{ marginLeft: 6 }}>dry run</span>}{(p.tags?.includes("mock") || p.tags?.includes("demo")) && <span className="badge demo" style={{ marginLeft: 6 }} title="Simulated provider: no external service is contacted">DEMO</span>}</td>
                    <td className="sub">{p.supported_task_types.map(human).join(", ")}</td>
                    <td>{p.available ? <span className="badge good">available</span> : <span className="badge" title={p.unavailable_reason}>unavailable</span>}
                      {!p.available && <div className="sub" style={{ maxWidth: 260 }}>{p.unavailable_reason}</div>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
