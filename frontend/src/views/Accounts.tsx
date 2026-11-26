import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import { api, Json, qs } from "../api";
import type { Nav } from "../App";
import { useCan } from "../auth";
import {
  ago, DemoTag, Empty, ErrorNote, human, Loading, Modal, Pager, Status, useAction, useConfirm, useData,
} from "../ui";
import type { Campaign } from "./Campaigns";
import "./accounts.css";

interface Account {
  id: string; campaign_id: string; company_id: string; name: string; domain: string; website: string | null;
  industry: string | null; employees: number | null; city: string | null; country: string | null;
  technologies: string[]; funding_stage: string | null; priority: "high" | "medium" | "low"; selected: boolean;
  discovery_status: string; discovery_source: string | null; prospects_found: number | null;
  discovered_at: string | null; demo: boolean;
}
interface AccountPage { total: number; page: number; page_size: number; industries: string[]; items: Account[] }

const PAGE_SIZE = 25;
const PRIORITIES = ["high", "medium", "low"] as const;
const DISCOVERY = ["pending", "discovering", "discovered", "failed"];

function useDebounced(v: string, ms = 250) {
  const [d, setD] = useState(v);
  useEffect(() => { const t = window.setTimeout(() => setD(v), ms); return () => window.clearTimeout(t); }, [v, ms]);
  return d;
}

