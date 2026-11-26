import type { Nav } from "../App";
import { qs } from "../api";
import { useAuth } from "../auth";
import { ago, BarList, DemoTag, Empty, ErrorNote, human, LineChart, Loading, pct, Status, useData } from "../ui";
import "./Dashboard.css";

interface Summary {
  kpis: Record<string, number>;
  rates: Record<string, number | null>;
  funnel: { stage: string; count: number }[];
  lead_status: Record<string, number>;
  reply_categories: Record<string, number>;
  timeseries: { date: string; sent: number; replies: number }[];
}
interface CampaignPerf {
  id: string; name: string; status: string; target_accounts: number; leads_discovered: number;
  qualified_leads: number; emails_sent: number; replies_received: number; positive_replies: number;
  meetings_booked: number; opt_outs: number; qualification_rate: number | null; reply_rate: number | null;
  positive_reply_rate: number | null; meeting_rate: number | null; lead_to_meeting: number | null;
}
interface LogEvent { id: string; type: string; source: string | null; lead_id: string | null; at: string; payload: any }

const RATES: { key: string; label: string; hint: string }[] = [
  { key: "qualification_rate", label: "Qualification rate", hint: "Qualified ÷ (qualified + disqualified)" },
  { key: "reply_rate", label: "Reply rate", hint: "Replies ÷ emails sent" },
  { key: "positive_reply_rate", label: "Positive reply rate", hint: "Positive replies ÷ replies" },
  { key: "meeting_rate", label: "Meeting rate", hint: "Meetings booked ÷ emails sent" },
  { key: "lead_to_meeting", label: "Lead → meeting", hint: "Meetings booked ÷ leads discovered" },
];

