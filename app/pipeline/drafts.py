"""
Drafts: every message Oxygen writes (first touch, follow-up, reply) becomes a versioned
``Draft`` with the text, why it is personalised that way, the evidence it used and the
messaging strategy. People can regenerate, edit and save drafts; submitting one sends it
to the Approval Centre (it is never sent without approval).
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import Campaign, Draft

SIGNAL_KINDS = ("news", "hiring", "technology", "expansion", "buying_signal", "digital_transformation", "challenge",
                "intent_signal", "research", "brief")


async def _next_version(db: AsyncSession, lead_id, kind: str, step: Optional[int]) -> int:
    v = (await db.execute(select(func.max(Draft.version)).where(Draft.lead_id == lead_id, Draft.kind == kind,
                                                               Draft.step.is_(None) if step is None
                                                               else Draft.step == step))).scalar()
    return (v or 0) + 1


async def _evidence(db: AsyncSession, refs: list[str]) -> list[dict]:
    from app.models.memory import ResearchFinding
    out = []
    for ref in refs:
        try:
            f = await db.get(ResearchFinding, uuid.UUID(str(ref)))
        except (ValueError, TypeError):
            f = None
        if f is not None:
            body = f.body or {}
            out.append({"ref": str(f.id), "kind": f.kind, "summary": f.summary,
                        "why_it_matters": body.get("why_it_matters"), "source": body.get("source") or f.source,
                        "simulated": bool(body.get("demo") or body.get("simulated"))})
        else:
            out.append({"ref": str(ref), "kind": "record", "summary": "CRM record"})
    return out


async def from_draft_task(db: AsyncSession, task, result: dict) -> Draft:
    """Record a completed draft_outreach task as a Draft (supersedes older unsent drafts of that kind)."""
    from app.models.base import Lead
    payload = json.loads(task.payload or "{}")
    lead = await db.get(Lead, task.lead_id)
    kind = "follow_up" if payload.get("follow_up_of") or (payload.get("step") or 1) > 1 else "initial"
    step = payload.get("step")
    campaign = await db.get(Campaign, lead.campaign_id) if lead and lead.campaign_id else None
    seq_angle = None
    if campaign and campaign.sequence and step:
        seq_angle = next((s.get("angle") for s in campaign.sequence if int(s.get("step", 0)) == int(step)), None)
    points = result.get("personalisation") or []
    evidence = await _evidence(db, [p.get("evidence_ref") for p in points if p.get("evidence_ref")])
    explanation = " ".join(
        [f"Opens with “{p['point']}”" + (f" — {e['why_it_matters']}" if e.get("why_it_matters") else "") + "."
         for p, e in zip(points, evidence)]) or "No personalisation evidence recorded."
    strategy = "; ".join(filter(None, [
        f"Sequence step {step or 1}: {seq_angle or payload.get('angle') or 'relevant initiative'}",
        f"Call to action: {result.get('call_to_action')}" if result.get("call_to_action") else None,
        f"Value: {campaign.value_points[0]}" if campaign and campaign.value_points else None]))
    await db.execute(update(Draft).where(Draft.lead_id == task.lead_id, Draft.kind == kind, Draft.status == "draft")
                     .values(status="superseded"))
    llm = result.get("llm") or {}
    d = Draft(tenant_id=task.tenant_id, lead_id=task.lead_id, campaign_id=lead.campaign_id if lead else None,
              kind=kind, step=step, version=await _next_version(db, task.lead_id, kind, step),
              subject=result.get("subject"), body=result.get("body") or "", explanation=explanation,
              strategy=strategy, evidence=evidence, status="draft", source_task_id=task.id,
              generated_by=f"{llm.get('vendor', 'provider')}/{llm.get('model', '')}".strip("/"))
    db.add(d)
    await db.flush()
    return d


async def from_reply_task(db: AsyncSession, task, result: dict) -> Draft:
    from app.models.base import Lead
    lead = await db.get(Lead, task.lead_id)
    supported = bool(result.get("supported"))
    d = Draft(tenant_id=task.tenant_id, lead_id=task.lead_id, campaign_id=lead.campaign_id if lead else None,
              kind="reply", version=await _next_version(db, task.lead_id, "reply", None),
              subject=result.get("subject"), body=result.get("body") or "", explanation=result.get("reasoning"),
              strategy=("Answer only from the approved knowledge base" if supported
                        else "Escalate: the question is not covered by the knowledge base"),
              evidence=result.get("evidence") or [], status="draft" if supported else "escalated",
              source_task_id=task.id, generated_by=result.get("generated_by"),
              reply_to_interaction_id=uuid.UUID(result["reply_interaction_id"]) if result.get("reply_interaction_id") else None)
    db.add(d)
    await db.flush()
    return d


async def mark_by_source_task(db: AsyncSession, source_task_id, **values: Any) -> None:
    if source_task_id:
        await db.execute(update(Draft).where(Draft.source_task_id == uuid.UUID(str(source_task_id))).values(**values))


async def generate(db: AsyncSession, lead, *, actor: str, angle: Optional[str] = None, variant: int = 0,
                   follow_up: bool = False) -> Draft:
    """Generate (or regenerate) a draft now through the normal draft_outreach capability."""
    from app.core.database import AsyncSessionLocal
    from app.execution.executor import TaskExecutor
    from app.models.base import Task
    payload = {"lead_id": str(lead.id), "channel": "email", "angle": angle, "variant": variant}
    if follow_up:
        payload["step"] = 2
    task = Task(tenant_id=lead.tenant_id, lead_id=lead.id, task_type="draft_outreach", status="pending",
                payload=json.dumps(payload), priority=7)
    db.add(task)
    await db.commit()
    async with AsyncSessionLocal() as s:
        outcome = await TaskExecutor().execute(task.id, s)
    if not outcome.success:
        raise ValueError(outcome.error_message or "draft generation failed")
    d = (await db.execute(select(Draft).where(Draft.source_task_id == task.id))).scalar_one()
    d.edited_by = None
    await db.flush()
    return d


async def save_edit(db: AsyncSession, draft: Draft, *, subject: Optional[str], body: str, actor: str) -> Draft:
    if draft.status not in ("draft", "rejected"):
        raise ValueError(f"a {draft.status} draft cannot be edited here (edit it in the Approval Centre)")
    draft.subject, draft.body, draft.edited_by, draft.status = subject, body, actor, "draft"
    # keep the source task's result in step so "send this draft" sends the edited text
    from app.models.base import Task
    if draft.source_task_id:
        t = await db.get(Task, draft.source_task_id)
        if t is not None:
            r = json.loads(t.result or "{}")
            r.update({"subject": subject, "body": body, "edited_by": actor})
            t.result = json.dumps(r)
    await db.flush()
    return draft


async def submit(db: AsyncSession, draft: Draft, *, actor: str) -> dict:
    """Propose sending this draft: creates the send task and its approval (never sends directly)."""
    from app.compliance.approvals import request_approval
    from app.models.base import Task
    if draft.status not in ("draft",):
        raise ValueError(f"only an unsent draft can be submitted (this one is {draft.status})")
    open_send = (await db.execute(select(Task).where(Task.lead_id == draft.lead_id, Task.task_type == "send_email",
                                                    Task.status.in_(("pending", "awaiting_approval", "in_progress")))
                                  )).scalars().first()
    if open_send is not None:
        raise ValueError("this prospect already has an email waiting for approval or sending")
    payload = {"lead_id": str(draft.lead_id), "draft_task_id": str(draft.source_task_id),
               "subject": draft.subject, "body": draft.body}
    task = Task(tenant_id=draft.tenant_id, lead_id=draft.lead_id, task_type="send_email", status="pending",
                payload=json.dumps(payload), priority=8,
                # same key the orchestrator would use: one send per draft, whoever proposes it
                idempotency_key=f"send_email:{draft.lead_id}:draft_task_id={draft.source_task_id}")
    db.add(task)
    await db.flush()
    approval = await request_approval(db, task, reasoning=f"Submitted by {actor} from the drafting workspace.",
                                      evidence=[e["ref"] for e in draft.evidence][:5])
    return {"task_id": str(task.id), "approval_id": str(approval.id)}


def view(d: Draft) -> dict:
    return {"id": str(d.id), "lead_id": str(d.lead_id), "campaign_id": str(d.campaign_id) if d.campaign_id else None,
            "kind": d.kind, "step": d.step, "version": d.version, "subject": d.subject, "body": d.body,
            "explanation": d.explanation, "strategy": d.strategy, "evidence": d.evidence, "status": d.status,
            "generated_by": d.generated_by, "edited_by": d.edited_by,
            "source_task_id": str(d.source_task_id) if d.source_task_id else None,
            "send_task_id": str(d.send_task_id) if d.send_task_id else None,
            "created_at": d.created_at, "updated_at": d.updated_at}
