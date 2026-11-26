import { useMemo, useRef, useState } from "react";
import { api, qs } from "../api";
import type { Nav } from "../App";
import { useCan } from "../auth";
import { ago, Empty, ErrorNote, human, Kpi, Loading, Pager, Status, useAction, useConfirm, useData } from "../ui";

interface Campaign { id: string; name: string }
interface FollowUp {
  id: string; lead_id: string; campaign_id: string | null; step: number; delay_hours: number | null; angle: string | null;
  due_at: string; status: string; reason: string | null; draft_task_id: string | null; send_task_id: string | null;
  updated_at: string; prospect: { name: string; company: string | null };
}

const STATUSES = ["scheduled", "due", "drafted", "sent", "cancelled", "skipped"];
const CANCELLABLE = ["scheduled", "due", "drafted"];
const STATUS_HINT: Record<string, string> = {
  scheduled: "Waiting for its send time",
  due: "Due now — Oxygen will draft it for approval",
  drafted: "Drafted and waiting for approval",
  sent: "Sent after approval",
  cancelled: "Stopped by a reply, a person or a rule",
  skipped: "Not needed, e.g. the sequence ended",
};
const PAGE_SIZE = 25;
const when = (iso: string) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

export default function FollowUps({ nav }: { nav: Nav }) {
  const can = useCan();
  const campaigns = useData<Campaign[]>("/campaigns");
  const [status, setStatus] = useState(nav.params.get("status") ?? "");
  const [campaignId, setCampaignId] = useState(nav.params.get("campaign") ?? "");
  const [page, setPage] = useState(1);
  const all = useData<FollowUp[]>(`/follow-ups${qs({ campaign_id: campaignId, limit: 1000 })}`);
  const { busy, run } = useAction();
  const confirm = useConfirm();
  const reason = useRef("");

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of all.data ?? []) c[f.status] = (c[f.status] ?? 0) + 1;
    return c;
  }, [all.data]);
  const rows = useMemo(() => (all.data ?? []).filter((f) => !status || f.status === status), [all.data, status]);
  const shown = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const pick = (s: string) => { setStatus(s); setPage(1); };

  const cancel = async (f: FollowUp) => {
    reason.current = "";
    const ok = await confirm({
      title: `Cancel step ${f.step} for ${f.prospect.name}?`,
      danger: true,
      confirm: "Cancel follow-up",
      body: <div className="form">
        <p style={{ margin: 0 }}>This follow-up will not be drafted or sent. Other steps in the sequence are not affected, and this cannot be undone.</p>
        <label>Reason (kept in the audit trail)
          <input className="text" maxLength={250} placeholder="e.g. Spoke to them by phone" onChange={(e) => { reason.current = e.target.value; }} />
        </label>
      </div>,
    });
    if (!ok) return;
    const r = await run(`cancel:${f.id}`, () => api.post(`/follow-ups/${f.id}/cancel`, reason.current.trim() ? { reason: reason.current.trim() } : {}),
                        "Follow-up cancelled.");
    if (r) all.reload();
  };

  const runNow = async (f: FollowUp) => {
    const ok = await confirm({
      title: `Make step ${f.step} due now?`,
      confirm: "Run now",
      body: <p style={{ margin: 0 }}>This follow-up for <b>{f.prospect.name}</b> becomes due immediately, so Oxygen drafts it now and places it in the
        Approval Centre. Nothing is sent until it is approved.</p>,
    });
    if (!ok) return;
    const r = await run(`run:${f.id}`, () => api.post<FollowUp & { made_due?: number }>(`/follow-ups/${f.id}/run-now`),
                        (x) => (x.status === "due" || (x.made_due ?? 0) > 0
                          ? "Follow-up is due now — Oxygen will draft it for approval."
                          : "Follow-up rescheduled to now — it becomes due on Oxygen's next cycle and is then drafted for approval."));
    if (r) all.reload();
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Follow-ups</h1>
          <p>Stage 8 — the automated follow-up schedule for every prospect in a sequence. Each step is drafted for approval before it is sent.</p>
        </div>
        <select value={campaignId} aria-label="Campaign" onChange={(e) => { setCampaignId(e.target.value); setPage(1); }}>
          <option value="">All campaigns</option>
          {campaigns.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>

      <div className="banner info" role="note">
        <div>
          A reply from the prospect automatically cancels their remaining follow-ups; an out-of-office reply postpones them until the person is back.
          In demo mode, the clock in the{" "}
          <a href="#" onClick={(e) => { e.preventDefault(); nav.go("demo"); }}>Demo Control Centre</a> can advance time so scheduled steps become due.
        </div>
      </div>

      <ErrorNote error={all.error ?? campaigns.error} />

      <div className="grid kpis" style={{ marginBottom: 14 }}>
        <Kpi v={all.data?.length ?? "—"} l="All follow-ups" onClick={() => pick("")} hint="Show every status" />
        {STATUSES.map((s) => (
          <Kpi key={s} v={all.data ? counts[s] ?? 0 : "—"} l={human(s)} hint={STATUS_HINT[s]} onClick={() => pick(s)} />
        ))}
      </div>

      <div className="toolbar">
        <select value={status} aria-label="Status" onChange={(e) => pick(e.target.value)}>
          <option value="">All statuses</option>
          {STATUSES.map((s) => <option key={s} value={s}>{human(s)}</option>)}
        </select>
        {status && <span className="sub">{STATUS_HINT[status]}</span>}
      </div>

      {all.loading && !all.data ? <div className="card"><Loading /></div> : !rows.length ? (
        <div className="card">
          <Empty title={status ? `No ${human(status)} follow-ups` : "No follow-ups scheduled yet"}
                 hint={status ? "Choose another status to see more." : "Follow-ups are scheduled automatically after a prospect's first email is sent."} />
        </div>
      ) : (
        <>
          <div className="card" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr><th>Prospect</th><th>Step</th><th>Angle</th><th>Scheduled</th><th>Status</th><th>Reason</th>{can.operate && <th>Actions</th>}</tr>
              </thead>
              <tbody>
                {shown.map((f) => (
                  <tr key={f.id}>
                    <td>
                      <a href="#" onClick={(e) => { e.preventDefault(); nav.openLead(f.lead_id); }}><b>{f.prospect.name || "Unnamed prospect"}</b></a>
                      <div className="sub">{f.prospect.company ?? ""}</div>
                    </td>
                    <td>Step {f.step}{f.delay_hours != null && <div className="sub">{f.delay_hours >= 24 ? `${Math.round(f.delay_hours / 24)}d` : `${f.delay_hours}h`} after previous</div>}</td>
                    <td className="sub" style={{ maxWidth: 240 }}>{f.angle ?? "—"}</td>
                    <td style={{ whiteSpace: "nowrap" }}>{when(f.due_at)}<div className="sub">{ago(f.due_at)}</div></td>
                    <td><Status value={f.status} /></td>
                    <td className="sub" style={{ maxWidth: 260 }}>{f.reason ?? "—"}</td>
                    {can.operate && (
                      <td>
                        <div className="row" style={{ flexWrap: "nowrap" }}>
                          {f.status === "scheduled" && (
                            <button className="btn sm" disabled={!!busy} title="Make this step due now so Oxygen drafts it for approval"
                                    onClick={() => runNow(f)}>{busy === `run:${f.id}` ? "Working…" : "Run now"}</button>
                          )}
                          {CANCELLABLE.includes(f.status) && (
                            <button className="btn sm danger" disabled={!!busy} onClick={() => cancel(f)}>
                              {busy === `cancel:${f.id}` ? "Cancelling…" : "Cancel"}
                            </button>
                          )}
                          {!CANCELLABLE.includes(f.status) && <span className="muted">—</span>}
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={page} pageSize={PAGE_SIZE} total={rows.length} onPage={setPage} />
          {can.operate && <p className="sub">“Run now” makes a scheduled step due immediately; Oxygen then drafts it and sends it to the Approval Centre. Nothing is sent without approval.</p>}
        </>
      )}
    </>
  );
}
