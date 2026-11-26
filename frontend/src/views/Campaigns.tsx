import { FormEvent, useState } from "react";
import { api, Json } from "../api";
import type { Nav } from "../App";
import { useCan } from "../auth";
import {
  ago, BarList, Empty, ErrorNote, human, Kpi, LineChart, Loading, Modal, pct, Status, useAction, useConfirm, useData,
} from "../ui";
import "./campaigns.css";

// ── types (shapes from app/api/routes/pipeline.py) ───────────────────────────
export interface Icp {
  industries?: string[]; countries?: string[]; technologies?: string[]; titles?: string[]; departments?: string[];
  min_employees?: number | null; max_employees?: number | null; min_score?: number;
}
interface Step { step?: number; delay_days: number; angle: string | null }
interface Performance {
  target_accounts: number; leads_discovered: number; qualified_leads: number; emails_sent: number;
  replies_received: number; positive_replies: number; meetings_booked: number; opt_outs: number;
  qualification_rate: number | null; reply_rate: number | null; positive_reply_rate: number | null;
  meeting_rate: number | null; lead_to_meeting: number | null;
}
export interface Campaign {
  id: string; name: string; description: string | null; status: "draft" | "active" | "paused" | "stopped";
  icp: Icp | null; offer: string | null; value_points: string[] | null; sequence: Step[] | null;
  created_by: string | null; started_at: string | null; created_at?: string; performance?: Performance | null;
}
interface CampaignDetail extends Campaign {
  metrics: {
    kpis: Record<string, number>; rates: Record<string, number | null>;
    funnel: { stage: string; count: number }[]; timeseries: { date: string; sent: number; replies: number }[];
    lead_status: Record<string, number>;
  };
}
interface ActivityItem { id: string; type: string; source: string | null; lead_id: string | null; at: string; payload: Json }

const num = (v?: number | null) => (v === null || v === undefined ? "—" : v.toLocaleString());

/** "follow_ups_cancelled: 3" → "3 follow ups cancelled" for toasts. */
export function describeEffects(effects?: Json | null): string {
  if (!effects) return "";
  return Object.entries(effects).map(([k, v]) => `${v} ${human(k)}`).join(", ");
}

/** One-line ICP summary used by the campaign card, detail and Accounts view. */
export function icpSummary(icp?: Icp | null): string {
  if (!icp) return "No ICP defined";
  const size = icp.min_employees || icp.max_employees
    ? `${icp.min_employees ?? 1}–${icp.max_employees ?? "any"} employees` : null;
  return [icp.industries?.join(", "), size, icp.countries?.join(", ")].filter(Boolean).join(" · ") || "No ICP filters set";
}

// ── view ─────────────────────────────────────────────────────────────────────
export default function Campaigns({ nav }: { nav: Nav }) {
  const selected = nav.params.get("campaign");
  const can = useCan();
  const { data, error, loading, reload } = useData<Campaign[]>("/campaigns");
  const [creating, setCreating] = useState(false);

  if (selected) return <CampaignView id={selected} nav={nav} onChanged={reload} />;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Campaigns</h1>
          <p>Campaign management — define the ICP, offer and follow-up sequence that drive every pipeline stage.</p>
        </div>
        {can.operate && <button className="btn primary" onClick={() => setCreating(true)}>New campaign</button>}
      </div>
      <ErrorNote error={error} />
      {loading && !data ? <Loading /> : !data?.length ? (
        <div className="card">
          <Empty title="No campaigns yet"
                 hint="Create a campaign to define who to target, or load demo data from the Demo Control Centre."
                 action={can.operate ? <button className="btn primary" onClick={() => setCreating(true)}>New campaign</button> : undefined} />
        </div>
      ) : (
        <div className="cmp-cards">
          {data.map((c) => <CampaignCard key={c.id} c={c} onOpen={() => nav.go("campaigns", { campaign: c.id })} />)}
        </div>
      )}
      {creating && (
        <CampaignForm onClose={() => setCreating(false)}
                      onSaved={(c) => { setCreating(false); reload(); nav.go("campaigns", { campaign: c.id }); }} />
      )}
    </>
  );
}