export default function Dashboard({ nav }: { nav: Nav }) {
  const { demoLogin } = useAuth();
  const campaign = nav.params.get("campaign") ?? "";
  const summary = useData<Summary>(`/metrics/summary${qs({ campaign_id: campaign })}`);
  const campaigns = useData<{ id: string; name: string; status: string }[]>("/campaigns");
  const perf = useData<CampaignPerf[]>("/metrics/campaigns");
  const logs = useData<LogEvent[]>("/workflow/logs?limit=30");

  const s = summary.data;
  const k = s?.kpis ?? {};
  const n = (key: string) => (s ? (k[key] ?? 0).toLocaleString() : "…");

  const tiles: { key: string; label: string; onClick?: () => void; hint?: string; demo?: boolean }[] = [
    { key: "target_accounts", label: "Target accounts", onClick: () => nav.go("accounts", { campaign }) },
    { key: "leads_discovered", label: "Leads discovered", onClick: () => nav.go("contacts", { campaign }) },
    { key: "qualified_leads", label: "Qualified", onClick: () => nav.go("qualification", { campaign }) },
    { key: "disqualified_leads", label: "Disqualified", onClick: () => nav.go("qualification", { campaign }) },
    { key: "research_signals", label: "Research signals", onClick: () => nav.go("research", { campaign }) },
    { key: "drafts_generated", label: "Drafts generated", onClick: () => nav.go("drafts", { campaign }) },
    { key: "pending_approvals", label: "Pending approvals", onClick: () => nav.go("approvals", { status: "pending" }),
      hint: "Open the Approval Centre" },
    { key: "emails_sent", label: "Emails sent", demo: demoLogin,
      hint: demoLogin ? "Simulated sends — no email leaves the system in demo mode" : undefined },
    { key: "follow_ups_scheduled", label: "Follow-ups scheduled", onClick: () => nav.go("followups") },
    { key: "replies_received", label: "Replies", onClick: () => nav.go("inbox") },
    { key: "positive_replies", label: "Positive replies" },
    { key: "negative_replies", label: "Negative replies" },
    { key: "meetings_proposed", label: "Meetings proposed", onClick: () => nav.go("meetings") },
    { key: "meetings_booked", label: "Meetings booked", onClick: () => nav.go("meetings", { status: "booked" }) },
    { key: "opt_outs", label: "Opt-outs" },
  ];

  const setCampaign = (id: string) => nav.go("overview", id ? { campaign: id } : undefined);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p>Pipeline performance from stored records — every figure is counted from the database and updates live.</p>
        </div>
        <select value={campaign} onChange={(e) => setCampaign(e.target.value)} aria-label="Campaign">
          <option value="">All campaigns</option>
          {(campaigns.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      <ErrorNote error={summary.error} />

      {summary.loading && !s ? <Loading label="Loading metrics…" /> : (
        <>
          <div className="grid kpis dash-kpis">
            {tiles.map((t) => (
              <div key={t.key} className={`card kpi ${t.onClick ? "clickable" : ""}`} title={t.hint}
                   onClick={t.onClick} role={t.onClick ? "button" : undefined} tabIndex={t.onClick ? 0 : undefined}
                   onKeyDown={t.onClick ? (e) => (e.key === "Enter" || e.key === " ") && t.onClick!() : undefined}>
                <div className="v">{n(t.key)}</div>
                <div className="l">{t.label}{t.demo && <> <DemoTag title="Simulated sends in demo mode — nothing left the system" /></>}</div>
              </div>
            ))}
          </div>

          <h3 className="dash-section">Conversion rates</h3>
          <div className="grid dash-rates">
            {RATES.map((r) => (
              <div key={r.key} className="card kpi" title={r.hint}>
                <div className="v">{pct(s?.rates?.[r.key])}</div>
                <div className="l">{r.label}</div>
                <div className="sub dash-hint">{r.hint}</div>
              </div>
            ))}
          </div>

          <div className="grid g2 dash-row">
            <div className="card">
              <h2>Pipeline funnel <small>leads at or past each stage</small></h2>
              <BarList rows={(s?.funnel ?? []).map((f) => ({ label: f.stage, value: f.count }))}
                       format={(v) => v.toLocaleString()} />
            </div>
            <div className="card">
              <h2>Emails sent vs replies <small>last 14 days</small></h2>
              <LineChart points={s?.timeseries ?? []} series={[
                { key: "sent", label: demoLogin ? "Emails sent (simulated)" : "Emails sent", color: "var(--series-1)" },
                { key: "replies", label: "Replies", color: "var(--warn)" },
              ]} />
            </div>
          </div>

          <div className="grid g2 dash-row">
            <div className="card">
              <h2>Reply categories <small>latest classification per prospect</small></h2>
              <BarList rows={Object.entries(s?.reply_categories ?? {}).sort((a, b) => b[1] - a[1])
                               .map(([label, value]) => ({ label, value }))} />
            </div>
            <div className="card">
              <h2>Leads by status</h2>
              <BarList rows={Object.entries(s?.lead_status ?? {}).sort((a, b) => b[1] - a[1])
                               .map(([label, value]) => ({ label, value }))} />
            </div>
          </div>
        </>
      )}

      <div className="grid dash-bottom">
        <div className="card dash-table">
          <h2>Campaign performance <small>click a row to filter the dashboard</small></h2>
          <ErrorNote error={perf.error} />
          {perf.loading && !perf.data ? <Loading /> : !perf.data?.length ? (
            <Empty title="No campaigns yet" hint="Create a campaign to start the pipeline."
                   action={<button className="btn sm" onClick={() => nav.go("campaigns")}>Go to campaigns</button>} />
          ) : (
            <div className="dash-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Campaign</th><th>Status</th><th className="num">Accounts</th><th className="num">Leads</th>
                    <th className="num">Qualified</th><th className="num">Sent</th><th className="num">Replies</th>
                    <th className="num">Positive</th><th className="num">Meetings</th><th className="num">Opt-outs</th>
                    <th className="num">Reply rate</th><th className="num">Meeting rate</th>
                  </tr>
                </thead>
                <tbody>
                  {perf.data.map((c) => (
                    <tr key={c.id} className={`hover ${c.id === campaign ? "dash-sel" : ""}`} onClick={() => setCampaign(c.id)}>
                      <td><b>{c.name}</b></td>
                      <td><Status value={c.status} /></td>
                      <td className="num">{c.target_accounts}</td>
                      <td className="num">{c.leads_discovered}</td>
                      <td className="num">{c.qualified_leads}</td>
                      <td className="num">{c.emails_sent}</td>
                      <td className="num">{c.replies_received}</td>
                      <td className="num">{c.positive_replies}</td>
                      <td className="num">{c.meetings_booked}</td>
                      <td className="num">{c.opt_outs}</td>
                      <td className="num">{pct(c.reply_rate)}</td>
                      <td className="num">{pct(c.meeting_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card">
          <h2>Latest activity <small>workflow log</small></h2>
          <ErrorNote error={logs.error} />
          {logs.loading && !logs.data ? <Loading /> : !logs.data?.length ? (
            <Empty title="No activity yet" hint="Events appear here as the pipeline runs." />
          ) : (
            <div className="dash-feed">
              {logs.data.map((e) => (
                <div key={e.id} className="dash-event">
                  <div className="dash-event-main">
                    {e.lead_id ? (
                      <button className="dash-link" onClick={() => nav.openLead(e.lead_id!)}>{human(e.type)}</button>
                    ) : <span>{human(e.type)}</span>}
                    {e.source && <span className="sub"> · {human(e.source)}</span>}
                  </div>
                  <span className="sub dash-time" title={new Date(e.at).toLocaleString()}>{ago(e.at)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
