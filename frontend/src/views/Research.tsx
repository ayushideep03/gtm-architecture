import { useEffect, useMemo, useState } from "react";
import { api, qs } from "../api";
import type { Nav } from "../App";
import { useCan } from "../auth";
import { ago, Confidence, DemoTag, Empty, ErrorNote, human, Loading, Modal, Status, useAction, useData, useToast } from "../ui";
import "./Research.css";

interface Campaign { id: string; name: string }
interface Signal {
  id: string; lead_id: string | null; company_id: string | null; kind: string; label: string; statement: string;
  source: string | null; source_ref: string | null; why_it_matters: string | null; strength: string | null;
  confidence: number | null; simulated: boolean; citations: unknown; created_at: string;
}
interface Company { id: string; name: string; domain: string | null; industry: string | null }
interface ResearchData { items: Signal[]; companies: Record<string, Company> }
interface Contact {
  lead_id: string; name: string; company: string | null; company_domain?: string | null; title: string | null;
  lead_status: string; demo?: boolean;
}

const KINDS = ["news", "hiring", "technology", "expansion", "buying_signal", "digital_transformation", "challenge", "intent_assessment"];
const STRENGTH_TONE: Record<string, string> = { strong: "good", moderate: "blue", weak: "warn" };

// Anything produced by the demo generator or the scripted demo AI is simulated, even if the flag is missing.
const isSimulated = (s: Signal) => s.simulated || /simulated|scripted|demo/i.test(s.source ?? "");