function CampaignCard({ c, onOpen }: { c: Campaign; onOpen: () => void }) {
  const p = c.performance;
  const stats: [string, string][] = [
    ["Accounts", num(p?.target_accounts)], ["Leads", num(p?.leads_discovered)], ["Qualified", num(p?.qualified_leads)],
    ["Sent", num(p?.emails_sent)], ["Replies", num(p?.replies_received)], ["Meetings", num(p?.meetings_booked)],
    ["Reply rate", pct(p?.reply_rate)], ["Lead → meeting", pct(p?.lead_to_meeting)],
  ];
  return (
    <button className="card cmp-card" onClick={onOpen} aria-label={`Open campaign ${c.name}`}>
      <div className="cmp-title"><b>{c.name}</b><Status value={c.status} /></div>
      {c.description && <p>{c.description}</p>}
      <div className="sub">{icpSummary(c.icp)}</div>
      <div className="cmp-stats">
        {stats.map(([l, v]) => <div key={l}><b>{v}</b><span>{l}</span></div>)}
      </div>
      <div className="sub">{c.started_at ? `Started ${ago(c.started_at)}` : "Not started"}{c.created_by ? ` · created by ${c.created_by}` : ""}</div>
    </button>
  );
}

// ── detail ───────────────────────────────────────────────────────────────────
function CampaignView({ id, nav, onChanged }: { id: string; nav: Nav; onChanged: () => void }) {
  const { data: c, error, loading, reload } = useData<CampaignDetail>(`/campaigns/${id}`);
  const can = useCan();
  const confirm = useConfirm();
  const { busy, run } = useAction();
  const [tab, setTab] = useState<"overview" | "activity" | "setup">("overview");
  const [editing, setEditing] = useState(false);

  const back = <button className="cmp-back" onClick={() => nav.go("campaigns")}>‹ All campaigns</button>;
  if (loading && !c) return <>{back}<Loading /></>;
  if (!c) return <>{back}<ErrorNote error={error ?? "Campaign not found"} /></>;

  const refresh = () => { reload(); onChanged(); };

  const start = async () => {
    const ok = await confirm({
      title: c.status === "paused" ? "Resume and run discovery?" : "Start the pipeline?",
      body: <p style={{ margin: 0 }}>Oxygen activates <b>{c.name}</b> and runs lead discovery for every selected target account
        that has not been discovered yet. New leads then move through qualification, research and drafting automatically;
        every outbound email still waits for approval.</p>,
      confirm: "Start pipeline",
    });
    if (!ok) return;
    await run("start", () => api.post<Json>(`/campaigns/${c.id}/start`),
      (r) => `Pipeline ${human(r.status)}: ${r.contacts ?? 0} contacts found at ${r.accounts ?? 0} accounts, ${r.leads_created ?? 0} new leads (${r.provider ?? "provider"}).`);
    refresh();
  };

  const setStatus = async (status: "active" | "paused" | "stopped") => {
    if (status === "stopped") {
      const ok = await confirm({
        title: "Stop this campaign?", danger: true, confirm: "Stop campaign",
        body: <>
          <p style={{ marginTop: 0 }}>Stopping <b>{c.name}</b> ends outreach for all of its leads:</p>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            <li>scheduled and drafted follow-ups are cancelled,</li>
            <li>pending approvals for its leads are rejected,</li>
            <li>open work for its leads is closed.</li>
          </ul>
          <p style={{ marginBottom: 0 }}>A stopped campaign cannot be restarted or edited.</p>
        </>,
      });
      if (!ok) return;
    } else if (status === "paused") {
      const ok = await confirm({
        title: "Pause this campaign?", confirm: "Pause",
        body: <p style={{ margin: 0 }}>Oxygen holds all work for this campaign's leads until you resume it. Nothing is cancelled.</p>,
      });
      if (!ok) return;
    }
    const verb = status === "active" ? (c.status === "paused" ? "resumed" : "activated") : status;
    await run(status, () => api.post<Json>(`/campaigns/${c.id}/status`, { status }), (r) => {
      const fx = describeEffects(r.effects);
      return `Campaign ${verb}${fx ? ` — ${fx}` : ""}.`;
    });
    refresh();
  };

  const k = c.metrics.kpis;
  const r = c.metrics.rates;
  const stopped = c.status === "stopped";
  const withRate = (label: string, rate?: number | null, suffix = "") => (rate == null ? label : `${label} · ${pct(rate)}${suffix}`);

  return (
    <>
      {back}
      <div className="page-head cmp-head">
        <div style={{ minWidth: 0 }}>
          <h1>{c.name} <Status value={c.status} /></h1>
          <p>{c.description || "Campaign management — ICP, offer, sequence and performance for this campaign."}</p>
          <div className="sub" style={{ marginTop: 4 }}>
            {c.started_at ? `Started ${ago(c.started_at)}` : "Not started yet"}{c.created_by ? ` · created by ${c.created_by}` : ""}
          </div>
        </div>
        {can.operate && !stopped && (
          <div className="row">
            {(c.status === "draft" || c.status === "paused") && (
              <button className="btn primary" disabled={!!busy} onClick={start}>{busy === "start" ? "Starting…" : "Start pipeline"}</button>
            )}
            {c.status === "paused" && <button className="btn" disabled={!!busy} onClick={() => setStatus("active")}>Resume</button>}
            {c.status === "active" && <button className="btn" disabled={!!busy} onClick={() => setStatus("paused")}>Pause</button>}
            <button className="btn" disabled={!!busy} onClick={() => setEditing(true)}>Edit</button>
            <button className="btn danger" disabled={!!busy} onClick={() => setStatus("stopped")}>Stop</button>
          </div>
        )}
      </div>
      <ErrorNote error={error} />
      {c.status === "draft" && (
        <div className="banner info">
          <span>This campaign is a draft. Choose target accounts, then start the pipeline to discover leads.</span>
        </div>
      )}
      {stopped && <div className="banner warn"><span>This campaign is stopped. Its follow-ups were cancelled and it can no longer be edited.</span></div>}

      <div className="tabs" role="tablist">
        {(["overview", "activity", "setup"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t === "overview" ? "Performance" : t === "activity" ? "Activity" : "ICP & sequence"}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="grid">
          <div className="grid kpis">
            <Kpi v={num(k.target_accounts)} l="Target accounts" onClick={() => nav.go("accounts", { campaign: c.id })} hint="Open target accounts" />
            <Kpi v={num(k.leads_discovered)} l="Leads discovered" onClick={() => nav.go("contacts", { campaign: c.id })} hint="Open contacts" />
            <Kpi v={num(k.qualified_leads)} l={withRate("Qualified", r.qualification_rate)} />
            <Kpi v={num(k.emails_sent)} l="Emails sent" />
            <Kpi v={num(k.replies_received)} l={withRate("Replies", r.reply_rate)} />
            <Kpi v={num(k.positive_replies)} l={withRate("Positive replies", r.positive_reply_rate)} />
            <Kpi v={num(k.meetings_booked)} l={withRate("Meetings booked", r.meeting_rate, " of sent")} />
            <Kpi v={num(k.pending_approvals)} l="Awaiting approval" onClick={() => nav.go("approvals")} hint="Open the Approval Centre" />
          </div>
          <div className="grid g2">
            <div className="card">
              <h2>Funnel <small>lead → meeting {pct(r.lead_to_meeting)}</small></h2>
              <BarList rows={c.metrics.funnel.map((f) => ({ label: f.stage, value: f.count }))} />
            </div>
            <div className="card">
              <h2>Emails sent and replies <small>last 14 days</small></h2>
              <LineChart points={c.metrics.timeseries}
                         series={[{ key: "sent", label: "Sent", color: "var(--accent)" }, { key: "replies", label: "Replies", color: "var(--good)" }]} />
            </div>
          </div>
          <div className="card">
            <h2>Next steps</h2>
            <div className="row">
              <button className="btn" onClick={() => nav.go("accounts", { campaign: c.id })}>Target accounts ({num(k.target_accounts)})</button>
              <button className="btn" onClick={() => nav.go("contacts", { campaign: c.id })}>Contacts ({num(k.leads_discovered)})</button>
              <button className="btn" onClick={() => nav.go("qualification", { campaign: c.id })}>Qualification</button>
              <button className="btn" onClick={() => nav.go("followups", { campaign: c.id })}>Follow-ups ({num(k.follow_ups_scheduled)})</button>
            </div>
          </div>
        </div>
      )}
      {tab === "activity" && <CampaignActivity id={c.id} nav={nav} />}
      {tab === "setup" && <CampaignSetup c={c} />}
      {editing && <CampaignForm initial={c} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); refresh(); }} />}
    </>
  );
}

