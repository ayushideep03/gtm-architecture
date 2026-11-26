import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { api, qs } from "../api";
import type { Nav } from "../App";
import { useCan } from "../auth";
import {
  ago, BarList, Empty, ErrorNote, human, Kpi, Loading, Pager, pct, Status, useAction, useConfirm, useData,
} from "../ui";
import "./Qualification.css";

interface Icp {
  industries?: string[]; countries?: string[]; technologies?: string[]; titles?: string[]; departments?: string[];
  min_employees?: number | null; max_employees?: number | null; min_score?: number; weights?: Record<string, number>;
}
interface Campaign { id: string; name: string; status: string; icp: Icp | null }
interface Dimension { name: string; label: string; match: number; weight: number; points: number; detail: string; evidence: string }
interface QualItem {
  lead_id: string; name: string; title: string | null; company: string | null; industry: string | null;
  employees: number | null; country: string | null; score: number | null; fit: string | null; qualified: boolean | null;
  lead_status: string; dimensions: Dimension[]; reasons_for: string[]; reasons_against: string[];
  engine: string | null; evaluated_at: string;
}
interface QualPage { total: number; items: QualItem[] }
interface PreviewItem { lead_id: string; name: string; company: string | null; current_score: number | null; new_score: number; fit: string; qualified: boolean }
interface Preview { icp: Icp; leads: number; qualified: number; changed: number; items: PreviewItem[] }

const FITS = ["strong", "good", "weak", "poor"];
const LIST_FIELDS: { key: keyof Icp; label: string; hint: string }[] = [
  { key: "industries", label: "Industries", hint: "e.g. Software, Fintech" },
  { key: "countries", label: "Countries", hint: "e.g. United Kingdom, Germany" },
  { key: "technologies", label: "Technologies", hint: "e.g. Salesforce, Snowflake" },
  { key: "titles", label: "Job titles", hint: "e.g. VP Sales, Head of RevOps" },
  { key: "departments", label: "Departments", hint: "e.g. Sales, Operations" },
];
// Scoring dimensions the ICP engine understands (weights are optional overrides).
const DIMENSIONS: { name: string; label: string }[] = [
  { name: "industry", label: "Industry" }, { name: "company_size", label: "Company size" },
  { name: "location", label: "Location" }, { name: "technology", label: "Technology" }, { name: "persona", label: "Persona" },
];
const PAGE_SIZE = 25;

const tone = (score: number | null) => (score === null ? "" : score >= 75 ? "good" : score >= 50 ? "" : score >= 30 ? "warn" : "bad");

function ScoreBar({ score }: { score: number | null }) {
  if (score === null || score === undefined) return <span className="muted">—</span>;
  const v = Math.max(0, Math.min(100, score));
  return (
    <span className="q-score" title={`ICP score ${v} / 100`}>
      <span className={`q-score-track ${tone(v)}`}><i style={{ width: `${v}%` }} /></span>
      <b>{Math.round(v)}</b>
    </span>
  );
}

function Outcome({ q }: { q: boolean | null }) {
  if (q === null || q === undefined) return <span className="muted">—</span>;
  return q ? <span className="badge good">qualified</span> : <span className="badge bad">disqualified</span>;
}