export default function Research({ nav }: { nav: Nav }) {
  const can = useCan();
  const campaigns = useData<Campaign[]>("/campaigns");
  const [campaignId, setCampaignId] = useState(nav.params.get("campaign") ?? "");
  const [kind, setKind] = useState("");
  const leadId = nav.params.get("lead") ?? "";
  const { data, error, loading, reload } = useData<ResearchData>(`/research${qs({ campaign_id: campaignId, kind, lead_id: leadId })}`);
  const contacts = useData<{ items: Contact[] }>(`/contacts${qs({ campaign_id: campaignId, page_size: 200 })}`);
  const [running, setRunning] = useState(false);

  const people = useMemo(() => new Map((contacts.data?.items ?? []).map((c) => [c.lead_id, c])), [contacts.data]);

  const groups = useMemo(() => {
    const items = data?.items ?? [];
    // intent assessments are recorded per prospect; attach them to that prospect's company
    const leadCompany = new Map<string, string>();
    for (const s of items) if (s.lead_id && s.company_id) leadCompany.set(s.lead_id, s.company_id);
    // signals are de-duplicated per company, so also match other prospects through their company's domain or name
    const companies = Object.values(data?.companies ?? {});
    for (const c of people.values()) {
      if (leadCompany.has(c.lead_id)) continue;
      const co = companies.find((x) => (c.company_domain && x.domain === c.company_domain) || (c.company && x.name === c.company));
      if (co) leadCompany.set(c.lead_id, co.id);
    }
    const out = new Map<string, { company: Company | null; signals: Signal[]; intents: Signal[] }>();
    for (const s of items) {
      const cid = s.company_id ?? (s.lead_id ? leadCompany.get(s.lead_id) : undefined) ?? "";
      if (!out.has(cid)) out.set(cid, { company: cid ? data?.companies[cid] ?? null : null, signals: [], intents: [] });
      (s.kind === "intent_assessment" ? out.get(cid)!.intents : out.get(cid)!.signals).push(s);
    }
    return [...out.entries()].sort(([a, ga], [b, gb]) => (a === "" ? 1 : b === "" ? -1 : (ga.company?.name ?? "").localeCompare(gb.company?.name ?? "")));
  }, [data, people]);

  const anySimulated = data?.items.some(isSimulated);
  const filtered = !!(campaignId || kind || leadId);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Research &amp; intent</h1>
          <p>Stage 5 — the signals Oxygen gathered on each target company and why they matter for outreach.</p>
        </div>
        {can.operate && <button className="btn primary" onClick={() => setRunning(true)}>Run research</button>}
      </div>

      {anySimulated && (
        <div className="banner warn" role="note">
          <DemoTag />
          <div>Signals marked DEMO are simulated for the demo environment. They illustrate how research works and are
            never verified facts about the real companies named.</div>
        </div>
      )}

      <div className="toolbar">
        <select value={campaignId} aria-label="Campaign" onChange={(e) => setCampaignId(e.target.value)}>
          <option value="">All campaigns</option>
          {campaigns.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select value={kind} aria-label="Signal type" onChange={(e) => setKind(e.target.value)}>
          <option value="">All signal types</option>
          {KINDS.map((k) => <option key={k} value={k}>{human(k)}</option>)}
        </select>
        {leadId && (
          <span className="badge blue">One prospect
            <button className="btn sm" style={{ padding: "0 4px", border: 0, background: "none" }} aria-label="Show all prospects"
                    onClick={() => nav.go("research", { campaign: campaignId || undefined })}>✕</button>
          </span>
        )}
        <span className="grow" />
        {data && <span className="sub">{data.items.length} signal{data.items.length === 1 ? "" : "s"} across {groups.length} compan{groups.length === 1 ? "y" : "ies"}</span>}
      </div>

      <ErrorNote error={error ?? campaigns.error} />
      {loading && !data ? <div className="card"><Loading /></div> : !groups.length ? (
        <div className="card">
          <Empty title={filtered ? "No research signals match these filters" : "No research has been gathered yet"}
                 hint={filtered ? "Try another campaign or signal type." : "Oxygen researches qualified prospects automatically. You can also run research for a prospect now."}
                 action={can.operate ? <button className="btn" onClick={() => setRunning(true)}>Run research</button> : undefined} />
        </div>
      ) : (
        <div className="grid">
          {groups.map(([cid, g]) => (
            <section className="card r-company" key={cid || "none"} aria-label={g.company?.name ?? "Other signals"}>
              <div className="r-company-head">
                <div>
                  <h2>{g.company?.name ?? "Prospect-level signals"}</h2>
                  <div className="sub">
                    {g.company ? [g.company.domain, g.company.industry].filter(Boolean).join(" · ") || "No company details"
                      : "Signals not linked to a company record"}
                  </div>
                </div>
                <span className="sub">{g.signals.length} signal{g.signals.length === 1 ? "" : "s"}</span>
              </div>

              {g.intents.length > 0 && (
                <div className="grid" style={{ gap: 8 }}>
                  {g.intents.map((s) => (
                    <div className="r-intent" key={s.id}>
                      <div className="r-signal-head">
                        <b>Intent assessment{s.lead_id && people.get(s.lead_id) ? ` — ${people.get(s.lead_id)!.name}` : ""}</b>
                        <span className="row">
                          {(isSimulated(s) || g.signals.some(isSimulated) || (s.lead_id && people.get(s.lead_id)?.demo)) &&
                            <DemoTag title="Assessed from simulated demo signals — not a verified real-world fact" />}
                          <Confidence value={s.confidence} />
                        </span>
                      </div>
                      <p style={{ margin: 0 }}>{s.statement}</p>
                      <Why s={s} />
                      <div className="r-source">
                        {s.source ? `Source: ${s.source}` : ""} · {ago(s.created_at)}
                        {s.lead_id && <> · <a href="#" onClick={(e) => { e.preventDefault(); nav.openLead(s.lead_id!); }}>open prospect</a></>}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {g.signals.length > 0 && (
                <div className="r-signals">
                  {g.signals.map((s) => <SignalCard key={s.id} s={s} />)}
                </div>
              )}
            </section>
          ))}
        </div>
      )}

      {running && (
        <RunResearch campaignId={campaignId} onClose={() => setRunning(false)} onDone={() => { setRunning(false); reload(); }} />
      )}
    </>
  );
}

function SignalCard({ s }: { s: Signal }) {
  return (
    <article className="r-signal">
      <div className="r-signal-head">
        <b>{s.label}</b>
        <span className="row">
          {isSimulated(s) && <DemoTag />}
          {s.strength && <span className={`badge ${STRENGTH_TONE[s.strength] ?? ""}`}>{human(s.strength)}</span>}
        </span>
      </div>
      <span className="sub">{human(s.kind)}</span>
      <p>{s.statement}</p>
      <div className="r-source">
        {s.source ? `Source: ${s.source}` : "Source not recorded"}
        {s.source_ref && <> · {/^https?:\/\//.test(s.source_ref)
          ? <a href={s.source_ref} target="_blank" rel="noreferrer noopener">{s.source_ref}</a> : s.source_ref}</>}
        {" · "}{ago(s.created_at)}
      </div>
      <Why s={s} />
    </article>
  );
}

function Why({ s }: { s: Signal }) {
  const cites = Array.isArray(s.citations) ? (s.citations as unknown[]) : [];
  if (!s.why_it_matters && !cites.length) return null;
  return (
    <details>
      <summary>
        {s.why_it_matters ? "Why it matters" : "Supporting evidence"}
        {cites.length ? ` · ${cites.length} citation${cites.length === 1 ? "" : "s"}` : ""}
      </summary>
      <div className="r-why">
        {s.why_it_matters && <div>{s.why_it_matters}</div>}
        {cites.length > 0 && (
          <ul className="r-cites">
            {cites.map((c, n) => <li key={n}><Citation c={c} /></li>)}
          </ul>
        )}
        {s.confidence != null && s.kind !== "intent_assessment" && <div className="row"><span className="sub">Confidence</span><Confidence value={s.confidence} /></div>}
      </div>
    </details>
  );
}

function Citation({ c }: { c: unknown }) {
  if (typeof c === "string") return /^https?:\/\//.test(c) ? <a href={c} target="_blank" rel="noreferrer noopener">{c}</a> : <>{c}</>;
  if (c && typeof c === "object") {
    const o = c as Record<string, unknown>;
    const url = typeof o.url === "string" ? o.url : null;
    const title = (o.title ?? o.source ?? o.evidence_ref ?? url ?? "Citation") as string;
    return (
      <>
        {url ? <a href={url} target="_blank" rel="noreferrer noopener">{String(title)}</a> : <span className="mono">{String(title)}</span>}
        {typeof o.quote === "string" && o.quote && <span className="sub"> — “{o.quote}”</span>}
      </>
    );
  }
  return <>{String(c)}</>;
}

function RunResearch({ campaignId, onClose, onDone }: { campaignId: string; onClose: () => void; onDone: () => void }) {
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [lead, setLead] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const { busy, run } = useAction();
  const toast = useToast();
  useEffect(() => { const t = window.setTimeout(() => setQ(search), 300); return () => window.clearTimeout(t); }, [search]);
  const { data, loading, error } = useData<{ items: Contact[] }>(`/contacts${qs({ campaign_id: campaignId, search: q, page_size: 200 })}`);
  const chosen = data?.items.find((c) => c.lead_id === lead);

  const go = async () => {
    const r = await run("run", () => api.post<{ success: boolean; error_message?: string | null; task_status: string }>(`/research/leads/${lead}/run`));
    if (!r) return;
    if (r.success) { toast(`Research completed for ${chosen?.name ?? "the prospect"}.`); onDone(); } else { setErr(r.error_message ?? `Research finished with status “${human(r.task_status)}”.`); }
  };
  return (
    <Modal title="Run research for a prospect" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!lead || !!busy} onClick={go}>{busy ? "Researching…" : "Run research"}</button>
    </>}>
      <p className="sub" style={{ margin: 0 }}>Oxygen gathers company and prospect signals now instead of waiting for the next pipeline cycle.
        Research is internal only — nothing is sent to the prospect.</p>
      <div className="form">
        <label>Find a prospect<input className="text" value={search} placeholder="Name, company or email" onChange={(e) => setSearch(e.target.value)} /></label>
        <label>Prospect
          <select value={lead} onChange={(e) => { setLead(e.target.value); setErr(null); }} size={6} style={{ width: "100%" }}>
            {data?.items.map((c) => (
              <option key={c.lead_id} value={c.lead_id}>{c.name}{c.company ? ` — ${c.company}` : ""}{c.title ? ` (${c.title})` : ""}</option>
            ))}
          </select>
        </label>
        {loading && !data && <Loading label="Loading prospects…" />}
        {data && !data.items.length && <div className="sub">No prospects match. Prospects appear once contacts are identified.</div>}
        <ErrorNote error={error} />
        {chosen && <div className="sub">Selected: <b>{chosen.name}</b> · <Status value={chosen.lead_status} /></div>}
        {err && <div className="banner bad" style={{ marginBottom: 0 }}>{err}</div>}
      </div>
    </Modal>
  );
}
