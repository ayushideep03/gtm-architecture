import { useEffect, useState } from "react";
import { Prospect } from "../api";
import type { Nav } from "../App";
import { ago, ErrorNote, human, Status, useData } from "../ui";

export const STATUSES = ["new", "enriching", "qualified", "contacted", "replied", "meeting_booked", "closed_won",
  "closed_lost", "disqualified"];

export default function Prospects({ nav, initialStatus }: { nav: Nav; initialStatus?: string }) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [status, setStatus] = useState(initialStatus ?? "");
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q), 250);
    return () => window.clearTimeout(t);
  }, [q]);
  useEffect(() => setStatus(initialStatus ?? ""), [initialStatus]);
  const params = new URLSearchParams();
  if (debounced) params.set("q", debounced);
  if (status) params.set("status", status);
  const { data, error, loading } = useData<Prospect[]>(`/prospects?${params}`);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Lead Intelligence</h1>
          <p>Every prospect Oxygen knows about, with its latest decision and what happens next.</p>
        </div>
        <div className="row">
          <input className="search" placeholder="Search name, email, company…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search prospects" />
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
            <option value="">All stages</option>
            {STATUSES.map((s) => <option key={s} value={s}>{human(s)}</option>)}
          </select>
        </div>
      </div>
      <ErrorNote error={error} />
      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Prospect</th><th>Company</th><th>Stage</th><th>Score</th><th>Last decision</th><th>Next</th><th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((p) => (
              <tr key={p.id} className="hover" onClick={() => nav.openLead(p.id)}>
                <td>
                  <b>{p.name ?? <span className="muted">Unnamed</span>}</b> {p.sample && <span className="badge">sample</span>}
                  <div className="sub">{p.title ?? ""}{p.title && p.email ? " · " : ""}{p.email ?? ""}</div>
                </td>
                <td>{p.company ?? "—"}<div className="sub">{[p.industry, p.employees ? `${p.employees} employees` : null].filter(Boolean).join(" · ")}</div></td>
                <td><Status value={p.status} />{p.pending_approvals > 0 && <div><span className="badge warn">{p.pending_approvals} awaiting approval</span></div>}</td>
                <td>{p.score ?? "—"}</td>
                <td>{p.last_decision ? <><span style={{ textTransform: "capitalize" }}>{human(p.last_decision.action)}</span><div className="sub">{ago(p.last_decision.at)}</div></> : <span className="muted">none yet</span>}</td>
                <td>{p.next_work ? <><span>{human(p.next_work.kind)}</span><div className="sub">{ago(p.next_work.due_at)}</div></> : <span className="muted">—</span>}</td>
                <td className="sub">{ago(p.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && !data?.length && <div className="empty">No prospects match. Import a CSV, connect a lead source, or load the demo scenarios from the Overview.</div>}
      </div>
    </>
  );
}
