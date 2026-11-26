import { useEffect, useState } from "react";
import { api, Approval } from "../api";
import type { Nav } from "../App";
import { ago, ErrorNote, human, Status, useData, useToast } from "../ui";

export default function Approvals({ nav }: { nav: Nav }) {
  const [filter, setFilter] = useState("pending");
  const { data, error, reload } = useData<Approval[]>(`/approvals?status=${filter}`);
  const [selected, setSelected] = useState<string | null>(null);
  const current = data?.find((a) => a.id === selected) ?? data?.[0] ?? null;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Approval Centre</h1>
          <p>Every external action Oxygen proposes waits here. Edit, approve or reject — nothing is sent without you.</p>
        </div>
        <select value={filter} onChange={(e) => { setFilter(e.target.value); setSelected(null); }} aria-label="Filter approvals">
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="expired">Expired</option>
          <option value="">All</option>
        </select>
      </div>
      <ErrorNote error={error} />
      <div className="grid split">
        <div className="card" style={{ padding: 6 }}>
          {!data?.length && <div className="empty">{filter === "pending" ? "Nothing waiting for approval" : "None"}</div>}
          {data?.map((a) => (
            <button key={a.id} className={`nav ${current?.id === a.id ? "active" : ""}`} onClick={() => setSelected(a.id)} style={{ display: "block", marginBottom: 2 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <b>{a.prospect.name ?? a.prospect.email}</b>
                <span className="sub">{ago(a.requested_at)}</span>
              </div>
              <div className="sub">{human(a.capability)} · {a.prospect.company ?? ""}</div>
            </button>
          ))}
        </div>
        {current ? <ApprovalDetail key={current.id} a={current} onDone={reload} nav={nav} /> : <div />}
      </div>
    </>
  );
}

function ApprovalDetail({ a, onDone, nav }: { a: Approval; onDone: () => void; nav: Nav }) {
  const toast = useToast();
  const payload = a.final_payload ?? a.original_payload;
  const isEmail = a.capability === "send_email";
  const [subject, setSubject] = useState(payload.subject ?? "");
  const [body, setBody] = useState(payload.body ?? "");
  const [topic, setTopic] = useState(payload.topic ?? "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const { data: memory } = useData<any>(a.lead_id ? `/memory/leads/${a.lead_id}` : null);
  useEffect(() => { setSubject(payload.subject ?? ""); setBody(payload.body ?? ""); setTopic(payload.topic ?? ""); }, [a.id]); // eslint-disable-line

  const pending = a.status === "pending";
  const edited = isEmail ? subject !== (a.original_payload.subject ?? "") || body !== (a.original_payload.body ?? "")
    : a.capability === "book_meeting" ? topic !== (a.original_payload.topic ?? "") : false;

  const decide = async (kind: "approve" | "reject") => {
    setBusy(true);
    try {
      const final = isEmail ? { ...a.original_payload, subject, body } : a.capability === "book_meeting" ? { ...a.original_payload, topic } : undefined;
      await api.post(`/approvals/${a.id}/${kind}`, { reviewer: "dashboard-user", note: note || undefined, ...(kind === "approve" && edited ? { payload: final } : {}) });
      toast(kind === "approve" ? "Approved. The worker executes it on the next tick (compliance is re-checked at send time)." : "Rejected. Oxygen will reconsider this prospect.");
      onDone();
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  };

  const evidence: string[] = Array.isArray(a.evidence) ? (a.evidence as any[]).map(String) : [];
  const describe = (id: string): string => {
    if (!memory) return id.slice(0, 8);
    const f = memory.findings.find((x: any) => x.id === id); if (f) return `${human(f.kind)}: ${f.summary}`;
    const i = memory.interactions.find((x: any) => x.id === id); if (i) return `${i.direction} ${human(i.type)}: ${i.subject ?? ""}`;
    const t = memory.tasks.find((x: any) => x.id === id); if (t) return `Task ${human(t.capability)} (${t.status})`;
    const d = (memory.decisions ?? []).find((x: any) => x.id === id); if (d) return `Earlier decision: ${human(d.action)}`;
    const ev = (memory.events ?? []).find((x: any) => x.id === id);
    if (ev) return `Event: ${human(ev.type)}${ev.payload?.capability ? ` (${human(ev.payload.capability)})` : ""} · ${ago(ev.occurred_at)}`;
    if (memory.lead?.id === id) return "Lead record"; if (memory.person?.id === id) return "Contact record"; if (memory.company?.id === id) return "Company record";
    return id.slice(0, 8);
  };

  return (
    <div className="grid">
      <div className="card">
        <h2>
          <span>{human(a.capability)} → {a.prospect.name} <span className="sub">&lt;{a.prospect.email}&gt;</span></span>
          <Status value={a.status} />
        </h2>
        <div className="sub" style={{ marginBottom: 10 }}>
          {[a.prospect.title, a.prospect.company].filter(Boolean).join(" · ")} ·{" "}
          {a.lead_id && <a href="#" onClick={(e) => { e.preventDefault(); nav.openLead(a.lead_id!); }}>open prospect</a>}
          {a.expires_at && pending && <> · expires {ago(a.expires_at)}</>}
        </div>
        {isEmail ? (
          <div className="grid" style={{ gap: 8 }}>
            <label className="sub">Subject<input className="text" value={subject} disabled={!pending} onChange={(e) => setSubject(e.target.value)} /></label>
            <label className="sub">Message<textarea rows={12} value={body} disabled={!pending} onChange={(e) => setBody(e.target.value)} /></label>
          </div>
        ) : a.capability === "book_meeting" ? (
          <div className="grid" style={{ gap: 8 }}>
            <label className="sub">Meeting topic<input className="text" value={topic} disabled={!pending} onChange={(e) => setTopic(e.target.value)} /></label>
            <pre className="json">{JSON.stringify(payload, null, 1)}</pre>
          </div>
        ) : <pre className="json">{JSON.stringify(payload, null, 1)}</pre>}
        {pending && (
          <div className="grid" style={{ gap: 8, marginTop: 10 }}>
            <input className="text" placeholder="Optional note for the record" value={note} onChange={(e) => setNote(e.target.value)} />
            <div className="row">
              <button className="btn good" disabled={busy} onClick={() => decide("approve")}>{edited ? "Approve edited version" : "Approve"}</button>
              <button className="btn danger" disabled={busy} onClick={() => decide("reject")}>Reject</button>
              {edited && <span className="badge blue">edited</span>}
            </div>
          </div>
        )}
        {!pending && (
          <div className="sub" style={{ marginTop: 10 }}>
            {human(a.status)} by {a.decided_by ?? "—"} {ago(a.decided_at)}{a.edited ? " (edited before approval)" : ""}{a.reviewer_note ? ` — “${a.reviewer_note}”` : ""}
          </div>
        )}
      </div>
      <div className="card">
        <h2>Why Oxygen proposed this</h2>
        <p style={{ marginTop: 0 }}>{a.reasoning ?? "—"}</p>
        <h2>Evidence it relied on</h2>
        {!evidence.length && <div className="empty">No evidence references</div>}
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {evidence.map((id) => <li key={id} className="sub" style={{ marginBottom: 4 }}>{describe(id)}</li>)}
        </ul>
        {memory && (
          <div className="row" style={{ marginTop: 10 }}>
            <span className="sub">Compliance now:</span>
            {memory.compliance.allowed ? <span className="badge good">contact allowed</span> : <span className="badge bad">blocked — {[...memory.compliance.suppression_reasons, memory.compliance.frequency_reason].filter(Boolean).join("; ")}</span>}
          </div>
        )}
      </div>
    </div>
  );
}
