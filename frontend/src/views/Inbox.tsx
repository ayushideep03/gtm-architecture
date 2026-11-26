import { useEffect, useState } from "react";
import type { Nav } from "../App";
import { api, qs } from "../api";
import { useCan } from "../auth";
import { ago, DemoTag, Empty, ErrorNote, human, Loading, Status, useAction, useConfirm, useData } from "../ui";
import "./Inbox.css";

interface Conversation {
  lead_id: string; name: string; email: string | null; company: string | null; lead_status: string;
  last_at: string; messages: number; replies: number; category: string | null;
}
interface Message {
  id: string; type: string; direction: string | null; subject: string | null; body: string | null; at: string;
  provider: string | null; channel: string | null; simulated: boolean;
}
interface Classification {
  id: string; category: string; summary: string; objection: string | null; quotes: string[] | null; model: string | null; at: string;
}
interface Draft {
  id: string; kind: string; subject: string | null; body: string | null; explanation: string | null; strategy: any;
  evidence: { ref: string; kind: string; summary: string }[] | null; status: string; generated_by: string | null;
  created_at?: string;
}
interface Thread {
  lead_id: string; lead_status: string; prospect: { name: string | null; email: string | null };
  messages: Message[]; classifications: Classification[]; suggested_responses: Draft[];
}

const CATEGORY_TONE: Record<string, string> = {
  interested: "good", positive_interest: "good", meeting_request: "good", referral: "blue", question: "blue", unsupported_question: "warn", objection: "warn",
  budget_objection: "warn", out_of_office: "", not_interested: "bad", unsubscribe: "bad", negative: "bad", positive: "good",
};
const REPLY_LABELS: Record<string, string> = {
  interested: "Interested", not_interested: "Not interested", meeting_request: "Asks for a meeting",
  question: "Asks a question", unsupported_question: "Asks something we can't answer", objection: "Raises an objection",
  budget_objection: "Budget objection", out_of_office: "Out of office", unsubscribe: "Unsubscribe request",
};

function CategoryBadge({ value }: { value?: string | null }) {
  if (!value) return null;
  return <span className={`badge ${CATEGORY_TONE[value] ?? ""}`}>{human(value)}</span>;
}

const modelLabel = (m?: string | null) =>
  !m ? "Unknown classifier" : /demo|scripted/i.test(m) ? "Scripted demo classifier" : m;