export default function Accounts({ nav }: { nav: Nav }) {
  const can = useCan();
  const confirm = useConfirm();
  const { busy, run } = useAction();
  const { data: campaigns, error: cErr, loading: cLoading, reload: reloadCampaigns } = useData<Campaign[]>("/campaigns");
  const campaignId = nav.params.get("campaign") || campaigns?.[0]?.id || "";
  const campaign = campaigns?.find((c) => c.id === campaignId) ?? null;

  const [search, setSearch] = useState("");
  const [industry, setIndustry] = useState("");
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [page, setPage] = useState(1);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [q, industry, status, priority, campaignId]);

  const path = campaignId
    ? `/campaigns/${campaignId}/accounts${qs({ search: q, industry, discovery_status: status, priority, page, page_size: PAGE_SIZE })}`
    : null;
  const { data, error, loading, reload } = useData<AccountPage>(path);
  const [modal, setModal] = useState<"add" | "import" | null>(null);

  const writable = can.operate && campaign && campaign.status !== "stopped";
  const filtered = !!(q || industry || status || priority);

  const patch = (a: Account, body: { priority?: string; selected?: boolean }) =>
    run(`row-${a.id}`, () => api.patch(`/campaigns/${campaignId}/accounts/${a.id}`, body),
      body.selected === undefined ? `${a.name}: priority set to ${body.priority}.` : `${a.name} ${body.selected ? "selected" : "deselected"}.`)
      .then(reload);

  const discoverResult = (r: Json) =>
    `Discovery finished: ${r.contacts ?? 0} contacts at ${r.accounts ?? 0} accounts, ${r.leads_created ?? 0} new leads (${r.provider ?? "provider"}).`;

  const discoverAll = async () => {
    const ok = await confirm({
      title: "Run lead discovery?", confirm: "Run discovery",
      body: <p style={{ margin: 0 }}>Oxygen searches for contacts at every <b>selected</b> account whose discovery is pending or
        failed, and adds them as leads to <b>{campaign?.name}</b>.</p>,
    });
    if (!ok) return;
    await run("discover", () => api.post<Json>(`/campaigns/${campaignId}/discover`, {}), discoverResult);
    reload(); reloadCampaigns();
  };

  const discoverOne = async (a: Account) => {
    if (a.discovery_status === "discovered") {
      const ok = await confirm({
        title: `Discover ${a.name} again?`, confirm: "Run again",
        body: <p style={{ margin: 0 }}>{a.prospects_found ?? 0} contacts were already found here. Running again searches for new
          contacts; existing people are matched, not duplicated.</p>,
      });
      if (!ok) return;
    }
    await run(`row-${a.id}`, () => api.post<Json>(`/campaigns/${campaignId}/discover`, { account_ids: [a.id] }), discoverResult);
    reload(); reloadCampaigns();
  };

  const startPipeline = async () => {
    const ok = await confirm({
      title: "Start the pipeline?", confirm: "Start pipeline",
      body: <p style={{ margin: 0 }}>Oxygen activates <b>{campaign?.name}</b> and runs lead discovery for every selected account.
        New leads move through qualification, research and drafting; every outbound email still waits for approval.</p>,
    });
    if (!ok) return;
    await run("start", () => api.post<Json>(`/campaigns/${campaignId}/start`),
      (r) => `Pipeline ${human(r.status)}: ${r.contacts ?? 0} contacts at ${r.accounts ?? 0} accounts, ${r.leads_created ?? 0} new leads.`);
    reload(); reloadCampaigns();
  };

  const head = (
    <div className="page-head">
      <div>
        <h1>Target Accounts</h1>
        <p>Stages 1–2 · Target accounts and lead discovery — choose the companies to pursue and find the people at them.</p>
      </div>
      {!!campaigns?.length && (
        <select value={campaignId} onChange={(e) => nav.go("accounts", { campaign: e.target.value })} aria-label="Campaign">
          {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name} ({human(c.status)})</option>)}
        </select>
      )}
    </div>
  );

  if (cLoading && !campaigns) return <>{head}<Loading /></>;
  if (!campaigns?.length) {
    return (
      <>
        {head}
        <ErrorNote error={cErr} />
        <div className="card">
          <Empty title="No campaigns yet" hint="Target accounts belong to a campaign. Create one in Campaigns, or load demo data from the Demo Control Centre."
                 action={<button className="btn primary" onClick={() => nav.go("campaigns")}>Go to Campaigns</button>} />
        </div>
      </>
    );
  }

  const icp = campaign?.icp ?? {};
  const size = icp.min_employees || icp.max_employees ? `${icp.min_employees ?? 1}–${icp.max_employees ?? "any"}` : "any";

  return (
    <>
      {head}
      <ErrorNote error={cErr ?? error} />
      {campaign && (
        <div className="card acc-icp">
          <Status value={campaign.status} />
          <div className="acc-icp-item"><span>Industries</span>{icp.industries?.length ? icp.industries.join(", ") : "any"}</div>
          <div className="acc-icp-item"><span>Employees</span>{size}</div>
          <div className="acc-icp-item"><span>Countries</span>{icp.countries?.length ? icp.countries.join(", ") : "any"}</div>
          <button className="btn sm" style={{ marginLeft: "auto" }} onClick={() => nav.go("campaigns", { campaign: campaign.id })}>
            {can.operate && campaign.status !== "stopped" ? "Edit ICP in Campaigns" : "View campaign"}
          </button>
        </div>
      )}
      {campaign?.status === "stopped" && <div className="banner warn"><span>This campaign is stopped — its accounts are read-only.</span></div>}

      <div className="toolbar acc-toolbar">
        <input className="search grow" placeholder="Search company or domain…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search accounts" />
        <select value={industry} onChange={(e) => setIndustry(e.target.value)} aria-label="Filter by industry">
          <option value="">All industries</option>
          {data?.industries.map((i) => <option key={i} value={i}>{i}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by discovery status">
          <option value="">Any discovery status</option>
          {DISCOVERY.map((s) => <option key={s} value={s}>{human(s)}</option>)}
        </select>
        <select value={priority} onChange={(e) => setPriority(e.target.value)} aria-label="Filter by priority">
          <option value="">Any priority</option>
          {PRIORITIES.map((p) => <option key={p} value={p}>{human(p)}</option>)}
        </select>
        {writable && (
          <>
            <button className="btn" onClick={() => setModal("add")}>Add company</button>
            <button className="btn" onClick={() => setModal("import")}>Import CSV</button>
            {campaign.status === "draft" ? (
              <button className="btn primary" disabled={!!busy} onClick={startPipeline}>{busy === "start" ? "Starting…" : "Start pipeline"}</button>
            ) : (
              <button className="btn primary" disabled={!!busy} onClick={discoverAll}>{busy === "discover" ? "Discovering…" : "Run discovery"}</button>
            )}
          </>
        )}
      </div>

      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        {loading && !data ? <Loading /> : !data?.items.length ? (
          filtered ? <Empty title="No accounts match these filters" hint="Clear the search or filters to see every target account." />
            : <Empty title="No target accounts yet"
                     hint={writable ? "Add companies manually or import a CSV, then run discovery to find contacts." : "Start the pipeline from Campaigns or the Demo Control Centre."} />
        ) : (
          <table className="acc-table">
            <thead>
              <tr>
                <th>Company</th><th>Industry</th><th>Employees</th><th>Location</th><th>Technologies</th>
                <th>Priority</th><th>Selected</th><th>Discovery</th><th>Contacts</th>{writable && <th />}
              </tr>
            </thead>
            <tbody>
              {data.items.map((a) => {
                const rowBusy = busy === `row-${a.id}`;
                return (
                  <tr key={a.id}>
                    <td className="wrap">
                      <b>{a.name}</b> {a.demo && <DemoTag title="Fictional company generated for the demo" />}
                      <div className="sub">
                        {a.website ? <a href={a.website} target="_blank" rel="noopener noreferrer">{a.domain}</a> : a.domain}
                        {a.funding_stage ? ` · ${a.funding_stage}` : ""}
                      </div>
                    </td>
                    <td>{a.industry ?? <span className="muted">—</span>}</td>
                    <td>{a.employees?.toLocaleString() ?? <span className="muted">—</span>}</td>
                    <td>{[a.city, a.country].filter(Boolean).join(", ") || <span className="muted">—</span>}</td>
                    <td className="wrap">
                      {a.technologies.length ? (
                        <div className="chip-row">{a.technologies.slice(0, 4).map((t) => <span key={t} className="acc-chip">{t}</span>)}
                          {a.technologies.length > 4 && <span className="sub" title={a.technologies.slice(4).join(", ")}>+{a.technologies.length - 4}</span>}</div>
                      ) : <span className="muted">—</span>}
                    </td>
                    <td>
                      {writable ? (
                        <select value={a.priority} disabled={rowBusy} aria-label={`Priority for ${a.name}`}
                                onChange={(e) => patch(a, { priority: e.target.value })}>
                          {PRIORITIES.map((p) => <option key={p} value={p}>{human(p)}</option>)}
                        </select>
                      ) : <span className={`badge ${a.priority === "high" ? "blue" : ""}`}>{human(a.priority)}</span>}
                    </td>
                    <td>
                      <input type="checkbox" checked={a.selected} disabled={!writable || rowBusy} aria-label={`Select ${a.name} for discovery`}
                             onChange={(e) => patch(a, { selected: e.target.checked })} />
                    </td>
                    <td>
                      <Status value={a.discovery_status} />
                      <div className="sub">{[a.discovery_source, a.discovered_at ? ago(a.discovered_at) : null].filter(Boolean).join(" · ")}</div>
                    </td>
                    <td>
                      {a.prospects_found ? (
                        <a href="#" onClick={(e) => { e.preventDefault(); nav.go("contacts", { campaign: campaignId }); }}>{a.prospects_found}</a>
                      ) : <span className="muted">{a.prospects_found ?? "—"}</span>}
                    </td>
                    {writable && (
                      <td>
                        <button className="btn sm" disabled={!!busy || !a.selected} onClick={() => discoverOne(a)}
                                title={a.selected ? "Find contacts at this company now" : "Select the account first"}>
                          {rowBusy ? "Working…" : a.discovery_status === "discovered" ? "Rediscover" : "Discover"}
                        </button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {data && <div style={{ padding: "0 12px 10px" }}><Pager page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} /></div>}
      </div>

      {modal === "add" && campaign && (
        <AddCompany campaignId={campaign.id} onClose={() => setModal(null)} onDone={() => { setModal(null); reload(); reloadCampaigns(); }} />
      )}
      {modal === "import" && campaign && (
        <ImportCsv campaignId={campaign.id} onClose={() => setModal(null)} onDone={() => { setModal(null); reload(); reloadCampaigns(); }} />
      )}
    </>
  );
}

function AddCompany({ campaignId, onClose, onDone }: { campaignId: string; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ name: "", domain: "", industry: "", employees: "", city: "", country: "", technologies: "", priority: "medium" });
  const [err, setErr] = useState<string | null>(null);
  const { busy, run } = useAction();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((s) => ({ ...s, [k]: e.target.value }));

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    setErr(null);
    const domain = f.domain.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
    if (!domain.includes(".")) return setErr("Enter the company's domain, for example acme.com.");
    const account = {
      name: f.name.trim() || null, domain, industry: f.industry.trim() || null,
      employees: f.employees ? Number(f.employees) : null, city: f.city.trim() || null, country: f.country.trim() || null,
      technologies: f.technologies.split(",").map((t) => t.trim()).filter(Boolean), priority: f.priority,
    };
    const r = await run("add", () => api.post<Json>(`/campaigns/${campaignId}/accounts`, { accounts: [account] }),
      `${account.name ?? domain} added and selected for discovery.`);
    if (r) onDone();
  };

  return (
    <Modal title="Add company" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!!busy} onClick={() => submit()}>{busy ? "Adding…" : "Add company"}</button>
    </>}>
      <form className="form" onSubmit={submit}>
        {err && <div className="banner bad" style={{ marginBottom: 0 }}>{err}</div>}
        <div className="two">
          <label>Company name<input className="text" value={f.name} onChange={set("name")} autoFocus /></label>
          <label>Domain (required)<input className="text" value={f.domain} onChange={set("domain")} placeholder="acme.com" required /></label>
          <label>Industry<input className="text" value={f.industry} onChange={set("industry")} /></label>
          <label>Employees<input className="text" type="number" min={1} value={f.employees} onChange={set("employees")} /></label>
          <label>City<input className="text" value={f.city} onChange={set("city")} /></label>
          <label>Country<input className="text" value={f.country} onChange={set("country")} /></label>
        </div>
        <label>Technologies <span className="muted">comma-separated</span><input className="text" value={f.technologies} onChange={set("technologies")} /></label>
        <label>Priority
          <select value={f.priority} onChange={set("priority")}>
            {PRIORITIES.map((p) => <option key={p} value={p}>{human(p)}</option>)}
          </select>
        </label>
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

const CSV_EXAMPLE = `name,domain,industry,employees,city,country,technologies,priority
Acme Analytics,acme-analytics.example,Software,220,London,United Kingdom,Salesforce;HubSpot,high`;

function ImportCsv({ campaignId, onClose, onDone }: { campaignId: string; onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState("");
  const { busy, run } = useAction();
  const rows = text.trim() ? Math.max(0, text.trim().split(/\r?\n/).length - 1) : 0;

  const readFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    file.text().then(setText);
  };
  const submit = async () => {
    const r = await run("import", () => api.post<Json>(`/campaigns/${campaignId}/accounts/import`, { csv: text }),
      (r) => `${r.imported ?? 0} accounts imported and selected for discovery.`);
    if (r) onDone();
  };

  return (
    <Modal wide title="Import target accounts from CSV" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!!busy || !rows} onClick={submit}>{busy ? "Importing…" : `Import ${rows || ""} row${rows === 1 ? "" : "s"}`}</button>
    </>}>
      <div className="sub">
        The first row must be a header. Only <span className="mono">domain</span> is required; separate technologies with
        semicolons. Priority is high, medium or low. Up to 500 rows per import; companies already in the campaign are updated.
      </div>
      <pre className="json">{CSV_EXAMPLE}</pre>
      <div className="row">
        <label className="btn sm" style={{ cursor: "pointer" }}>
          Choose file
          <input type="file" accept=".csv,text/csv,text/plain" onChange={readFile} hidden />
        </label>
        <button className="btn sm" onClick={() => setText((t) => t.trim() ? t : CSV_EXAMPLE.split("\n")[0] + "\n")}>Insert header row</button>
      </div>
      <textarea rows={10} className="mono" value={text} onChange={(e) => setText(e.target.value)} aria-label="CSV content"
                placeholder="Paste CSV here or choose a file" />
    </Modal>
  );
}
