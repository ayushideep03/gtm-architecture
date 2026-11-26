"""
Sales Controller planning contract: the structured proposal schema and the
prompt built from prospect memory.

The schema is strict JSON (every field required, no extra keys) so it works
with both OpenAI strict json_schema and Anthropic output_config.format.
Free-form capability inputs travel as ``parameters_json`` (a JSON string) and
are validated against the capability's own input schema afterwards.

The allowed ``action`` values are generated per lead from the capability
catalogue + live availability, so the model can only choose something that
exists right now; guardrails still validate everything it proposes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from app.core.gtm_config import GTMConfig

NON_TASK_ACTIONS = ["wait", "stop", "escalate"]

SYSTEM_PROMPT = """You are Oxygen, the sales controller of an autonomous B2B sales system.

For ONE prospect at a time you decide the single next best action. You do not execute anything:
your output is a proposal. Deterministic guardrails, compliance checks (suppression, lawful basis,
contact frequency) and human approval of every external message are enforced after you, so never
try to work around them, and never claim an action has happened.

How to decide:
1. Read the prospect memory. Separate what is verified (band "verified"/"probable", status
   "applied") from what is only proposed or weak ("possible", status "proposed").
2. Identify what is missing for the campaign goal and what signals suggest interest or lack of it.
3. Decide whether the prospect is worth pursuing for this campaign.
4. Choose exactly one action from the allowed list:
   - a capability (enrichment, research, qualification, drafting, sending, meetings, CRM update);
   - "wait" when the right move is to give the prospect time or wait for a pending result
     (set wait_hours);
   - "stop" when pursuing further would be pointless, unwanted or non-compliant (e.g. the
     prospect declined or asked not to be contacted);
   - "escalate" when a human should decide (ambiguous reply, high stakes, conflicting evidence).
5. Prefer gathering missing information over guessing. Skip steps that add nothing (e.g. do not
   re-enrich fresh data or re-qualify without new information). Do not repeat an action that is
   pending, awaiting approval, or was just completed unless new information justifies it.
6. Never send generic follow-ups. A follow-up must use something new (research, a reply, a signal).
7. Never invent facts. Every claim in your reasoning must come from the memory; cite the ids of
   facts, findings, interactions or decisions you relied on in evidence_refs.
8. parameters_json must be a JSON object matching the chosen capability's input_schema
   (use "{}" for wait/stop/escalate). To send an email, reference the completed draft task
   with draft_task_id.
9. Be honest about confidence (0..1) and uncertainty, list concrete risks, and note the
   alternatives you rejected.
10. "learned_insights" summarises outcomes of past outreach to similar prospects. Use it as evidence
    for choosing channel, angle and sequence, weighed by sample size (n); "weak" groups are anecdotes.
    Never treat it as permission to skip research, approval or compliance.