function CampaignSetup({ c }: { c: Campaign }) {
  const icp = c.icp ?? {};
  const list = (v?: string[]) => (v?.length ? v.join(", ") : <span className="muted">any</span>);
  return (
    <div className="grid g2">
      <div className="card">
        <h2>Ideal customer profile</h2>
        <dl className="cmp-icp">
          <dt>Industries</dt><dd>{list(icp.industries)}</dd>
          <dt>Company size</dt><dd>{icp.min_employees || icp.max_employees ? `${icp.min_employees ?? 1}–${icp.max_employees ?? "any"} employees` : <span className="muted">any</span>}</dd>
          <dt>Countries</dt><dd>{list(icp.countries)}</dd>
          <dt>Technologies</dt><dd>{list(icp.technologies)}</dd>
          <dt>Titles</dt><dd>{list(icp.titles)}</dd>
          <dt>Departments</dt><dd>{list(icp.departments)}</dd>
          <dt>Minimum score</dt><dd>{icp.min_score ?? "—"}</dd>
        </dl>
      </div>
      <div className="card">
        <h2>Offer</h2>
        <p style={{ marginTop: 0, whiteSpace: "pre-wrap" }}>{c.offer || <span className="muted">No offer written yet</span>}</p>
        {!!c.value_points?.length && (
          <>
            <h2>Value points</h2>
            <ul style={{ margin: 0, paddingLeft: 18 }}>{c.value_points.map((v, i) => <li key={i}>{String(v)}</li>)}</ul>
          </>
        )}
      </div>
      <div className="card" style={{ gridColumn: "1 / -1" }}>
        <h2>Follow-up sequence</h2>
        {!c.sequence?.length ? <div className="empty">No sequence steps</div> : (
          <table>
            <thead><tr><th>Step</th><th>Delay</th><th>Angle</th></tr></thead>
            <tbody>
              {c.sequence.map((s, i) => (
                <tr key={i}>
                  <td>{s.step ?? i + 1}{i === 0 ? <div className="sub">first touch</div> : null}</td>
                  <td>{s.delay_days} day{s.delay_days === 1 ? "" : "s"}</td>
                  <td>{s.angle || <span className="muted">Oxygen chooses</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function summarise(payload: Json): string {
  return Object.entries(payload ?? {})
    .filter(([k, v]) => !/(^|_)id$/.test(k) && v !== null && v !== undefined && v !== "" && typeof v !== "object")
    .slice(0, 5)
    .map(([k, v]) => `${human(k)}: ${String(v).slice(0, 80)}`)
    .join(" · ");
}

function CampaignActivity({ id, nav }: { id: string; nav: Nav }) {
  const { data, error, loading } = useData<ActivityItem[]>(`/campaigns/${id}/activity?limit=200`);
  return (
    <div className="card">
      <ErrorNote error={error} />
      {loading && !data ? <Loading /> : !data?.length ? (
        <Empty title="No activity yet" hint="Events appear here once accounts are added and the pipeline runs." />
      ) : (
        <div className="cmp-activity">
          {data.map((e) => (
            <div className="ev" key={e.id}>
              <div style={{ minWidth: 0 }}>
                <b>{human(e.type)}</b>
                <div className="sub" style={{ overflowWrap: "anywhere" }}>
                  {summarise(e.payload) || (e.source ? human(e.source) : "")}
                  {e.lead_id && <> · <a href="#" onClick={(ev) => { ev.preventDefault(); nav.openLead(e.lead_id!); }}>open prospect</a></>}
                </div>
              </div>
              <span className="sub" title={new Date(e.at).toLocaleString()}>{ago(e.at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── create / edit form ───────────────────────────────────────────────────────
const csv = (v?: string[] | null) => (v ?? []).join(", ");
const split = (v: string) => v.split(",").map((s) => s.trim()).filter(Boolean);

function CampaignForm({ initial, onClose, onSaved }: {
  initial?: Campaign; onClose: () => void; onSaved: (c: Campaign) => void;
}) {
  const icp = initial?.icp ?? {};
  const [f, setF] = useState({
    name: initial?.name ?? "", description: initial?.description ?? "", offer: initial?.offer ?? "",
    value_points: (initial?.value_points ?? []).map(String).join("\n"),
    industries: csv(icp.industries), countries: csv(icp.countries), technologies: csv(icp.technologies),
    titles: csv(icp.titles), departments: csv(icp.departments),
    min_employees: icp.min_employees != null ? String(icp.min_employees) : "",
    max_employees: icp.max_employees != null ? String(icp.max_employees) : "",
    min_score: String(icp.min_score ?? 60),
  });
  const [steps, setSteps] = useState<{ delay_days: string; angle: string }[]>(
    initial?.sequence?.length
      ? initial.sequence.map((s) => ({ delay_days: String(s.delay_days), angle: s.angle ?? "" }))
      : [{ delay_days: "0", angle: "" }, { delay_days: "3", angle: "" }],
  );
  const [err, setErr] = useState<string | null>(null);
  const { busy, run } = useAction();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((s) => ({ ...s, [k]: e.target.value }));
  const setStep = (i: number, k: "delay_days" | "angle", v: string) => setSteps((s) => s.map((x, j) => (j === i ? { ...x, [k]: v } : x)));

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    setErr(null);
    if (!f.name.trim()) return setErr("Give the campaign a name.");
    const minE = f.min_employees ? Number(f.min_employees) : null;
    const maxE = f.max_employees ? Number(f.max_employees) : null;
    if (minE && maxE && minE > maxE) return setErr("Minimum employees cannot exceed maximum employees.");
    if (!steps.length) return setErr("Add at least one sequence step.");
    if (steps.some((s) => s.delay_days === "" || Number(s.delay_days) < 0 || Number(s.delay_days) > 90))
      return setErr("Each step's delay must be between 0 and 90 days.");
    const body = {
      name: f.name.trim(), description: f.description.trim() || null, offer: f.offer.trim() || null,
      value_points: f.value_points.split("\n").map((s) => s.trim()).filter(Boolean),
      icp: {
        industries: split(f.industries), countries: split(f.countries), technologies: split(f.technologies),
        titles: split(f.titles), departments: split(f.departments), min_employees: minE, max_employees: maxE,
        min_score: Math.max(0, Math.min(100, Number(f.min_score) || 0)),
      },
      sequence: steps.map((s) => ({ delay_days: Number(s.delay_days), angle: s.angle.trim() || null })),
    };
    const saved = await run("save", () => initial
      ? api.patch<Campaign>(`/campaigns/${initial.id}`, body)
      : api.post<Campaign>("/campaigns", body), initial ? "Campaign updated." : "Campaign created as a draft.");
    if (saved) onSaved(saved);
  };

  return (
    <Modal wide title={initial ? `Edit ${initial.name}` : "New campaign"} onClose={onClose}
           footer={<>
             <button className="btn" onClick={onClose}>Cancel</button>
             <button className="btn primary" disabled={!!busy} onClick={() => submit()}>{busy ? "Saving…" : initial ? "Save changes" : "Create campaign"}</button>
           </>}>
      <form className="form" onSubmit={submit}>
        {err && <div className="banner bad" style={{ marginBottom: 0 }}>{err}</div>}
        <label>Name<input className="text" value={f.name} onChange={set("name")} required maxLength={255} autoFocus /></label>
        <label>Description<input className="text" value={f.description} onChange={set("description")} maxLength={2000} /></label>
        <label>Offer<textarea rows={3} value={f.offer} onChange={set("offer")} maxLength={4000}
                              placeholder="What you are offering and why it matters to this audience" /></label>
        <label>Value points <span className="muted">one per line</span>
          <textarea rows={3} value={f.value_points} onChange={set("value_points")} /></label>

        <fieldset className="cmp-fieldset">
          <legend>Ideal customer profile</legend>
          <div className="sub">Comma-separated lists. Leave a field empty to accept any value.</div>
          <div className="two">
            <label>Industries<input className="text" value={f.industries} onChange={set("industries")} /></label>
            <label>Countries<input className="text" value={f.countries} onChange={set("countries")} /></label>
            <label>Technologies<input className="text" value={f.technologies} onChange={set("technologies")} /></label>
            <label>Titles<input className="text" value={f.titles} onChange={set("titles")} /></label>
            <label>Departments<input className="text" value={f.departments} onChange={set("departments")} /></label>
            <label>Minimum qualification score (0–100)<input className="text" type="number" min={0} max={100} value={f.min_score} onChange={set("min_score")} /></label>
            <label>Minimum employees<input className="text" type="number" min={1} value={f.min_employees} onChange={set("min_employees")} /></label>
            <label>Maximum employees<input className="text" type="number" min={1} value={f.max_employees} onChange={set("max_employees")} /></label>
          </div>
        </fieldset>

        <fieldset className="cmp-fieldset">
          <legend>Follow-up sequence</legend>
          <div className="sub">Step 1 is the first touch. Each later step waits the given number of days after the previous one (up to 6 steps).</div>
          <div className="cmp-seq">
            {steps.map((s, i) => (
              <div className="cmp-step" key={i}>
                <span className="n">Step {i + 1}</span>
                <label>Delay (days)<input className="text" type="number" min={0} max={90} step="0.5" value={s.delay_days}
                                          onChange={(e) => setStep(i, "delay_days", e.target.value)} /></label>
                <label className="angle">Angle<input className="text" value={s.angle} maxLength={200}
                                                     placeholder={i === 0 ? "e.g. lead with the main pain point" : "e.g. share a relevant case study"}
                                                     onChange={(e) => setStep(i, "angle", e.target.value)} /></label>
                <button type="button" className="btn sm danger" disabled={steps.length <= 1}
                        onClick={() => setSteps((x) => x.filter((_, j) => j !== i))} aria-label={`Remove step ${i + 1}`}>Remove</button>
              </div>
            ))}
          </div>
          <div>
            <button type="button" className="btn sm" disabled={steps.length >= 6}
                    onClick={() => setSteps((x) => [...x, { delay_days: "3", angle: "" }])}>Add step</button>
          </div>
        </fieldset>
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}
