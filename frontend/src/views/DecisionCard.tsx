import { useState } from "react";
import { Decision } from "../api";
import { ago, Confidence, EngineBadge, human, Status } from "../ui";

// One Oxygen decision: what it proposed, why, on what evidence, what the guardrails said, what happened.
export default function DecisionCard({ d, onLead, showLead = true }: { d: Decision; onLead?: (id: string) => void; showLead?: boolean }) {
  const [open, setOpen] = useState(false);
  const g = d.guardrails;
  const details = d.details ?? {};
  const fallback = details.fallback_reason;
  return (
    <div className="decision">
      <div className="head">
        <div className="row">
          <b style={{ textTransform: "capitalize" }}>{human(d.action)}</b>
          {d.channel && <span className="badge">{d.channel}</span>}
          <Status value={d.status} />
          {d.requires_approval && <span className="badge warn">needs approval</span>}
        </div>
        <div className="row">
          <EngineBadge engine={d.engine} model={d.model} />
          <span className="muted" title={d.created_at}>{ago(d.created_at)}</span>
        </div>
      </div>
      {showLead && d.name !== undefined && (
        <div className="sub">
          {onLead ? (
            <a href="#" onClick={(e) => { e.preventDefault(); onLead(d.lead_id); }}>{d.name ?? "Unnamed prospect"}</a>
          ) : (d.name ?? "Unnamed prospect")}
          {d.company ? ` · ${d.company}` : ""}
        </div>
      )}
      <div>{d.reasoning ?? <span className="muted">No reasoning recorded</span>}</div>
      <div className="row">
        <span className="sub">Confidence</span> <Confidence value={d.confidence} />
        {d.provider && <span className="badge blue">provider: {d.provider}</span>}
        {d.task_status && <span className="sub">task: <Status value={d.task_status} /></span>}
      </div>
      {g && g.allowed === false && (
        <div className="banner bad" style={{ margin: 0 }}>
          Blocked by {(g.rule_ids ?? []).join(", ")}: {g.reason}
        </div>
      )}
      {fallback && <div className="sub">⚠ {fallback}</div>}
      <div>
        <button className="btn sm" onClick={() => setOpen(!open)}>{open ? "Hide details" : "Evidence, risks & parameters"}</button>
      </div>
      {open && (
        <dl className="kv">
          {details.situation_summary && (<><dt>Situation</dt><dd>{details.situation_summary}</dd></>)}
          <dt>Expected outcome</dt><dd>{d.expected_outcome ?? "—"}</dd>
          <dt>Uncertainty</dt><dd>{d.uncertainty ?? "—"}</dd>
          <dt>Risks</dt><dd>{d.risks?.length ? d.risks.join("; ") : "—"}</dd>
          {details.missing_information?.length > 0 && (<><dt>Missing info</dt><dd>{details.missing_information.join("; ")}</dd></>)}
          {details.interest_signals?.length > 0 && (<><dt>Interest signals</dt><dd>{details.interest_signals.join("; ")}</dd></>)}
          {details.alternatives?.length > 0 && (
            <><dt>Alternatives</dt><dd>{details.alternatives.map((a: any) => `${human(a.action)} — ${a.why_not}`).join("; ")}</dd></>
          )}
          <dt>Evidence</dt><dd className="mono">{d.evidence_refs?.length ? d.evidence_refs.map((r) => r.slice(0, 8)).join(", ") : "—"}</dd>
          <dt>Guardrails</dt><dd>{g ? (g.allowed ? `passed${g.warnings?.length ? ` (warnings: ${g.warnings.join("; ")})` : ""}` : `blocked: ${g.reason}`) : "not evaluated (no task)"}</dd>
          <dt>Parameters</dt><dd><pre className="json">{JSON.stringify(d.parameters ?? {}, null, 1)}</pre></dd>
          {d.task_result && (<><dt>Result</dt><dd><pre className="json">{JSON.stringify(d.task_result, null, 1)}</pre></dd></>)}
        </dl>
      )}
    </div>
  );
}
