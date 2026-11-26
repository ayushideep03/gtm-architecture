import { Prospect } from "../api";
import type { Nav } from "../App";
import { ago, ErrorNote, human, useData } from "../ui";

const COLUMNS = ["new", "enriching", "qualified", "contacted", "replied", "meeting_booked", "closed_won", "disqualified", "closed_lost"];

export default function Pipeline({ nav }: { nav: Nav }) {
  const { data, error } = useData<Prospect[]>("/prospects?limit=1000");
  const by: Record<string, Prospect[]> = {};
  for (const p of data ?? []) (by[p.status] ??= []).push(p);
  const cols = COLUMNS.filter((c) => by[c]?.length || ["new", "qualified", "contacted", "replied", "meeting_booked"].includes(c));
  for (const s of Object.keys(by)) if (!cols.includes(s)) cols.push(s);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Pipeline</h1>
          <p>Prospects by stage. Stages move only from real events: qualification, sends, replies, meetings.</p>
        </div>
      </div>
      <ErrorNote error={error} />
      <div className="board">
        {cols.map((c) => (
          <div className="col" key={c}>
            <h3>
              <a href={`#/prospects?status=${c}`} style={{ color: "inherit", textDecoration: "none", textTransform: "capitalize" }}>{human(c)}</a>
              <span className="badge">{by[c]?.length ?? 0}</span>
            </h3>
            {(by[c] ?? []).map((p) => (
              <div className="pcard" key={p.id} onClick={() => nav.openLead(p.id)}>
                <b>{p.name ?? p.email ?? "Unnamed"}</b>
                <div className="sub">{p.company ?? "—"}{p.score !== null ? ` · score ${p.score}` : ""}</div>
                {p.last_decision && <div className="sub" style={{ marginTop: 4 }}>Oxygen: {human(p.last_decision.action)} · {ago(p.last_decision.at)}</div>}
                {p.pending_approvals > 0 && <span className="badge warn" style={{ marginTop: 4 }}>awaiting approval</span>}
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