export default function Qualification({ nav }: { nav: Nav }) {
  const can = useCan();
  const campaigns = useData<Campaign[]>("/campaigns");
  const [campaignId, setCampaignId] = useState(nav.params.get("campaign") ?? "");
  const [fit, setFit] = useState("");
  const [outcome, setOutcome] = useState(nav.params.get("status") === "disqualified" ? "disqualified" : "");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | null>(null);

  const summary = useData<QualPage>(`/qualification${qs({ campaign_id: campaignId, page_size: 200 })}`);
  const list = useData<QualPage>(`/qualification${qs({ campaign_id: campaignId, fit, outcome, page, page_size: PAGE_SIZE })}`);
  const campaign = campaigns.data?.find((c) => c.id === campaignId) ?? null;
  // open on a campaign so its ICP is visible straight away (the selector still offers "All campaigns")
  const defaulted = useRef(!!campaignId);
  useEffect(() => {
    if (defaulted.current || !campaigns.data?.length) return;
    defaulted.current = true;
    setCampaignId(campaigns.data.find((c) => c.status === "active")?.id ?? campaigns.data[0].id);
  }, [campaigns.data]);

  const stats = useMemo(() => {
    const items = summary.data?.items ?? [];
    const scored = items.filter((i) => typeof i.score === "number");
    const byFit = FITS.map((f) => ({ label: f, value: items.filter((i) => i.fit === f).length }));
    return {
      evaluated: summary.data?.total ?? 0,
      sampled: items.length,
      qualified: items.filter((i) => i.qualified).length,
      disqualified: items.filter((i) => i.qualified === false).length,
      avg: scored.length ? Math.round(scored.reduce((s, i) => s + (i.score ?? 0), 0) / scored.length) : null,
      byFit,
    };
  }, [summary.data]);

  const reloadAll = () => { summary.reload(); list.reload(); campaigns.reload(); };
  const setFilter = (fn: () => void) => { fn(); setPage(1); setOpen(null); };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Qualification</h1>
          <p>Stage 4 — every prospect is scored against the campaign's ideal customer profile, with the reasons behind each decision.</p>
        </div>
        <select value={campaignId} aria-label="Campaign"
                onChange={(e) => setFilter(() => setCampaignId(e.target.value))}>
          <option value="">All campaigns</option>
          {campaigns.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      <ErrorNote error={campaigns.error ?? summary.error} />

      {summary.loading && !summary.data ? <Loading /> : (
        <div className="grid" style={{ gap: 14, marginBottom: 14 }}>
          <div className="grid kpis">
            <Kpi v={stats.evaluated} l="Prospects evaluated" />
            <Kpi v={stats.qualified} l="Qualified" hint="Show qualified prospects"
                 onClick={() => setFilter(() => setOutcome("qualified"))} />
            <Kpi v={stats.disqualified} l="Disqualified" hint="Show disqualified prospects"
                 onClick={() => setFilter(() => setOutcome("disqualified"))} />
            <Kpi v={stats.avg ?? "—"} l="Average ICP score" />
            <Kpi v={stats.sampled ? pct(stats.qualified / stats.sampled) : "—"} l="Qualification rate" />
          </div>
          {stats.sampled < stats.evaluated && (
            <div className="sub">Summary figures are based on the {stats.sampled} highest-scoring of {stats.evaluated} evaluated prospects.</div>
          )}
        </div>
      )}

      <div className="grid g2" style={{ marginBottom: 14 }}>
        <div className="card">
          <h2>Prospects by fit <small>click a filter below to narrow the table</small></h2>
          <BarList rows={stats.byFit} />
        </div>
        {campaign ? (
          <IcpEditor key={campaign.id} campaign={campaign} canEdit={can.operate} currentQualified={stats.qualified}
                     currentEvaluated={stats.evaluated} onSaved={reloadAll}
                     currentOutcome={new Map((summary.data?.items ?? []).map((i) => [i.lead_id, i.qualified]))} />
        ) : (
          <div className="card">
            <h2>ICP criteria</h2>
            <Empty title="Select a campaign to see its ideal customer profile"
                   hint="Each campaign has its own ICP. Choose one above to review, preview or change the criteria." />
          </div>
        )}
      </div>

      <div className="toolbar">
        <select value={fit} aria-label="Fit" onChange={(e) => setFilter(() => setFit(e.target.value))}>
          <option value="">Any fit</option>
          {FITS.map((f) => <option key={f} value={f}>{human(f)} fit</option>)}
        </select>
        <select value={outcome} aria-label="Outcome" onChange={(e) => setFilter(() => setOutcome(e.target.value))}>
          <option value="">Qualified and disqualified</option>
          <option value="qualified">Qualified only</option>
          <option value="disqualified">Disqualified only</option>
        </select>
        {(fit || outcome) && <button className="btn sm" onClick={() => setFilter(() => { setFit(""); setOutcome(""); })}>Clear filters</button>}
      </div>

      <ErrorNote error={list.error} />
      {list.loading && !list.data ? <div className="card"><Loading /></div> : !list.data?.items.length ? (
        <div className="card">
          <Empty title={fit || outcome ? "No prospects match these filters" : "No prospects have been qualified yet"}
                 hint={fit || outcome ? "Try a different fit or outcome." : "Prospects are scored automatically once contacts are identified for a campaign."} />
        </div>
      ) : (
        <>
          <div className="card" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr><th>Prospect</th><th>Company</th><th>Score</th><th>Fit</th><th>Outcome</th><th>Key reason</th><th>Stage</th></tr>
              </thead>
              <tbody>
                {list.data.items.map((i) => {
                  const key = i.qualified ? i.reasons_for[0] : i.reasons_against[0] ?? i.reasons_for[0];
                  const expanded = open === i.lead_id;
                  return (
                    <Fragment key={i.lead_id}>
                      <tr className="hover" onClick={() => setOpen(expanded ? null : i.lead_id)} aria-expanded={expanded}>
                        <td><b>{i.name || "—"}</b><div className="sub">{i.title ?? ""}</div></td>
                        <td>{i.company ?? "—"}<div className="sub">{[i.industry, i.country].filter(Boolean).join(" · ")}</div></td>
                        <td><ScoreBar score={i.score} /></td>
                        <td><Status value={i.fit} /></td>
                        <td><Outcome q={i.qualified} /></td>
                        <td className="sub q-key">{key ?? "—"}</td>
                        <td><Status value={i.lead_status} /></td>
                      </tr>
                      {expanded && (
                        <tr>
                          <td colSpan={7} style={{ background: "var(--surface)" }}>
                            <Explanation item={i} onOpen={() => nav.openLead(i.lead_id)} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Pager page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={(p) => { setPage(p); setOpen(null); }} />
        </>
      )}
    </>
  );
}

function Explanation({ item, onOpen }: { item: QualItem; onOpen: () => void }) {
  const against = item.reasons_against ?? [];
  const forList = item.reasons_for ?? [];
  const blocks = [
    against.length > 0 && (
      <div key="against">
        <h3>{item.qualified ? "Reasons against" : "Why this prospect was disqualified"}</h3>
        <ul className="q-reasons against">{against.map((r, n) => <li key={n}>{r}</li>)}</ul>
      </div>
    ),
    forList.length > 0 && (
      <div key="for">
        <h3>Reasons for</h3>
        <ul className="q-reasons for">{forList.map((r, n) => <li key={n}>{r}</li>)}</ul>
      </div>
    ),
  ];
  return (
    <div className="q-explain">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="sub">
          Score {item.score ?? "—"} / 100 · evaluated {ago(item.evaluated_at)}{item.engine ? ` · ${human(item.engine)}` : ""}
          {item.employees ? ` · ${item.employees.toLocaleString()} employees` : ""}
        </span>
        <button className="btn sm" onClick={(e) => { e.stopPropagation(); onOpen(); }}>Open prospect</button>
      </div>
      {/* the disqualification reason comes first when the prospect did not qualify */}
      {item.qualified ? blocks.reverse() : blocks}
      <div>
        <h3>Score by dimension</h3>
        {!item.dimensions?.length ? <div className="sub">No dimension breakdown was recorded for this evaluation.</div> : (
          <div className="q-dims">
            {item.dimensions.map((d) => (
              <div className="q-dim" key={d.name}>
                <div className="q-dim-head">
                  <b>{d.label}</b>
                  <span className="row">
                    <span className={`badge ${d.match >= 0.75 ? "good" : d.match >= 0.4 ? "warn" : "bad"}`}>{pct(d.match)} match</span>
                    <span className="sub">{d.points} / {d.weight} pts</span>
                  </span>
                </div>
                {d.detail && <div>{d.detail}</div>}
                {d.evidence && <div className="sub">Evidence: {d.evidence}</div>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── ICP editor ───────────────────────────────────────────────────────────────

interface Form {
  lists: Record<string, string>; min_employees: string; max_employees: string; min_score: string; weights: Record<string, string>;
}

function toForm(icp: Icp | null): Form {
  const i = icp ?? {};
  return {
    lists: Object.fromEntries(LIST_FIELDS.map((f) => [f.key, ((i[f.key] as string[] | undefined) ?? []).join(", ")])),
    min_employees: i.min_employees != null ? String(i.min_employees) : "",
    max_employees: i.max_employees != null ? String(i.max_employees) : "",
    min_score: i.min_score != null ? String(i.min_score) : "",
    weights: Object.fromEntries(DIMENSIONS.map((d) => [d.name, i.weights?.[d.name] != null ? String(i.weights[d.name]) : ""])),
  };
}

function fromForm(f: Form): Icp {
  const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const num = (s: string) => (s.trim() === "" ? null : Number(s));
  const weights = Object.fromEntries(Object.entries(f.weights).filter(([, v]) => v.trim() !== "").map(([k, v]) => [k, Number(v)]));
  const icp: Icp = {
    ...Object.fromEntries(LIST_FIELDS.map((l) => [l.key, split(f.lists[l.key as string] ?? "")])),
    min_employees: num(f.min_employees), max_employees: num(f.max_employees),
  };
  if (f.min_score.trim() !== "") icp.min_score = Number(f.min_score);
  if (Object.keys(weights).length) icp.weights = weights;
  return icp;
}

function validate(f: Form): string | null {
  const bad = (s: string) => s.trim() !== "" && (!/^\d+$/.test(s.trim()));
  if (bad(f.min_employees) || bad(f.max_employees)) return "Employee counts must be whole numbers.";
  if (f.min_employees && f.max_employees && Number(f.min_employees) > Number(f.max_employees)) return "Minimum employees cannot exceed maximum employees.";
  if (bad(f.min_score) || (f.min_score && Number(f.min_score) > 100)) return "Minimum score must be a whole number from 0 to 100.";
  if (Object.values(f.weights).some((w) => bad(w) || (w && Number(w) > 100))) return "Weights must be whole numbers from 0 to 100.";
  return null;
}

function IcpEditor({ campaign, canEdit, currentQualified, currentEvaluated, currentOutcome, onSaved }: {
  campaign: Campaign; canEdit: boolean; currentQualified: number; currentEvaluated: number;
  currentOutcome: Map<string, boolean | null>; onSaved: () => void;
}) {
  const [form, setForm] = useState<Form>(() => toForm(campaign.icp));
  const [editing, setEditing] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const { busy, run } = useAction();
  const confirm = useConfirm();
  useEffect(() => { if (!editing) setForm(toForm(campaign.icp)); }, [campaign.icp, editing]);

  const error = validate(form);
  const stopped = campaign.status === "stopped";
  const set = (patch: Partial<Form>) => { setForm((f) => ({ ...f, ...patch })); setPreview(null); };

  const doPreview = () => run("preview", async () => {
    setPreview(await api.post<Preview>(`/campaigns/${campaign.id}/qualification/preview`, { icp: fromForm(form) }));
  });

  const doSave = async () => {
    const ok = await confirm({
      title: "Save ICP and re-score prospects?",
      body: <p style={{ margin: 0 }}>The new criteria are saved to <b>{campaign.name}</b> and every prospect not yet contacted is re-scored.
        Prospects may move between qualified and disqualified. Prospects already contacted keep their pipeline stage.</p>,
      confirm: "Save & recalculate",
    });
    if (!ok) return;
    const r = await run("save", async () => {
      await api.patch(`/campaigns/${campaign.id}`, { icp: fromForm(form) });
      return api.post<{ rescored: number; qualified: number; disqualified: number }>(`/campaigns/${campaign.id}/qualification/recalculate`);
    }, (r) => `ICP saved. ${r.rescored} prospects re-scored: ${r.qualified} qualified, ${r.disqualified} disqualified.`);
    if (r) { setEditing(false); setPreview(null); onSaved(); }
  };

  const icp = campaign.icp ?? {};
  const flips = (i: PreviewItem) => currentOutcome.has(i.lead_id) && currentOutcome.get(i.lead_id) !== i.qualified;
  const changed = preview?.items.filter((i) => (i.current_score !== null && i.current_score !== i.new_score) || flips(i)) ?? [];
  const flipCount = preview?.items.filter(flips).length ?? 0;

  if (!editing) {
    return (
      <div className="card">
        <h2>ICP criteria — {campaign.name}
          {canEdit && !stopped && <button className="btn sm" onClick={() => setEditing(true)}>Edit criteria</button>}
        </h2>
        <dl className="kv">
          {LIST_FIELDS.map((f) => (
            <Fragment key={f.key}>
              <dt>{f.label}</dt>
              <dd>{((icp[f.key] as string[] | undefined) ?? []).length ? (icp[f.key] as string[]).join(", ") : <span className="muted">Any</span>}</dd>
            </Fragment>
          ))}
          <dt>Company size</dt>
          <dd>{icp.min_employees || icp.max_employees
            ? `${icp.min_employees?.toLocaleString() ?? "Any"} – ${icp.max_employees?.toLocaleString() ?? "any"} employees`
            : <span className="muted">Any</span>}</dd>
          <dt>Minimum score</dt><dd>{icp.min_score ?? <span className="muted">Default</span>}</dd>
          <dt>Weights</dt>
          <dd>{icp.weights && Object.keys(icp.weights).length
            ? Object.entries(icp.weights).map(([k, v]) => `${DIMENSIONS.find((d) => d.name === k)?.label ?? human(k)} ${v}`).join(" · ")
            : <span className="muted">Default weights</span>}</dd>
        </dl>
        {stopped && <div className="sub" style={{ marginTop: 10 }}>This campaign is stopped, so its ICP can no longer be changed.</div>}
      </div>
    );
  }

  return (
    <div className="card">
      <h2>Edit ICP — {campaign.name}</h2>
      <div className="form">
        <div className="sub">Separate multiple values with commas. Leave a field empty to accept any value.</div>
        {LIST_FIELDS.map((f) => (
          <label key={f.key}>{f.label}
            <input className="text" value={form.lists[f.key as string]} placeholder={f.hint}
                   onChange={(e) => set({ lists: { ...form.lists, [f.key]: e.target.value } })} />
          </label>
        ))}
        <div className="two">
          <label>Minimum employees<input className="text" inputMode="numeric" value={form.min_employees}
                                         onChange={(e) => set({ min_employees: e.target.value })} /></label>
          <label>Maximum employees<input className="text" inputMode="numeric" value={form.max_employees}
                                         onChange={(e) => set({ max_employees: e.target.value })} /></label>
          <label>Minimum score to qualify (0–100)<input className="text" inputMode="numeric" value={form.min_score} placeholder="Default"
                                                       onChange={(e) => set({ min_score: e.target.value })} /></label>
        </div>
        <details>
          <summary className="sub" style={{ cursor: "pointer" }}>Dimension weights (optional)</summary>
          <div className="sub" style={{ margin: "6px 0" }}>Override how many points each dimension contributes. Empty fields keep the default weight.</div>
          <div className="q-weights">
            {DIMENSIONS.map((d) => (
              <label key={d.name}>{d.label}
                <input className="text" inputMode="numeric" value={form.weights[d.name]} placeholder="Default"
                       onChange={(e) => set({ weights: { ...form.weights, [d.name]: e.target.value } })} />
              </label>
            ))}
          </div>
        </details>
        {error && <div className="banner bad" style={{ marginBottom: 0 }}>{error}</div>}
        <div className="row">
          <button className="btn" disabled={!!error || !!busy} onClick={doPreview}>{busy === "preview" ? "Scoring…" : "Preview impact"}</button>
          <button className="btn primary" disabled={!!error || !!busy} onClick={doSave}>{busy === "save" ? "Saving…" : "Save & recalculate"}</button>
          <button className="btn" disabled={!!busy} onClick={() => { setEditing(false); setPreview(null); }}>Cancel</button>
        </div>

        {preview && (
          <div className="grid" style={{ gap: 10 }}>
            <div className="sub">Preview only — nothing has been saved.</div>
            <div className="q-compare">
              <div><div className="v">{currentQualified}<span className="sub"> / {currentEvaluated}</span></div><div className="sub">Qualified now</div></div>
              <div><div className="v">{preview.qualified}<span className="sub"> / {preview.leads}</span></div><div className="sub">Would qualify</div></div>
              <div><div className="v">{preview.changed}</div><div className="sub">Scores that change</div></div>
              <div><div className="v">{flipCount}</div><div className="sub">Outcomes that change</div></div>
            </div>
            {changed.length > 0 ? (
              <div className="q-table-scroll">
                <table>
                  <thead><tr><th>Prospect</th><th>Score</th><th>Result</th></tr></thead>
                  <tbody>
                    {changed.map((i) => {
                      const d = i.current_score === null ? 0 : i.new_score - i.current_score;
                      return (
                        <tr key={i.lead_id}>
                          <td><b>{i.name}</b><div className="sub">{i.company ?? ""}</div></td>
                          <td style={{ whiteSpace: "nowrap" }}>
                            {i.current_score ?? "—"} → <b>{i.new_score}</b>{" "}
                            {d !== 0 && <span className={d > 0 ? "q-delta-up" : "q-delta-down"}>{d > 0 ? `+${d}` : d}</span>}
                          </td>
                          <td>
                            <Status value={i.fit} /> <Outcome q={i.qualified} />
                            {flips(i) && <div className="sub">was {currentOutcome.get(i.lead_id) ? "qualified" : "disqualified"}</div>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : <div className="sub">No existing scores or outcomes would change with these criteria.</div>}
          </div>
        )}
      </div>
    </div>
  );
}
