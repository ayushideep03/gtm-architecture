import { useEffect, useState } from "react";
import { api, qs } from "../api";
import type { Nav } from "../App";
import { useCan } from "../auth";
import { ago, DemoTag, Empty, ErrorNote, human, Loading, Modal, Pager, Status, useAction, useConfirm, useData } from "../ui";
import "./Drafts.css";

interface Campaign { id: string; name: string }
interface Evidence { ref: string; kind: string; summary: string; why_it_matters?: string | null; source?: string | null; simulated?: boolean }
interface Draft {
  id: string; lead_id: string; campaign_id: string | null; kind: string; step: number | null; version: number;
  subject: string | null; body: string; explanation: string | null; strategy: string | null; evidence: Evidence[] | null;
  status: string; generated_by: string | null; edited_by: string | null; source_task_id: string | null;
  send_task_id: string | null; created_at: string; updated_at: string;
  prospect: { name: string; email: string | null; title: string | null; company: string | null };
}
interface DraftPage { total: number; items: Draft[] }
interface Contact { lead_id: string; name: string; company: string | null; title: string | null; lead_status: string }

const STATUSES = ["draft", "submitted", "approved", "rejected", "sent", "escalated", "superseded"];
const KINDS: Record<string, string> = { initial: "First touch", follow_up: "Follow-up", reply: "Reply" };
const EDITABLE = ["draft", "rejected"];
const PAGE_SIZE = 25;

const isDemoAi = (g?: string | null) => !!g && /scripted|demo/i.test(g);

function GeneratedBy({ by }: { by: string | null }) {
  if (!by) return <span className="muted">Unknown generator</span>;
  if (isDemoAi(by)) return <span className="badge warn" title={`${by} — a scripted demo playbook, not a language model`}>Demo AI (scripted)</span>;
  return <span className="badge blue" title={by}>AI · {by.split("/").pop()}</span>;
}

