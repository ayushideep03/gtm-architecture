import { useEffect, useState } from "react";
import type { Nav } from "../App";
import { api } from "../api";
import { useCan } from "../auth";
import { ago, DemoTag, Empty, ErrorNote, human, Loading, Status, useAction, useConfirm, useData } from "../ui";
import "./Meetings.css";

interface Meeting {
  id: string; lead_id: string; uid: string | null; status: string; provider: string | null; simulated: boolean;
  title: string | null; duration_minutes: number; proposed_slots: string[] | null; start_at: string | null; end_at: string | null;
  invite: { label?: string; to?: string; subject?: string; body?: string; ics_preview?: string; simulated?: boolean } | null;
  history: { at: string; status: string; by?: string; note?: string }[] | null;
  task_id: string | null; created_at: string; updated_at: string;
  prospect: { name: string | null; email: string | null; title: string | null; company: string | null };
}
interface Availability {
  simulated: boolean;
  availability: { timezone: string; weekdays: number[]; hours: number[]; min_notice_hours: number };
  next_free_slots: string[];
}

const STATUSES = ["requested", "pending_approval", "invited", "booked", "reschedule_requested", "declined", "cancelled"];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];   // Python weekday() numbering

const fmt = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";

function providerLabel(provider?: string | null, simulated?: boolean) {
  const p = (provider ?? "").toLowerCase();
  const name = p.includes("calendly") ? "Calendly" : p.includes("google") ? "Google Calendar"
    : p.includes("outlook") || p.includes("microsoft") ? "Outlook Calendar" : provider ? human(provider.replace(/^demo_/, "")) : "Calendar";
  return simulated ? `Simulated ${name}` : name;
}