Return only the JSON object required by the schema."""

PROMPT_VERSION = "planner-v1-" + hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:10]


class Alternative(BaseModel):
    action: str
    why_not: str


class ActionProposal(BaseModel):
    situation_summary: str
    missing_information: list[str]
    interest_signals: list[str]
    worth_pursuing: bool
    action: str
    channel: Optional[str]
    parameters_json: str
    reasoning: str
    evidence_refs: list[str]
    expected_outcome: str
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: str
    risks: list[str]
    needs_human_approval: bool
    wait_hours: Optional[float]
    alternatives_considered: list[Alternative]

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v):
        return min(max(float(v), 0.0), 1.0)

    def parameters(self) -> dict[str, Any]:
        value = json.loads(self.parameters_json or "{}")
        if not isinstance(value, dict):
            raise ValueError("parameters_json must encode a JSON object")
        return value


def proposal_schema(allowed_actions: list[str]) -> dict[str, Any]:
    """Strict JSON schema (all fields required, nullable where optional)."""
    s = {"type": "string"}
    arr = {"type": "array", "items": {"type": "string"}}
    props: dict[str, Any] = {
        "situation_summary": s,
        "missing_information": arr,
        "interest_signals": arr,
        "worth_pursuing": {"type": "boolean"},
        "action": {"type": "string", "enum": allowed_actions},
        "channel": {"type": ["string", "null"]},
        "parameters_json": s,
        "reasoning": s,
        "evidence_refs": arr,
        "expected_outcome": s,
        "confidence": {"type": "number"},
        "uncertainty": s,
        "risks": arr,
        "needs_human_approval": {"type": "boolean"},
        "wait_hours": {"type": ["number", "null"]},
        "alternatives_considered": {
            "type": "array",
            "items": {"type": "object", "properties": {"action": s, "why_not": s},
                      "required": ["action", "why_not"], "additionalProperties": False},
        },
    }
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


# ── Context rendering ─────────────────────────────────────────────────────────

def _trim(text: Any, limit: int = 600) -> Any:
    if isinstance(text, str) and len(text) > limit:
        return text[:limit] + "…"
    return text


def _facts(rows: list[dict]) -> list[dict]:
    return [{"id": f["id"], "field": f["field"], "value": _trim(f["value"], 300), "band": f["band"],
             "status": f["status"], "sources": sorted({e["kind"] for e in f["evidence"]}),
             "observed_at": f["observed_at"]} for f in rows]


def render_context(memory: dict, capabilities: list[dict], cfg: GTMConfig) -> str:
    """The planner's user message: campaign, prospect memory, capability menu."""
    ox = cfg.oxygen
    lead, person, company = memory["lead"], memory.get("person"), memory.get("company")
    doc = {
        "now": datetime.now(timezone.utc).isoformat(),
        "campaign": ox.campaign.model_dump(),
        "prospect": {
            "lead": {k: lead.get(k) for k in ("id", "status", "score", "source", "notes", "created_at")},
            "person": {k: person.get(k) for k in ("id", "first_name", "last_name", "email", "title", "seniority",
                                                  "department", "location", "linkedin_url")} if person else None,
            "company": {k: company.get(k) for k in ("id", "name", "domain", "industry", "employee_count",
                                                    "country", "funding_stage", "revenue_range", "technologies",
                                                    "description")} if company else None,
        },
        "facts": {k: _facts(v) for k, v in memory["facts"].items()},
        "findings": [{**{k: f[k] for k in ("id", "kind", "summary", "confidence", "source", "created_at")},
                      "citations": f.get("citations")} for f in memory["findings"]],
        "interactions": [{**{k: i[k] for k in ("id", "type", "direction", "channel", "subject", "occurred_at")},
                          "body": _trim(i.get("body"))}
                         for i in memory["interactions"][: ox.context_interaction_limit]],
        "decision_history": [{k: d[k] for k in ("id", "engine", "action", "status", "reasoning", "created_at")}
                             for d in memory["decisions"][: ox.context_decision_limit]],
        "tasks": [{"id": t["id"], "capability": t["capability"], "status": t["status"],
                   "result": _trim(json.dumps(t["result"], default=str), 800) if t["result"] else None,
                   "completed_at": t["completed_at"]} for t in memory["tasks"][:15]],
        "approvals": [{k: a[k] for k in ("id", "capability", "status", "decided_by", "decided_at")}
                      for a in memory["approvals"][:10]],
        "scheduled_work": memory["work_items"],
        "compliance": memory["compliance"],
        "learned_insights": memory.get("insights"),
        "capabilities": capabilities,
        "non_task_actions": {
            "wait": "Re-evaluate later; set wait_hours.",
            "stop": "End pursuit of this prospect for this campaign.",
            "escalate": "Ask a human to decide.",
        },
    }
    return json.dumps(doc, default=str, indent=1)


def evidence_ids(memory: dict) -> set[str]:
    ids: set[str] = {str(memory[k]["id"]) for k in ("lead", "person", "company") if memory.get(k)}
    for rows in memory["facts"].values():
        ids.update(f["id"] for f in rows)
    for key in ("findings", "interactions", "decisions", "tasks", "approvals", "events"):
        ids.update(str(r["id"]) for r in memory.get(key, []))
    return ids
