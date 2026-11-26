import { useEffect, useMemo, useState } from "react";
import { qs } from "../api";
import type { Nav } from "../App";
import { DemoTag, Empty, ErrorNote, human, Loading, Pager, Status, useData } from "../ui";
import type { Campaign } from "./Campaigns";
import "./accounts.css";

interface Contact {
  lead_id: string; person_id: string; name: string; title: string | null; department: string | null;
  seniority: string | null; email: string | null; location: string | null; verification_status: string;
  source: string | null; company: string | null; company_domain: string | null; lead_status: string;
  score: number | null; campaign_id: string | null; demo: boolean;
}
interface ContactPage { total: number; page: number; page_size: number; items: Contact[] }

const PAGE_SIZE = 25;
const VERIFICATION = ["verified", "unverified", "guessed"];
const LEAD_STAGES = ["new", "enriching", "qualified", "contacted", "replied", "meeting_booked", "closed_won",
  "closed_lost", "disqualified", "unsubscribed"];

export default function Contacts({ nav }: { nav: Nav }) {
  const campaignId = nav.params.get("campaign") ?? "";
  const { data: campaigns } = useData<Campaign[]>("/campaigns");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [verification, setVerification] = useState("");
  const [stage, setStage] = useState(nav.params.get("status") ?? "");
  const [department, setDepartment] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => { const t = window.setTimeout(() => setQ(search), 250); return () => window.clearTimeout(t); }, [search]);
  useEffect(() => setPage(1), [q, verification, stage, department, campaignId]);

  const { data, error, loading } = useData<ContactPage>(
    `/contacts${qs({ campaign_id: campaignId, search: q, verification, lead_status: stage, department, page, page_size: PAGE_SIZE })}`);

  // The API has no department list, so offer the departments seen so far plus those in campaign ICPs.
  const [seen, setSeen] = useState<string[]>([]);
  useEffect(() => {
    const add = (data?.items ?? []).map((c) => c.department).filter((d): d is string => !!d);
    if (add.some((d) => !seen.includes(d))) setSeen((s) => Array.from(new Set([...s, ...add])));
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps
  const departments = useMemo(() => Array.from(new Set([
    ...seen, ...(campaigns ?? []).flatMap((c) => c.icp?.departments ?? []), ...(department ? [department] : []),
  ])).sort(), [seen, campaigns, department]);

  const campaignName = (id: string | null) => campaigns?.find((c) => c.id === id)?.name;
  const filtered = !!(q || verification || stage || department);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Contacts</h1>
          <p>Stage 3 · Contact identification — the people Oxygen found at your target accounts, and how their emails were verified.</p>
        </div>
      </div>
      <ErrorNote error={error} />
      <div className="toolbar acc-toolbar">
        <select value={campaignId} aria-label="Campaign"
                onChange={(e) => (e.target.value ? nav.go("contacts", { campaign: e.target.value }) : nav.go("contacts"))}>
          <option value="">All campaigns</option>
          {campaigns?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <input className="search grow" placeholder="Search name, title, email or company…" value={search}
               onChange={(e) => setSearch(e.target.value)} aria-label="Search contacts" />
        <select value={verification} onChange={(e) => setVerification(e.target.value)} aria-label="Filter by email verification">
          <option value="">Any verification</option>
          {VERIFICATION.map((v) => <option key={v} value={v}>{human(v)}</option>)}
        </select>
        <select value={stage} onChange={(e) => setStage(e.target.value)} aria-label="Filter by lead stage">
          <option value="">All lead stages</option>
          {LEAD_STAGES.map((s) => <option key={s} value={s}>{human(s)}</option>)}
        </select>
        <select value={department} onChange={(e) => setDepartment(e.target.value)} aria-label="Filter by department">
          <option value="">All departments</option>
          {departments.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
      </div>

      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        {loading && !data ? <Loading /> : !data?.items.length ? (
          filtered ? <Empty title="No contacts match these filters" hint="Clear the search or filters to see every contact." />
            : <Empty title="No contacts identified yet"
                     hint="Contacts appear once lead discovery runs. Start the pipeline from Campaigns or the Demo Control Centre."
                     action={<button className="btn" onClick={() => nav.go("accounts", campaignId ? { campaign: campaignId } : undefined)}>Go to Target Accounts</button>} />
        ) : (
          <table className="acc-table">
            <thead>
              <tr><th>Contact</th><th>Department</th><th>Company</th><th>Email</th><th>Verification</th><th>Source</th><th>Lead stage</th><th>Score</th></tr>
            </thead>
            <tbody>
              {data.items.map((c) => (
                <tr key={c.lead_id} className="hover" onClick={() => nav.openLead(c.lead_id)} tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && nav.openLead(c.lead_id)}>
                  <td className="wrap">
                    <b>{c.name || <span className="muted">Unnamed</span>}</b>
                    <div className="sub">{[c.title, c.seniority && !c.title?.includes(c.seniority) ? c.seniority : null].filter(Boolean).join(" · ") || "—"}</div>
                  </td>
                  <td>{c.department ?? <span className="muted">—</span>}</td>
                  <td>
                    {c.company ?? <span className="muted">—</span>}
                    <div className="sub">{c.company_domain ?? ""}{!campaignId && campaignName(c.campaign_id) ? ` · ${campaignName(c.campaign_id)}` : ""}</div>
                  </td>
                  <td>
                    <span className="mono">{c.email ?? "—"}</span>{" "}
                    {c.demo && <DemoTag title="Demo data — a fictional business email address on a reserved .example domain" />}
                    {c.location && <div className="sub">{c.location}</div>}
                  </td>
                  <td><Status value={c.verification_status} /></td>
                  <td className="sub">{c.source ? human(c.source) : "—"}</td>
                  <td><Status value={c.lead_status} /></td>
                  <td>{c.score ?? <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data && <div style={{ padding: "0 12px 10px" }}><Pager page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} /></div>}
      </div>
    </>
  );
}
