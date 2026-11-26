"""
Scripted demo "LLM" — NOT a language model.

Used only when the ``demo`` profile is selected (``GTM_PROFILE=demo``), so the
platform can be demonstrated end to end without an API key. It reads the same
context documents a real model receives and answers every structured request
(planner proposals, research, intent, qualification, drafting, reply
classification) with a transparent, deterministic playbook. Its output goes
through exactly the same validation, guardrails, compliance and approval
gates as a real model's output.

Everything it produces is labelled: vendor ``demo``, model
``scripted-demo-playbook``. The dashboard shows a banner whenever it is active.
Never configure it in production.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

MODEL = "scripted-demo-playbook"
_SENIOR = re.compile(r"\b(vp|vice president|head|chief|cro|ceo|coo|founder|director)\b", re.I)


def _doc(user: str) -> dict:
    try:
        return json.JSONDecoder().raw_decode(user.lstrip())[0]
    except ValueError:
        return {}


def _tail_json(user: str, marker: str) -> dict:
    if marker not in user:
        return {}
    return _doc(user.split(marker, 1)[1])


def _age_hours(ts: Optional[str]) -> float:
    if not ts:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return 0.0
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


class ScriptedDemoClient:
    vendor = "demo"

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    async def complete_json(self, *, model: str, system: str, user: str, schema: dict, schema_name: str,
                            **_: Any) -> tuple[dict, dict, str]:
        handler = {
            "action_proposal": self._plan,
            "prospect_research": self._research,
            "intent_assessment": self._intent,
            "qualification": self._qualify,
            "outreach_draft": self._draft,
            "reply_classification": self._reply,
        }.get(schema_name)
        if handler is None:
            from app.llm.base import LLMError
            raise LLMError(f"demo playbook has no script for {schema_name!r}", retryable=True, vendor="demo")
        return handler(user, schema), {"input_tokens": 0, "output_tokens": 0, "scripted": True}, MODEL

    # ── Planner ──────────────────────────────────────────────────────────────
    def _plan(self, user: str, schema: dict) -> dict:
        d = _doc(user)
        allowed = set(schema["properties"]["action"]["enum"])
        p = d.get("prospect", {})
        lead, person, company = p.get("lead") or {}, p.get("person") or {}, p.get("company") or {}
        tasks = d.get("tasks", [])
        findings = d.get("findings", [])
        inter = d.get("interactions", [])
        comp = d.get("compliance") or {}
        base_refs = [x for x in (lead.get("id"), person.get("id"), company.get("id")) if x]

        def done(cap: str) -> list[dict]:
            return [t for t in tasks if t["capability"] == cap and t["status"] in ("completed", "done")]

        def tried(cap: str) -> bool:
            return any(t["capability"] == cap for t in tasks)

        def open_(cap: str) -> bool:
            return any(t["capability"] == cap and t["status"] in ("pending", "in_progress", "awaiting_approval")
                       for t in tasks)

        def out(action, reasoning, refs=None, params=None, channel=None, conf=0.75, wait=None, expected="",
                risks=None, missing=None, signals=None, alternatives=None, worth=True):
            if action not in allowed:
                action, reasoning, wait = "wait", f"{reasoning} (capability not available right now; waiting)", 24
            return {"situation_summary": f"{person.get('first_name') or 'Prospect'} at {company.get('name') or 'unknown company'} "
                                         f"— lead status {lead.get('status')}.",
                    "missing_information": missing or [], "interest_signals": signals or [], "worth_pursuing": worth,
                    "action": action, "channel": channel, "parameters_json": json.dumps(params or {}),
                    "reasoning": reasoning, "evidence_refs": (refs or []) + base_refs[:1],
                    "expected_outcome": expected or "Moves the prospect one step forward.", "confidence": conf,
                    "uncertainty": "Scripted demo playbook; a language model would weigh the evidence more finely.",
                    "risks": risks or [], "needs_human_approval": False,
                    "wait_hours": wait, "alternatives_considered": alternatives or []}

        replies = [i for i in inter if i["direction"] == "inbound" and i["type"] == "email_received"]
        classified = [f for f in findings if f["kind"] == "reply_analysis"]
        if comp.get("suppressed"):
            return out("stop", "The prospect is suppressed (opted out or bounced); outreach must end.",
                       conf=0.98, worth=False, expected="No further contact.")
        if lead.get("status") == "meeting_booked":
            return out("escalate", "A meeting is booked. The account executive should take over preparation.",
                       conf=0.9, expected="Human owner prepares for the call.")
        if lead.get("status") == "disqualified":
            return out("stop", "Qualification found the prospect outside the ideal customer profile.",
                       refs=[f["id"] for f in findings if f["kind"] == "qualification"][:1], conf=0.85, worth=False)
        if replies:
            latest = classified[0] if classified else None
            cat = (latest["summary"].split(":", 1)[0] if latest else "unclassified")
            refs = [replies[0]["id"]] + ([latest["id"]] if latest else [])
            if cat in ("positive_interest", "meeting_request"):
                if tried("book_meeting"):
                    return out("wait", "Invitation already sent; waiting for the prospect to accept.", refs=refs,
                               wait=24, conf=0.8)
                return out("book_meeting", "The prospect replied with interest; propose a short discovery call.",
                           refs=refs, channel="meeting", conf=0.86,
                           params={"topic": f"Discovery call — {company.get('name')}", "duration_minutes": 30},
                           signals=[f"Reply: {replies[0].get('subject') or 'positive reply'}"],
                           expected="Prospect accepts the invitation.", risks=["Calendar conflicts"])
            if cat in ("not_interested", "unsubscribe"):
                return out("stop", "The prospect declined; respecting that and ending outreach.", refs=refs,
                           conf=0.93, worth=False)
            if cat == "not_now":
                return out("wait", "The prospect asked to reconnect later; re-evaluate in a month.", refs=refs,
                           wait=24 * 30, conf=0.8)
            return out("escalate", "The reply needs a human answer (question/objection or unclear).", refs=refs,
                       conf=0.7)

        if not company.get("industry") and not tried("enrich_company") and "enrich_company" in allowed:
            return out("enrich_company", "Company firmographics are missing; enrich before judging fit.",
                       missing=["industry", "employee_count"], conf=0.85, expected="Industry and size known.")
        if not any(f["kind"] in ("brief", "research", "intent_signal", "news") for f in findings) \
                and not tried("research_prospect") and "research_prospect" in allowed:
            return out("research_prospect", "No research yet; read the company's website for relevant signals.",
                       missing=["current initiatives"], conf=0.8)
        if not company.get("industry") and not person.get("title") and lead.get("status") in ("new", "enriching"):
            if any(w.get("kind") == "human_review" for w in d.get("scheduled_work") or []):
                return out("wait", "Already waiting for a human to supply the missing information.", wait=48,
                           conf=0.9, missing=["company industry", "company size", "contact's role"])
            return out("escalate", "Enrichment and research found no verifiable industry, size or role. "
                                   "Qualifying now would be a guess, so a human should confirm or source the data.",
                       missing=["company industry", "company size", "contact's role"], conf=0.88,
                       expected="A person adds the missing facts or discards the lead.",
                       alternatives=[{"action": "qualify_lead", "why_not": "No evidence to score fit on."},
                                     {"action": "draft_outreach", "why_not": "Cannot personalise without facts."}])
        if not tried("analyze_intent") and "analyze_intent" in allowed:
            return out("analyze_intent", "Assess buying intent from the research before investing in outreach.",
                       refs=[f["id"] for f in findings][:3], conf=0.78)
        if lead.get("status") in ("new", "enriching") and not tried("qualify_lead") and "qualify_lead" in allowed:
            return out("qualify_lead", "Enough evidence to score fit against the ideal customer profile.",
                       refs=[f["id"] for f in findings][:3], conf=0.8)

        drafts, sends = done("draft_outreach"), [t for t in tasks if t["capability"] == "send_email"]
        sent = [t for t in sends if t["status"] in ("completed", "done")]
        outbound = [i for i in inter if i["direction"] == "outbound" and i["type"] == "email_sent"]
        if open_("send_email") or open_("draft_outreach"):
            return out("wait", "An outreach step is already waiting (approval or execution).", wait=4, conf=0.9)
        used = set()
        for t in sends:
            m = re.search(r'"draft_task_id":\s*"([^"]+)"', t.get("result") or "")
            if m:
                used.add(m.group(1))
        fresh = [t for t in drafts if t["id"] not in used and len(drafts) > len(sends)]
        if not outbound:
            if fresh:
                return out("send_email", "A personalised draft is ready; propose sending it (human approval required).",
                           refs=[fresh[0]["id"]], channel="email", params={"draft_task_id": fresh[0]["id"]},
                           conf=0.8, expected="Prospect reads and replies.", risks=["No reply"])
            return out("draft_outreach", "Qualified with evidence; write a first personalised email.",
                       refs=[f["id"] for f in findings][:3], channel="email",
                       params={"channel": "email", "angle": "relevant initiative from research"}, conf=0.82)
        last_age = _age_hours(outbound[0]["occurred_at"])
        follow_after = 72
        if last_age < follow_after:
            return out("wait", f"Email sent {last_age:.0f}h ago; give the prospect time to reply.",
                       refs=[outbound[0]["id"]], wait=round(follow_after - last_age, 1), conf=0.85)
        if len(outbound) >= 2:
            return out("stop", f"No reply after {len(outbound)} emails; ending this sequence respectfully.",
                       refs=[outbound[0]["id"]], conf=0.82, worth=False)
        if fresh:
            return out("send_email", "Follow-up draft is ready; propose sending it (human approval required).",
                       refs=[fresh[0]["id"]], channel="email", params={"draft_task_id": fresh[0]["id"]}, conf=0.72)
        return out("draft_outreach", "No reply after the first email; write a short follow-up that adds something new.",
                   refs=[outbound[0]["id"]], channel="email",
                   params={"channel": "email", "follow_up_of": outbound[0]["id"]}, conf=0.7)

    # ── Capabilities ─────────────────────────────────────────────────────────
    def _research(self, user: str, schema: dict) -> dict:
        d = _doc(user)
        findings = []
        for page in d.get("pages", [])[:3]:
            for sentence in re.split(r"(?<=[.!?])\s+", page.get("text", "")):
                s = sentence.strip()
                if 30 <= len(s) <= 280 and len(findings) < 4:
                    findings.append({"kind": "other", "statement": s, "source_url": page["url"], "quote": s,
                                     "confidence": 0.6})
        return {"company_summary": findings[0]["statement"] if findings else "Website had little usable text.",
                "findings": findings, "facts": [], "gaps": ["scripted demo research: sentences copied verbatim"]}

    def _intent(self, user: str, schema: dict) -> dict:
        d = _doc(user)
        sig = [{"signal": f["summary"][:160], "evidence_ref": f["id"], "strength": "moderate"}
               for f in d.get("findings", []) if f["kind"] in ("intent_signal", "news", "research", "brief")][:3]
        replies = [i for i in d.get("interactions", []) if i["direction"] == "inbound"]
        sig += [{"signal": "Prospect replied", "evidence_ref": i["id"], "strength": "strong"} for i in replies[:1]]
        score = min(90, 25 + 18 * len(sig))
        level = "high" if score >= 70 else "medium" if score >= 45 else "low"
        return {"intent_level": level, "score": score, "signals": sig, "counter_signals": [],
                "timing_hypothesis": "Act within the next two weeks" if sig else "No timing evidence",
                "rationale": f"{len(sig)} cited signal(s) in memory."}

    def _qualify(self, user: str, schema: dict) -> dict:
        d = _doc(user)
        p = d.get("prospect", {})
        company, person = p.get("company") or {}, p.get("person") or {}
        dims = []
        industry = (company.get("industry") or "").lower()
        ind_ok = any(k in industry for k in ("software", "saas", "technology", "internet"))
        dims.append({"name": "company fit", "assessment": f"Industry: {company.get('industry') or 'unknown'}",
                     "score": 85 if ind_ok else 30, "evidence_ref": company.get("id")})
        emp = company.get("employee_count")
        size_ok = isinstance(emp, int) and 50 <= emp <= 1000
        dims.append({"name": "size", "assessment": f"{emp or 'unknown'} employees", "score": 80 if size_ok else 35,
                     "evidence_ref": company.get("id")})
        senior = bool(_SENIOR.search(person.get("title") or ""))
        dims.append({"name": "persona", "assessment": person.get("title") or "unknown title",
                     "score": 85 if senior else 40, "evidence_ref": person.get("id")})
        intent = [f for f in d.get("findings", []) if f["kind"] == "intent_assessment"]
        dims.append({"name": "need/timing", "assessment": intent[0]["summary"][:140] if intent else "no intent evidence",
                     "score": 75 if intent else 45, "evidence_ref": intent[0]["id"] if intent else None})
        score = round(sum(x["score"] for x in dims) / len(dims))
        disq = [] if ind_ok else ["Outside target industries"]
        return {"fit_score": score, "qualified": score >= 60 and not disq, "dimensions": dims, "disqualifiers": disq,
                "rationale": f"Average of {len(dims)} scored dimensions.",
                "persona_fit": "strong" if senior else "weak"}

    def _draft(self, user: str, schema: dict) -> dict:
        d = _doc(user)
        brief = _tail_json(user, "DRAFT BRIEF:")
        p = d.get("prospect", {})
        company, person = p.get("company") or {}, p.get("person") or {}
        finding = next((f for f in d.get("findings", []) if f["kind"] in ("intent_signal", "news", "research", "brief")),
                       None)
        hook = finding["summary"].rstrip(".") if finding else f"{company.get('name')}'s growth"
        sender = brief.get("sender") or {}
        first = person.get("first_name") or "there"
        follow = bool(brief.get("follow_up_of"))
        if follow:
            subject = f"Re: {company.get('name')} outbound"
            body = (f"Hi {first},\n\nA follow-up on my note. Given that {hook}, teams in a similar spot usually start with "
                    f"one segment and a two-week pilot, so the effort stays small.\n\nWould a 20-minute call next week "
                    f"be useful?\n\n{sender.get('name', '')}")
        else:
            subject = f"{company.get('name')} and outbound research"
            body = (f"Hi {first},\n\nI saw that {hook}. "
                    f"We help sales teams research accounts and prepare personalised outreach that a person approves "
                    f"before anything is sent.\n\nWould a 20-minute call to compare notes be worthwhile?\n\n"
                    f"{sender.get('name', '')}, {sender.get('title', '')}")
        ref = finding["id"] if finding else company.get("id") or person.get("id")
        return {"channel": brief.get("channel") or "email", "subject": subject, "body": body,
                "personalisation": [{"point": hook, "evidence_ref": ref}], "claims": [],
                "call_to_action": "20-minute call"}

    def _reply(self, user: str, schema: dict) -> dict:
        d = _doc(user)
        from app.events.inbound import strip_quoted
        text = strip_quoted(d.get("reply") or "")
        low = text.lower()
        rules = [("unsubscribe", ("unsubscribe", "remove me", "stop emailing")),
                 ("not_interested", ("not interested", "no thanks", "no thank you", "not a fit")),
                 ("out_of_office", ("out of office", "on leave", "back on")),
                 ("not_now", ("next quarter", "not right now", "later this year", "circle back")),
                 ("meeting_request", ("book a call", "set up a call", "send times", "calendar", "meet next")),
                 ("positive_interest", ("interested", "sounds good", "tell me more", "let's talk", "keen"))]
        category = next((c for c, keys in rules if any(k in low for k in keys)), "question" if "?" in text else "other")
        first = next((ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 5), text.strip()[:120])
        return {"category": category, "summary": f"Scripted demo classification: {first[:140]}",
                "objection": None, "follow_up_after_days": 7 if category == "out_of_office" else None,
                "referral": None, "quotes": [first] if first and first in (d.get("reply") or "") else []}