export default function Drafts({ nav }: { nav: Nav }) {
  const can = useCan();
  const campaigns = useData<Campaign[]>("/campaigns");
  const [status, setStatus] = useState(nav.params.get("status") ?? "");
  const [kind, setKind] = useState("");
  const [campaignId, setCampaignId] = useState(nav.params.get("campaign") ?? "");
  const leadId = nav.params.get("lead") ?? "";
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [submitted, setSubmitted] = useState<{ id: string; name: string } | null>(null);

  // the API hides superseded drafts unless that status is asked for explicitly
  const { data, error, loading, reload } = useData<DraftPage>(
    `/drafts${qs({ status, kind, campaign_id: campaignId, lead_id: leadId, page, page_size: PAGE_SIZE })}`);
  const current = data?.items.find((d) => d.id === selected) ?? data?.items[0] ?? null;
  const filter = (fn: () => void) => { fn(); setPage(1); setSelected(null); };
  const filtered = !!(status || kind || campaignId || leadId);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Drafting workspace</h1>
          <p>Stage 6 — AI-personalised outreach, with the evidence and reasoning behind every message. Nothing is sent without approval.</p>
        </div>
        {can.operate && <button className="btn primary" onClick={() => setGenerating(true)}>Generate draft</button>}
      </div>

      {submitted && (
        <div className="banner info" role="status">
          <div style={{ flex: 1 }}>
            The draft for <b>{submitted.name}</b> is waiting in the Approval Centre. Nothing is sent until someone with approval rights approves it.
          </div>
          <button className="btn sm primary" onClick={() => nav.go("approvals")}>Open Approval Centre</button>
          <button className="btn sm" onClick={() => setSubmitted(null)} aria-label="Dismiss">✕</button>
        </div>
      )}

      <div className="toolbar">
        <select value={status} aria-label="Status" onChange={(e) => filter(() => setStatus(e.target.value))}>
          <option value="">All current drafts</option>
          {STATUSES.map((s) => <option key={s} value={s}>{human(s)}</option>)}
        </select>
        <select value={kind} aria-label="Type" onChange={(e) => filter(() => setKind(e.target.value))}>
          <option value="">All types</option>
          {Object.entries(KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <select value={campaignId} aria-label="Campaign" onChange={(e) => filter(() => setCampaignId(e.target.value))}>
          <option value="">All campaigns</option>
          {campaigns.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        {leadId && (
          <button className="btn sm" onClick={() => nav.go("drafts", { campaign: campaignId || undefined })}>Show all prospects ✕</button>
        )}
      </div>

      <ErrorNote error={error ?? campaigns.error} />
      {loading && !data ? <div className="card"><Loading /></div> : !data?.items.length ? (
        <div className="card">
          <Empty title={filtered ? "No drafts match these filters" : "No drafts yet"}
                 hint={filtered ? "Try another status, type or campaign." : "Oxygen drafts outreach for researched prospects automatically, or you can generate one now."}
                 action={can.operate ? <button className="btn" onClick={() => setGenerating(true)}>Generate draft</button> : undefined} />
        </div>
      ) : (
        <div className="split3">
          <div>
            <div className="card d-list" role="listbox" aria-label="Drafts">
              {data.items.map((d) => (
                <button key={d.id} role="option" aria-selected={current?.id === d.id}
                        className={`list-item ${current?.id === d.id ? "active" : ""}`} onClick={() => setSelected(d.id)}>
                  <span className="d-list-top"><b>{d.prospect.name || d.prospect.email}</b><span className="sub">{ago(d.updated_at)}</span></span>
                  <span className="sub d-subject">{d.subject || "(no subject)"}</span>
                  <span className="d-meta">
                    <Status value={d.status} />
                    <span className="sub">{KINDS[d.kind] ?? human(d.kind)}{d.step ? ` · step ${d.step}` : ""} · v{d.version}</span>
                    {isDemoAi(d.generated_by) && <DemoTag title="Written by the scripted demo AI" />}
                  </span>
                </button>
              ))}
            </div>
            <Pager page={page} pageSize={PAGE_SIZE} total={data.total} onPage={(p) => { setPage(p); setSelected(null); }} />
          </div>
          {current && (
            <DraftEditor key={`${current.id}:${current.updated_at}`} d={current} nav={nav} canOperate={can.operate}
                         onChanged={(id) => { if (id) setSelected(id); reload(); }}
                         onSubmitted={(d) => { setSubmitted({ id: d.id, name: d.prospect.name }); reload(); }} />
          )}
        </div>
      )}

      {generating && (
        <GenerateDraft campaignId={campaignId} onClose={() => setGenerating(false)}
                       onDone={(d) => { setGenerating(false); filter(() => { setStatus(""); setKind(""); }); setSelected(d.id); reload(); }} />
      )}
    </>
  );
}

function DraftEditor({ d, nav, canOperate, onChanged, onSubmitted }: {
  d: Draft; nav: Nav; canOperate: boolean; onChanged: (newId?: string) => void; onSubmitted: (d: Draft) => void;
}) {
  const [subject, setSubject] = useState(d.subject ?? "");
  const [body, setBody] = useState(d.body ?? "");
  const { busy, run } = useAction();
  const confirm = useConfirm();
  const editable = canOperate && EDITABLE.includes(d.status);
  const dirty = subject !== (d.subject ?? "") || body !== (d.body ?? "");
  const escalated = d.status === "escalated";
  const evidence = d.evidence ?? [];
  const strategy = (d.strategy ?? "").split(";").map((s) => s.trim()).filter(Boolean);

  const save = () => run("save", () => api.put<Draft>(`/drafts/${d.id}`, { subject: subject || null, body }), "Draft saved.")
    .then((r) => r && onChanged());
  const regenerate = async () => {
    if (dirty && !(await confirm({ title: "Discard your unsaved edits?", body: "Regenerating writes a new version and your unsaved changes to this one are lost.", confirm: "Regenerate", danger: true }))) return;
    const r = await run("regen", () => api.post<Draft>(`/drafts/${d.id}/regenerate`), (n) => `Version ${n.version} generated.`);
    if (r) onChanged(r.id);
  };
  const submit = async () => {
    const ok = await confirm({
      title: "Submit this draft for approval?",
      body: <>
        <p style={{ marginTop: 0 }}>The email to <b>{d.prospect.name}</b>{d.prospect.email ? ` <${d.prospect.email}>` : ""} goes to the Approval Centre.</p>
        <p style={{ marginBottom: 0 }}>Nothing is sent until someone with approval rights approves it there. Compliance checks run again at send time.</p>
      </>,
      confirm: "Submit for approval",
    });
    if (!ok) return;
    const r = await run("submit", () => api.post<Draft & { approval_id: string }>(`/drafts/${d.id}/submit`), "Submitted for approval.");
    if (r) onSubmitted(d);
  };

  return (
    <div className="d-editor">
      {escalated && (
        <div className="banner bad" role="alert">
          <div>
            <b>A person must answer this reply.</b> Oxygen could not answer it from the approved knowledge base, so it will not draft or send a response.
            {d.explanation && <div style={{ marginTop: 4 }}>{d.explanation}</div>}
          </div>
        </div>
      )}
      <div className={`card ${escalated ? "d-escalated" : ""}`}>
        <h2>
          <span>{KINDS[d.kind] ?? human(d.kind)}{d.step ? ` · step ${d.step}` : ""} → {d.prospect.name}</span>
          <Status value={d.status} />
        </h2>
        <div className="sub" style={{ marginBottom: 10 }}>
          {[d.prospect.title, d.prospect.company, d.prospect.email].filter(Boolean).join(" · ")}{" · "}
          <a href="#" onClick={(e) => { e.preventDefault(); nav.openLead(d.lead_id); }}>open prospect</a>
        </div>
        <div className="d-meta" style={{ marginBottom: 12 }}>
          <span className="badge">Version {d.version}</span>
          <GeneratedBy by={d.generated_by} />
          {d.edited_by && <span className="badge blue">Edited by {d.edited_by}</span>}
          <span className="sub">updated {ago(d.updated_at)}</span>
        </div>

        {editable ? (
          <div className="form">
            <label>Subject<input className="text" value={subject} maxLength={200} onChange={(e) => setSubject(e.target.value)} /></label>
            <label>Message<textarea rows={14} value={body} maxLength={8000} onChange={(e) => setBody(e.target.value)} /></label>
          </div>
        ) : (
          <div className="grid" style={{ gap: 8 }}>
            <div><span className="sub">Subject</span><div><b>{d.subject || "(no subject)"}</b></div></div>
            <div className="email">{d.body || <span className="muted">No message body</span>}</div>
            {canOperate && !EDITABLE.includes(d.status) && !escalated && (
              <div className="sub">
                {d.status === "submitted" ? "This draft is waiting for approval — edit it in the Approval Centre if needed."
                  : `A ${human(d.status)} draft can no longer be edited.`}
              </div>
            )}
          </div>
        )}

        {canOperate && (
          <div className="row" style={{ marginTop: 12 }}>
            {editable && <button className="btn" disabled={!dirty || !body.trim() || !!busy} onClick={save}>{busy === "save" ? "Saving…" : "Save changes"}</button>}
            {d.status === "draft" && (
              <button className="btn primary" disabled={dirty || !!busy} title={dirty ? "Save your edits before submitting" : undefined} onClick={submit}>
                {busy === "submit" ? "Submitting…" : "Submit for approval"}
              </button>
            )}
            {d.kind !== "reply" && ["draft", "rejected"].includes(d.status) && (
              <button className="btn" disabled={!!busy} onClick={regenerate}>{busy === "regen" ? "Generating…" : "Regenerate"}</button>
            )}
            {d.kind === "reply" && <button className="btn" onClick={() => nav.go("inbox", { lead: d.lead_id })}>Open conversation</button>}
            {d.status === "submitted" && <button className="btn" onClick={() => nav.go("approvals")}>Open Approval Centre</button>}
            {dirty && d.status === "draft" && <span className="sub">Save your edits before submitting.</span>}
          </div>
        )}
      </div>

      {!escalated && (
        <div className="card">
          <h2>Why it is written this way</h2>
          <p className="d-explain">{d.explanation || <span className="muted">No personalisation explanation was recorded.</span>}</p>
        </div>
      )}
      <div className="card">
        <h2>Suggested messaging strategy</h2>
        {strategy.length ? <ul className="d-strategy">{strategy.map((s, n) => <li key={n}>{s}</li>)}</ul>
          : <div className="muted">No strategy recorded.</div>}
      </div>
      <div className="card">
        <h2>Evidence used <small>{evidence.length} item{evidence.length === 1 ? "" : "s"}</small></h2>
        {!evidence.length ? <div className="muted">This draft does not cite any research evidence.</div> : (
          <ul className="d-evidence">
            {evidence.map((e, n) => (
              <li key={`${e.ref}-${n}`}>
                <span className="d-meta"><b>{human(e.kind)}</b>{e.simulated && <DemoTag />}{e.source && <span className="sub">{e.source}</span>}</span>
                <span>{e.summary}</span>
                {e.why_it_matters && <span className="sub">Why it matters: {e.why_it_matters}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function GenerateDraft({ campaignId, onClose, onDone }: { campaignId: string; onClose: () => void; onDone: (d: Draft) => void }) {
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [lead, setLead] = useState("");
  const [angle, setAngle] = useState("");
  const [followUp, setFollowUp] = useState(false);
  const { busy, run } = useAction();
  useEffect(() => { const t = window.setTimeout(() => setQ(search), 300); return () => window.clearTimeout(t); }, [search]);
  const { data, loading, error } = useData<{ items: Contact[] }>(`/contacts${qs({ campaign_id: campaignId, search: q, page_size: 200 })}`);

  const go = async () => {
    const r = await run("gen", () => api.post<Draft>("/drafts/generate", { lead_id: lead, angle: angle.trim() || undefined, follow_up: followUp }),
                        "Draft generated. Review it, then submit it for approval.");
    if (r) onDone(r);
  };

  return (
    <Modal title="Generate a draft" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!lead || !!busy} onClick={go}>{busy ? "Generating…" : "Generate draft"}</button>
    </>}>
      <p className="sub" style={{ margin: 0 }}>Oxygen writes a personalised email from the prospect's research. The draft is only saved here — nothing is sent.</p>
      <div className="form">
        <label>Find a prospect<input className="text" value={search} placeholder="Name, company or email" onChange={(e) => setSearch(e.target.value)} /></label>
        <label>Prospect
          <select value={lead} onChange={(e) => setLead(e.target.value)} size={6} style={{ width: "100%" }}>
            {data?.items.map((c) => (
              <option key={c.lead_id} value={c.lead_id}>{c.name}{c.company ? ` — ${c.company}` : ""}{c.title ? ` (${c.title})` : ""}</option>
            ))}
          </select>
        </label>
        {loading && !data && <Loading label="Loading prospects…" />}
        {data && !data.items.length && <div className="sub">No prospects match. Prospects appear once contacts are identified.</div>}
        <ErrorNote error={error} />
        <label>Angle (optional)<input className="text" value={angle} maxLength={200} placeholder="e.g. their recent expansion into Germany"
                                      onChange={(e) => setAngle(e.target.value)} /></label>
        <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={followUp} onChange={(e) => setFollowUp(e.target.checked)} /> Write it as a follow-up to an earlier email
        </label>
      </div>
    </Modal>
  );
}