export default function Meetings({ nav }: { nav: Nav }) {
  const filter = nav.params.get("status") ?? "";
  const { data, error, loading, reload } = useData<Meeting[]>("/meetings");
  const avail = useData<Availability>("/meetings/availability");
  const can = useCan();
  const demo = useData<unknown>("/demo/control/status");
  const demoOn = !!demo.data && !demo.error;
  const [sel, setSel] = useState<string | null>(null);

  const all = data ?? [];
  const counts = STATUSES.reduce<Record<string, number>>((acc, s) => ({ ...acc, [s]: all.filter((m) => m.status === s).length }), {});
  const shown = filter ? all.filter((m) => m.status === filter) : all;
  const current = shown.find((m) => m.id === sel) ?? null;

  useEffect(() => {
    if (shown.length && !shown.some((m) => m.id === sel)) setSel(shown[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, filter]);

  const setFilter = (s: string) => nav.go("meetings", s ? { status: s } : undefined);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Meetings</h1>
          <p>Meetings Oxygen has proposed, invited and booked — every calendar action in demo mode is simulated.</p>
        </div>
      </div>
      <ErrorNote error={error} />

      <div className="chip-row meet-chips" role="tablist" aria-label="Filter by status">
        <button role="tab" aria-selected={!filter} className={`meet-chip ${!filter ? "active" : ""}`} onClick={() => setFilter("")}>
          <b>{data ? all.length : "…"}</b> All
        </button>
        {STATUSES.map((s) => (
          <button key={s} role="tab" aria-selected={filter === s} className={`meet-chip ${filter === s ? "active" : ""}`}
                  onClick={() => setFilter(s)}>
            <b>{data ? counts[s] : "…"}</b> {human(s)}
          </button>
        ))}
      </div>

      {counts.pending_approval > 0 && (
        <div className="banner info">
          <span aria-hidden>ℹ</span>
          <div>
            {counts.pending_approval} meeting proposal{counts.pending_approval === 1 ? " is" : "s are"} waiting for approval.
            No invitation goes out until an approver approves it.{" "}
            <button className="meet-link" onClick={() => nav.go("approvals", { status: "pending" })}>Open the Approval Centre</button>
          </div>
        </div>
      )}

      <div className="split3">
        <div className="grid meet-side">
          <div className="card meet-list">
            {loading && !data ? <Loading /> : !shown.length ? (
              <Empty title={filter ? `No ${human(filter)} meetings` : "No meetings yet"}
                     hint={filter ? "Choose another status." : "Meetings appear when a prospect asks to talk and Oxygen proposes times."} />
            ) : (
              <div className="meet-items" role="list">
                {shown.map((m) => (
                  <button key={m.id} role="listitem" className={`list-item ${current?.id === m.id ? "active" : ""}`} onClick={() => setSel(m.id)}>
                    <div className="meet-item-top">
                      <b className="meet-ellipsis">{m.prospect.name ?? m.prospect.email ?? "Unknown prospect"}</b>
                      <Status value={m.status} />
                    </div>
                    <div className="sub meet-ellipsis">{m.title ?? "Untitled meeting"}{m.prospect.company ? ` · ${m.prospect.company}` : ""}</div>
                    <div className="sub meet-item-meta">
                      {m.status === "booked" && m.start_at ? <>Booked {fmt(m.start_at)}</>
                        : m.proposed_slots?.length ? <>{m.proposed_slots.length} proposed slot{m.proposed_slots.length === 1 ? "" : "s"}</>
                        : <>Updated {ago(m.updated_at)}</>}
                    </div>
                    <div className="row meet-item-prov">
                      <span className="sub">{providerLabel(m.provider, m.simulated)}</span>
                      {m.simulated && <DemoTag />}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
          <AvailabilityCard a={avail.data} error={avail.error} loading={avail.loading} />
        </div>

        <div className="meet-detail">
          {current ? (
            <MeetingDetail key={current.id} m={current} nav={nav} canSimulate={demoOn && (can.operate || can.admin)} demoOn={demoOn} onChange={reload} />
          ) : !loading && (
            <div className="card"><Empty title="Select a meeting" hint="Its timeline, invitation and proposed times appear here." /></div>
          )}
        </div>
      </div>
    </>
  );
}

function AvailabilityCard({ a, error, loading }: { a: Availability | null; error: string | null; loading: boolean }) {
  return (
    <div className="card">
      <h2><span>Demo calendar availability</span>{a?.simulated && <DemoTag title="Simulated calendar — not a real calendar" />}</h2>
      <ErrorNote error={error} />
      {loading && !a ? <Loading /> : a && (
        <>
          <dl className="kv meet-kv">
            <dt>Time zone</dt><dd>{a.availability.timezone}</dd>
            <dt>Working days</dt><dd>{a.availability.weekdays.map((d) => DAYS[d] ?? d).join(", ")}</dd>
            <dt>Slot start times</dt><dd>{a.availability.hours.map((h) => `${String(h).padStart(2, "0")}:00`).join(", ")}</dd>
            <dt>Minimum notice</dt><dd>{a.availability.min_notice_hours} hours</dd>
          </dl>
          <div className="sub meet-slots-h">Next free slots</div>
          {!a.next_free_slots.length ? <div className="muted">No free slots</div> : (
            <ul className="meet-slots">{a.next_free_slots.map((s) => <li key={s}>{fmt(s)}</li>)}</ul>
          )}
        </>
      )}
    </div>
  );
}

function MeetingDetail({ m, nav, canSimulate, demoOn, onChange }: {
  m: Meeting; nav: Nav; canSimulate: boolean; demoOn: boolean; onChange: () => void;
}) {
  const { busy, run } = useAction();
  const confirm = useConfirm();
  const slots = m.proposed_slots ?? [];
  const who = m.prospect.name ?? "the prospect";

  const respond = async (action: "accept" | "decline" | "reschedule", slot_index = 0) => {
    const text = {
      accept: { title: "Simulate: prospect accepts this slot?", body: `${who} will accept ${fmt(slots[slot_index])}. The meeting becomes booked.`, confirm: "Accept slot (simulated)" },
      decline: { title: "Simulate: prospect declines?", body: `${who} will decline the invitation. The meeting is marked declined.`, confirm: "Decline (simulated)" },
      reschedule: { title: "Simulate: prospect asks to reschedule?", body: `${who} will ask for a different time. Oxygen will propose new times, which need approval again.`, confirm: "Ask to reschedule (simulated)" },
    }[action];
    const ok = await confirm({
      title: text.title, confirm: text.confirm, danger: action === "decline",
      body: <><p>{text.body}</p><p className="sub">This simulates the prospect's calendar response through the demo calendar. No real calendar or email is touched.</p></>,
    });
    if (!ok) return;
    await run(`${action}:${slot_index}`, () => api.post<any>(`/meetings/${m.id}/respond`, { action, slot_index }),
      (r) => `Simulated response recorded — meeting is now ${human(r?.status)}`);
    onChange();
  };

  const canAccept = m.status === "invited";
  const canDecline = ["invited", "requested", "pending_approval"].includes(m.status);
  const canResched = ["invited", "booked"].includes(m.status);

  return (
    <div className="grid meet-stack">
      <div className="card">
        <div className="meet-head">
          <div className="meet-head-main">
            <h2 className="meet-title">{m.title ?? "Untitled meeting"}</h2>
            <div className="sub">
              {m.prospect.name ?? "Unknown prospect"}{m.prospect.title ? `, ${m.prospect.title}` : ""}{m.prospect.company ? ` · ${m.prospect.company}` : ""}
            </div>
            <div className="row meet-head-badges">
              <Status value={m.status} />
              <span className="badge">{m.duration_minutes} min</span>
              <span className="badge">{providerLabel(m.provider, m.simulated)}</span>
              {m.simulated && <DemoTag title="Simulated calendar provider — no real calendar was used" />}
            </div>
          </div>
          <button className="btn sm" onClick={() => nav.openLead(m.lead_id)}>Open prospect</button>
        </div>
        {m.status === "booked" && m.start_at && (
          <div className="meet-booked"><b>Booked:</b> {fmt(m.start_at)}{m.end_at ? ` – ${new Date(m.end_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}</div>
        )}
        {m.status === "pending_approval" && (
          <div className="banner warn meet-inline">
            <span aria-hidden>⏳</span>
            <div>This meeting proposal needs approval before any invitation is sent.{" "}
              <button className="meet-link" onClick={() => nav.go("approvals", { status: "pending" })}>Review in the Approval Centre</button>
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <h2>Proposed times <small>{slots.length} slot{slots.length === 1 ? "" : "s"}</small></h2>
        {!slots.length ? <div className="muted">No times proposed yet.</div> : (
          <ul className="meet-proposed">
            {slots.map((s, i) => (
              <li key={s} className={m.status === "booked" && m.start_at && new Date(m.start_at).getTime() === new Date(s).getTime() ? "chosen" : ""}>
                <span>{fmt(s)}</span>
                {canSimulate && canAccept && (
                  <button className="btn sm" disabled={!!busy} onClick={() => respond("accept", i)}>
                    {busy === `accept:${i}` ? "Accepting…" : "Prospect accepts this slot"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
        {canSimulate && (canDecline || canResched) && (
          <div className="row meet-sim">
            {canDecline && <button className="btn sm danger" disabled={!!busy} onClick={() => respond("decline")}>
              {busy?.startsWith("decline") ? "Declining…" : "Prospect declines"}</button>}
            {canResched && <button className="btn sm" disabled={!!busy} onClick={() => respond("reschedule")}>
              {busy?.startsWith("reschedule") ? "Requesting…" : "Prospect asks to reschedule"}</button>}
          </div>
        )}
        {canSimulate && (canAccept || canDecline || canResched) && (
          <p className="sub meet-simnote"><DemoTag title="Demo control" /> These buttons simulate the prospect's response. They are available only in demo mode.</p>
        )}
        {!demoOn && m.simulated && <p className="sub meet-simnote">Prospect responses can be simulated only in demo mode.</p>}
      </div>

      <div className="card">
        <h2>Invitation preview {m.invite?.simulated !== false && m.invite && <span className="badge demo">SIMULATED</span>}</h2>
        {!m.invite ? <div className="muted">No invitation has been sent yet.</div> : (
          <div className="grid meet-invite">
            {m.invite.label && <div className="banner warn meet-inline"><span aria-hidden>⚠</span><div>{m.invite.label}</div></div>}
            <dl className="kv">
              <dt>To</dt><dd>{m.invite.to ?? "—"}</dd>
              <dt>Subject</dt><dd>{m.invite.subject ?? "—"}</dd>
            </dl>
            {m.invite.body && <div className="email">{m.invite.body}</div>}
            {m.invite.ics_preview && (
              <div>
                <div className="sub meet-slots-h">Calendar file (.ics) preview</div>
                <pre className="json meet-ics">{m.invite.ics_preview}</pre>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="card">
        <h2>Timeline</h2>
        {!m.history?.length ? <div className="muted">No status changes recorded.</div> : (
          <ol className="meet-timeline">
            {[...m.history].reverse().map((h, i) => (
              <li key={i}>
                <div className="row"><Status value={h.status} /><span className="sub" title={new Date(h.at).toLocaleString()}>{fmt(h.at)} · {ago(h.at)}</span></div>
                {(h.note || h.by) && <div className="sub">{h.note}{h.note && h.by ? " — " : ""}{h.by && <i>{h.by}</i>}</div>}
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
