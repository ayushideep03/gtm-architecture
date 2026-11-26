import { useState } from "react";
import { api, Decision } from "../api";
import { ago, ErrorNote, human, Status, useData, useToast } from "../ui";
import DecisionCard from "./DecisionCard";

type Tab = "summary" | "research" | "decisions" | "timeline" | "tasks";

export default function ProspectDrawer({ leadId, onClose }: { leadId: string; onClose: () => void }) {
  const { data: m, error, reload } = useData<any>(`/memory/leads/${leadId}`);
  const { data: demo } = useData<any>("/demo/scenarios");
  const [tab, setTab] = useState<Tab>("summary");
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState("");
  const toast = useToast();

  const runCycle = async () => {
    setBusy(true);
    try {
      const r = await api.post<any>(`/oxygen/leads/${leadId}/cycle`);
      toast(`Oxygen: ${human(r.decision?.action ?? r.status)} — ${r.next}`);
      setTab("decisions");
      reload();
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  };
  const simulate = async (kind: "reply" | "accept") => {
    setBusy(true);
    try {
      const r = await api.post<any>(`/demo/leads/${leadId}/simulate-reply`, { body: reply, kind });
      toast(r.actions.join("; "));
      setReply("");
      reload();
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  };

  const person = m?.person, company = m?.company, lead = m?.lead;
  const name = person ? [person.first_name, person.last_name].filter((x: string) => x && x !== "Unknown").join(" ") : "";
  const decisions: Decision[] = (m?.decisions ?? []).map((d: any) => ({ ...d, guardrails: d.guardrail_result, details: null, lead_id: leadId }));
  const facts = m ? [...m.facts.company.map((f: any) => ({ ...f, entity: "company" })), ...m.facts.person.map((f: any) => ({ ...f, entity: "person" })), ...m.facts.lead.map((f: any) => ({ ...f, entity: "lead" }))] : [];
  const timeline = m ? [
    ...m.interactions.map((i: any) => ({ at: i.occurred_at, kind: `${i.direction} ${human(i.type)}`, text: i.subject, body: i.body, by: i.provider })),
    ...m.events.filter((e: any) => !["oxygen_decision"].includes(e.type)).map((e: any) => ({ at: e.occurred_at, kind: human(e.type), text: e.source })),
  ].sort((a, b) => b.at.localeCompare(a.at)) : [];

  return (
    <>
      <div className="drawer-bg" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Prospect detail">
        <div className="page-head" style={{ marginBottom: 6 }}>
          <div>
            <h1>{name || person?.email || "Prospect"}</h1>
            <p>{[person?.title, company?.name].filter(Boolean).join(" · ") || "No role or company on record"}</p>
          </div>
          <div className="row">
            <button className="btn primary" disabled={busy} onClick={runCycle} title="Ask Oxygen to decide the next best action now">Run Oxygen</button>
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        </div>
        <ErrorNote error={error} />
        {lead && (
          <div className="row" style={{ marginTop: 6 }}>
            <Status value={lead.status} />
            {lead.score !== null && <span className="badge blue">fit score {lead.score}</span>}
            {m.compliance.allowed ? <span className="badge good">contact allowed</span> : <span className="badge bad" title={[...m.compliance.suppression_reasons, m.compliance.frequency_reason].filter(Boolean).join("; ")}>contact blocked</span>}
            {lead.notes?.startsWith("[SAMPLE") && <span className="badge">sample data</span>}
          </div>
        )}
        <div className="tabs" role="tablist">
          {(["summary", "research", "decisions", "timeline", "tasks"] as Tab[]).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
              {human(t)}{t === "decisions" && m ? ` (${m.decisions.length})` : ""}
            </button>
          ))}
        </div>
        {m && tab === "summary" && (
          <div className="grid">
            {lead.notes && <div className="banner info" style={{ margin: 0 }}>{lead.notes}</div>}
            <div className="grid g2">
              <div className="card">
                <h2>Contact</h2>
                <dl className="kv">
                  <dt>Email</dt><dd>{person?.email ?? "—"}</dd>
                  <dt>Title</dt><dd>{person?.title ?? "—"}</dd>
                  <dt>Location</dt><dd>{person?.location ?? "—"}</dd>
                  <dt>LinkedIn</dt><dd>{person?.linkedin_url ?? "—"}</dd>
                </dl>
              </div>
              <div className="card">
                <h2>Company</h2>
                <dl className="kv">
                  <dt>Name</dt><dd>{company?.name ?? "—"}</dd>
                  <dt>Domain</dt><dd>{company?.domain ?? "—"}</dd>
                  <dt>Industry</dt><dd>{company?.industry ?? "—"}</dd>
                  <dt>Employees</dt><dd>{company?.employee_count ?? "—"}</dd>
                </dl>
              </div>
            </div>
            <div className="card">
              <h2>Compliance</h2>
              <dl className="kv">
                <dt>Suppressed</dt><dd>{m.compliance.suppressed ? m.compliance.suppression_reasons.join("; ") : "No"}</dd>
                <dt>Lawful basis</dt><dd>{m.compliance.lawful_basis_ok ? m.compliance.lawful_basis_reason : `Missing — ${m.compliance.lawful_basis_reason}`}</dd>
                <dt>Frequency</dt><dd>{m.compliance.frequency_ok ? "Within limits" : m.compliance.frequency_reason}</dd>
              </dl>
            </div>
            <div className="card">
              <h2>Facts <small>evidence-scored; human-entered values are never overwritten</small></h2>
              {!facts.length && <div className="empty">No scored facts yet</div>}
              {facts.length > 0 && (
                <table><thead><tr><th>Field</th><th>Value</th><th>Confidence</th><th>Status</th><th>Sources</th></tr></thead>
                  <tbody>{facts.map((f: any) => (
                    <tr key={f.id}><td>{f.entity}.{f.field}</td><td>{String(f.value)}</td><td><span className={`badge ${f.band === "verified" ? "good" : f.band === "probable" ? "blue" : ""}`}>{f.band}</span></td><td>{human(f.status)}</td><td className="sub">{[...new Set(f.evidence.map((e: any) => e.kind))].join(", ")}</td></tr>
                  ))}</tbody></table>
              )}
            </div>
            {demo && (
              <div className="card">
                <h2>Simulate the prospect <small>demo only — goes through the real inbound pipeline</small></h2>
                <textarea rows={3} placeholder="Type the prospect's reply…" value={reply} onChange={(e) => setReply(e.target.value)} />
                <div className="row" style={{ marginTop: 8 }}>
                  {Object.entries(demo.suggested_replies as Record<string, string>).map(([k, v]) => (
                    <button key={k} className="btn sm" onClick={() => setReply(v)}>{human(k)}</button>
                  ))}
                  <span style={{ flex: 1 }} />
                  <button className="btn primary" disabled={busy || !reply.trim()} onClick={() => simulate("reply")}>Send reply</button>
                  <button className="btn" disabled={busy} onClick={() => simulate("accept")} title="Prospect accepts the meeting invitation">Accept meeting</button>
                </div>
              </div>
            )}
          </div>
        )}
        {m && tab === "research" && (
          <div className="grid">
            {!m.findings.length && <div className="empty">No research findings yet</div>}
            {m.findings.map((f: any) => (
              <div className="decision" key={f.id}>
                <div className="head"><span className="row"><span className="badge blue">{human(f.kind)}</span><span className="sub">{f.source}</span></span><span className="muted">{ago(f.created_at)}</span></div>
                <div>{f.summary}</div>
                {f.citations?.filter((c: any) => c.quote || c.url).map((c: any, i: number) => (
                  <div className="sub" key={i}>“{c.quote ?? ""}” {c.url && <a href={c.url} target="_blank" rel="noreferrer">source</a>}</div>
                ))}
              </div>
            ))}
          </div>
        )}
        {m && tab === "decisions" && (
          <div className="grid">
            {!decisions.length && <div className="empty">Oxygen has not evaluated this prospect yet. Click “Run Oxygen”.</div>}
            {decisions.map((d) => <DecisionCard key={d.id} d={d} showLead={false} />)}
          </div>
        )}
        {m && tab === "timeline" && (
          <div className="card">
            {!timeline.length && <div className="empty">Nothing yet</div>}
            <div className="feed" style={{ maxHeight: "none" }}>
              {timeline.map((t, i) => (
                <div className="feed-item" key={i}>
                  <span className="t">{ago(t.at)}</span>
                  <span><b style={{ textTransform: "capitalize" }}>{t.kind}</b> {t.text && <span className="sub">— {t.text}</span>}
                    {t.body && <div className="email" style={{ marginTop: 6 }}>{t.body}</div>}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {m && tab === "tasks" && (
          <div className="card" style={{ padding: 0, overflowX: "auto" }}>
            <table><thead><tr><th>Capability</th><th>Status</th><th>Created</th><th>Result</th></tr></thead>
              <tbody>{m.tasks.map((t: any) => (
                <tr key={t.id}><td>{human(t.capability)}</td><td><Status value={t.status} />{t.error_message && <div className="sub">{t.error_message}</div>}</td><td className="sub">{ago(t.created_at)}</td>
                  <td><details><summary className="sub">view</summary><pre className="json">{JSON.stringify(t.result ?? t.payload, null, 1)}</pre></details></td></tr>
              ))}</tbody></table>
            {!m.tasks.length && <div className="empty">No tasks yet</div>}
          </div>
        )}
      </aside>
    </>
  );
}