export default function Inbox({ nav }: { nav: Nav }) {
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [onlyReplied, setOnlyReplied] = useState(false);
  const [sel, setSel] = useState<string | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => window.clearTimeout(t);
  }, [searchInput]);

  const list = useData<Conversation[]>(`/inbox${qs({ search, only_replied: onlyReplied || undefined })}`);
  const items = list.data ?? [];
  useEffect(() => {
    if (items.length && (!sel || !items.some((c) => c.lead_id === sel))) setSel(items[0].lead_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.data]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Inbox</h1>
          <p>Prospect conversations, how each reply was classified, and the response Oxygen suggests — nothing is sent without approval.</p>
        </div>
      </div>
      <ErrorNote error={list.error} />
      <div className="split3">
        <div className="card inbox-list">
          <div className="toolbar">
            <input className="search grow" type="search" placeholder="Search name or company" aria-label="Search conversations"
                   value={searchInput} onChange={(e) => setSearchInput(e.target.value)} />
            <label className="inbox-toggle">
              <input type="checkbox" checked={onlyReplied} onChange={(e) => setOnlyReplied(e.target.checked)} /> Replied only
            </label>
          </div>
          {list.loading && !list.data ? <Loading /> : !items.length ? (
            <Empty title={search || onlyReplied ? "No matching conversations" : "No conversations yet"}
                   hint={search || onlyReplied ? "Try clearing the filters." : "Conversations appear once outreach emails are sent."} />
          ) : (
            <div className="inbox-items" role="list">
              {items.map((c) => (
                <button key={c.lead_id} role="listitem" className={`list-item ${sel === c.lead_id ? "active" : ""}`}
                        onClick={() => setSel(c.lead_id)}>
                  <div className="inbox-item-top">
                    <b className="inbox-name">{c.name || c.email}</b>
                    <span className="sub inbox-when" title={new Date(c.last_at).toLocaleString()}>{ago(c.last_at)}</span>
                  </div>
                  <div className="sub inbox-co">{c.company ?? "—"}</div>
                  <div className="chip-row inbox-chips">
                    {c.category ? <CategoryBadge value={c.category} /> : <span className="badge">no reply</span>}
                    <span className="sub">{c.messages} msg{c.messages === 1 ? "" : "s"}{c.replies ? ` · ${c.replies} repl${c.replies === 1 ? "y" : "ies"}` : ""}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="inbox-thread">
          {sel ? <ThreadView key={sel} leadId={sel} nav={nav} /> : !list.loading && items.length > 0 ? null : (
            <div className="card"><Empty title="Select a conversation" hint="The thread, classification and suggested response appear here." /></div>
          )}
        </div>
      </div>
    </>
  );
}

function ThreadView({ leadId, nav }: { leadId: string; nav: Nav }) {
  const { data: t, error, loading, reload } = useData<Thread>(`/inbox/${leadId}`);
  const can = useCan();
  const demo = useData<{ reply_types: string[] }>(can.operate ? "/demo/control/status" : null);
  const { busy, run } = useAction();
  const confirm = useConfirm();

  if (loading && !t) return <div className="card"><Loading label="Loading conversation…" /></div>;
  if (error && !t) return <div className="card"><ErrorNote error={error} /></div>;
  if (!t) return null;

  const latest = t.classifications[t.classifications.length - 1];
  const demoTypes = can.operate && demo.data && !demo.error ? demo.data.reply_types ?? [] : [];

  const simulate = async (kind: string) => {
    const ok = await confirm({
      title: "Simulate a prospect reply?",
      body: <p>Oxygen will receive a simulated “{REPLY_LABELS[kind] ?? human(kind)}” reply from {t.prospect.name ?? "this prospect"} through
        the demo inbox and process it exactly like a real reply (classification, follow-up changes, suggested response). No email is sent.</p>,
      confirm: "Simulate reply",
    });
    if (!ok) return;
    await run(`reply:${kind}`, () => api.post<any>(`/demo/leads/${leadId}/reply`, { kind }), (r) => {
      const cat = r?.classification?.category;
      const acts: string[] = r?.actions ?? [];
      return `${r?.matched === false ? "Reply not matched to a lead" : `Classified as ${cat ? human(cat) : "unclassified"}`}`
        + (acts.length ? ` — ${acts.join("; ")}` : "");
    });
    reload();
  };

  return (
    <div className="grid inbox-stack">
      <div className="card">
        <div className="inbox-head">
          <div>
            <h2 className="inbox-title">{t.prospect.name ?? "Unknown prospect"}</h2>
            <div className="sub">{t.prospect.email ?? "No email"} · <Status value={t.lead_status} /></div>
          </div>
          <button className="btn sm" onClick={() => nav.openLead(t.lead_id)}>Open prospect</button>
        </div>
        {!t.messages.length ? <Empty title="No messages yet" /> : (
          <div className="inbox-msgs">
            {t.messages.map((m) => <MessageBubble key={m.id} m={m} />)}
          </div>
        )}
      </div>

      <div className="card">
        <h2>Reply classification <small>latest of {t.classifications.length}</small></h2>
        {!latest ? <Empty title="No reply classified yet" hint="Classifications appear when the prospect replies." /> : (
          <div className="grid inbox-class">
            <div className="row">
              <CategoryBadge value={latest.category} />
              <span className={`badge ${/demo|scripted/i.test(latest.model ?? "") ? "warn" : "blue"}`} title={latest.model ?? ""}>
                {modelLabel(latest.model)}
              </span>
              <span className="sub">{ago(latest.at)}</span>
            </div>
            <div>{latest.summary.includes(":") ? latest.summary.split(":").slice(1).join(":").trim() : latest.summary}</div>
            {latest.objection && <dl className="kv"><dt>Objection</dt><dd>{latest.objection}</dd></dl>}
            {!!latest.quotes?.length && (
              <div className="grid inbox-quotes">
                {latest.quotes.map((q, i) => <blockquote key={i} className="inbox-quote">“{q}”</blockquote>)}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="card">
        <h2>Suggested response <small>drafted from the approved knowledge base only</small></h2>
        {!t.suggested_responses.length ? (
          <Empty title="No suggested response" hint="Oxygen drafts a response after it classifies a reply that needs one." />
        ) : (
          <div className="grid inbox-drafts">
            {t.suggested_responses.map((d, i) => <DraftCard key={d.id} d={d} latest={i === 0} onChange={reload} canEdit={can.operate} />)}
          </div>
        )}
      </div>

      {demoTypes.length > 0 && (
        <div className="card inbox-demo">
          <h2><span>Simulate a prospect reply <DemoTag title="Demo control — injects a simulated inbound email" /></span></h2>
          <p className="sub inbox-demo-note">Presenter control. The reply goes through the simulated inbox and is processed like a real one.</p>
          <div className="chip-row">
            {demoTypes.map((k) => (
              <button key={k} className="btn sm" disabled={!!busy} onClick={() => simulate(k)}>
                {busy === `reply:${k}` ? "Sending…" : REPLY_LABELS[k] ?? human(k)}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function MessageBubble({ m }: { m: Message }) {
  if (m.type === "meeting") {
    return (
      <div className="inbox-event">
        <span className="sub">{m.subject ?? "Meeting update"} · {new Date(m.at).toLocaleString()}</span>
        {m.simulated && <DemoTag />}
      </div>
    );
  }
  const out = m.direction === "outbound";
  return (
    <div className={`inbox-msg ${out ? "out" : "in"}`}>
      <div className="inbox-msg-meta">
        <b>{out ? "Sent" : "Received"}</b>
        <span className="sub" title={new Date(m.at).toLocaleString()}>{new Date(m.at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</span>
        {m.simulated && <DemoTag title={out ? "Simulated send — this email did not leave the system" : "Simulated inbound message"} />}
      </div>
      {m.subject && <div className="inbox-subject">{m.subject}</div>}
      <div className="inbox-body">{m.body ?? ""}</div>
    </div>
  );
}

function DraftCard({ d, latest, onChange, canEdit }: { d: Draft; latest: boolean; onChange: () => void; canEdit: boolean }) {
  const [subject, setSubject] = useState(d.subject ?? "");
  const [body, setBody] = useState(d.body ?? "");
  const { busy, run } = useAction();
  const confirm = useConfirm();
  useEffect(() => { setSubject(d.subject ?? ""); setBody(d.body ?? ""); }, [d.id, d.subject, d.body]);

  const dirty = subject !== (d.subject ?? "") || body !== (d.body ?? "");
  const editable = d.status === "draft" && canEdit;
  const uncovered: string[] = Array.isArray(d.strategy?.uncovered) ? d.strategy.uncovered : [];
  const kb = (d.evidence ?? []).filter((e) => e.kind === "knowledge_base");
  const other = (d.evidence ?? []).filter((e) => e.kind !== "knowledge_base");

  if (d.status === "escalated") {
    return (
      <div className={`inbox-draft ${latest ? "" : "older"}`}>
        <div className="banner warn inbox-esc">
          <span aria-hidden>⚠</span>
          <div>
            <b>A person must reply.</b> Oxygen will not invent an answer: the approved knowledge base does not cover what the
            prospect asked, so no response was drafted.
            {d.explanation && <div className="sub inbox-esc-why">{d.explanation}</div>}
            {uncovered.length > 0 && (
              <ul className="inbox-uncovered">{uncovered.map((q, i) => <li key={i}>{q}</li>)}</ul>
            )}
          </div>
        </div>
        <div className="row"><Status value={d.status} />{d.generated_by && <span className="sub">by {d.generated_by}</span>}</div>
      </div>
    );
  }

  const save = () => run("save", () => api.put(`/drafts/${d.id}`, { subject, body }), "Draft saved").then(onChange);
  const submit = async () => {
    const ok = await confirm({
      title: "Submit for approval?",
      body: <p>This response goes to the Approval Centre. Nothing is sent to the prospect until an approver approves it.</p>,
      confirm: "Submit for approval",
    });
    if (!ok) return;
    await run("submit", async () => {
      if (dirty) await api.put(`/drafts/${d.id}`, { subject, body });
      return api.post(`/drafts/${d.id}/submit`);
    }, "Submitted for approval — not sent");
    onChange();
  };

  return (
    <div className={`inbox-draft ${latest ? "" : "older"}`}>
      <div className="row inbox-draft-head">
        <Status value={d.status} />
        {!latest && <span className="badge">earlier version</span>}
        {d.generated_by && <span className="sub">by {d.generated_by}</span>}
      </div>
      {editable ? (
        <div className="form">
          <label>Subject<input className="text" value={subject} onChange={(e) => setSubject(e.target.value)} /></label>
          <label>Body<textarea rows={8} value={body} onChange={(e) => setBody(e.target.value)} /></label>
        </div>
      ) : (
        <div className="email">
          {d.subject && <div className="inbox-subject">{d.subject}</div>}
          {d.body}
        </div>
      )}
      {(d.explanation || typeof d.strategy === "string") && (
        <dl className="kv">
          {d.explanation && <><dt>Why this response</dt><dd>{d.explanation}</dd></>}
          {typeof d.strategy === "string" && d.strategy && <><dt>Strategy</dt><dd>{d.strategy}</dd></>}
        </dl>
      )}
      {kb.length > 0 && (
        <div>
          <div className="sub inbox-evh">Knowledge-base entries used</div>
          <ul className="inbox-evidence">
            {kb.map((e, i) => <li key={i}><span className="mono">{e.ref}</span> {e.summary}</li>)}
          </ul>
        </div>
      )}
      {other.length > 0 && (
        <div>
          <div className="sub inbox-evh">Answers the reply</div>
          <ul className="inbox-evidence">{other.map((e, i) => <li key={i} className="sub">{e.summary}</li>)}</ul>
        </div>
      )}
      {editable && (
        <div className="row">
          <button className="btn sm" disabled={!dirty || !!busy} onClick={save}>{busy === "save" ? "Saving…" : "Save changes"}</button>
          <button className="btn sm primary" disabled={!!busy || !body.trim()} onClick={submit}>
            {busy === "submit" ? "Submitting…" : "Submit for approval"}
          </button>
          <span className="sub">Nothing is sent until approved.</span>
        </div>
      )}
    </div>
  );
}
