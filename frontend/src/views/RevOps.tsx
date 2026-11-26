import { useState } from "react";
import { ErrorNote, human, pct, BarList, useData } from "../ui";

export default function RevOps() {
  const [days, setDays] = useState(30);
  const { data: d, error } = useData<any>(`/revops/dashboard?days=${days}`);
  const funnel: any[] = d?.pipeline?.funnel ?? [];
  const byChannel = Object.entries(d?.outreach?.by_channel ?? {}) as [string, any][];
  const overall = d?.outreach?.overall;
  const dec = d?.decisions;
  const appr = d?.approvals;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>RevOps</h1>
          <p>What is working, from attributed outcomes. Small samples are flagged and never turned into recommendations.</p>
        </div>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Time window">
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
      </div>
      <ErrorNote error={error} />
      <div className="grid kpis" style={{ marginBottom: 14 }}>
        <Tile v={overall?.n ?? "…"} l="Touches attributed" />
        <Tile v={pct(overall?.reply_rate)} l="Reply rate" weak={overall?.weak} />
        <Tile v={pct(overall?.positive_rate)} l="Positive reply rate" weak={overall?.weak} />
        <Tile v={pct(overall?.meeting_rate)} l="Meeting rate" weak={overall?.weak} />
        <Tile v={dec?.total ?? "…"} l="Oxygen decisions" />
        <Tile v={pct(dec?.fallback_rate)} l="Rules-fallback rate" />
        <Tile v={pct(appr?.edit_rate)} l="Approvals edited" />
        <Tile v={appr?.median_minutes_to_decision != null ? `${Math.round(appr.median_minutes_to_decision)}m` : "—"} l="Median time to approve" />
      </div>
      <div className="grid g2" style={{ marginBottom: 14 }}>
        <div className="card">
          <h2>Funnel <small>count per stage</small></h2>
          <BarList rows={funnel.map((s) => ({ label: s.stage, value: s.count, note: s.conversion_from_previous != null ? `${pct(s.conversion_from_previous)} of previous stage` : undefined }))} />
        </div>
        <div className="card">
          <h2>Positive reply rate by channel</h2>
          <BarList max={1} format={(v) => pct(v)} rows={byChannel.map(([k, v]) => ({ label: `${k}${v.weak ? " *" : ""}`, value: v.positive_rate ?? 0, note: `n=${v.n}` }))} />
          {byChannel.some(([, v]) => v.weak) && <div className="sub" style={{ marginTop: 8 }}>* fewer observations than the configured minimum — treat as anecdotal</div>}
        </div>
      </div>
      <div className="grid g2" style={{ marginBottom: 14 }}>
        <div className="card">
          <h2>Recommendations <small>require a human to act — nothing changes automatically</small></h2>
          {!d?.recommendations?.length && <div className="empty">Not enough evidence yet for a recommendation.</div>}
          {d?.recommendations?.map((r: any, i: number) => (
            <div className="decision" key={i} style={{ marginBottom: 8 }}>
              <div className="row"><span className="badge blue">{human(r.area)}</span>{r.evidence && <span className="sub">{typeof r.evidence === "string" ? r.evidence : JSON.stringify(r.evidence)}</span>}</div>
              <div>{r.suggestion}</div>
            </div>
          ))}
        </div>
        <div className="card">
          <h2>Decisions by engine and outcome</h2>
          <BarList rows={Object.entries(dec?.by_status ?? {}).map(([label, value]) => ({ label, value: value as number }))} />
          {dec && Object.keys(dec.blocked_by_rule ?? {}).length > 0 && (
            <div className="sub" style={{ marginTop: 8 }}>Blocked by rule: {Object.entries(dec.blocked_by_rule).map(([k, v]) => `${k} ×${v}`).join(", ")}</div>
          )}
        </div>
      </div>
      <div className="grid g2">
        <div className="card" style={{ padding: 0 }}>
          <h2 style={{ padding: "16px 16px 0" }}>Providers <small>success, latency, cost</small></h2>
          <table>
            <thead><tr><th>Provider</th><th>Capability</th><th>Calls</th><th>Success</th><th>Median latency</th></tr></thead>
            <tbody>
              {(d?.providers ?? []).map((p: any, i: number) => (
                <tr key={i}><td>{p.provider}</td><td className="sub">{human(p.capability)}</td><td>{p.success + p.failed}</td><td>{pct(p.success_rate)}</td><td>{p.median_latency_ms != null ? `${Math.round(p.median_latency_ms)}ms` : "—"}</td></tr>
              ))}
            </tbody>
          </table>
          {!d?.providers?.length && <div className="empty">No provider calls in this window</div>}
        </div>
        <div className="card">
          <h2>Segments <small>positive rate by industry</small></h2>
          <BarList max={1} format={(v) => pct(v)} rows={Object.entries(d?.segments?.industry ?? {}).map(([k, v]: [string, any]) => ({ label: `${k}${v.weak ? " *" : ""}`, value: v.positive_rate ?? 0, note: `n=${v.n}` }))} />
          <div className="sub" style={{ marginTop: 8 }}>Costs: {d?.costs?.total ?? 0} total{d?.costs?.per_meeting != null ? ` · ${d.costs.per_meeting} per meeting` : ""}</div>
        </div>
      </div>
    </>
  );
}

function Tile({ v, l, weak }: { v: any; l: string; weak?: boolean }) {
  return (
    <div className="card kpi" title={weak ? "Small sample — not statistically meaningful yet" : undefined}>
      <div className="v">{v}{weak && <span className="badge" style={{ marginLeft: 6, fontSize: 11 }}>small n</span>}</div>
      <div className="l">{l}</div>
    </div>
  );
}
