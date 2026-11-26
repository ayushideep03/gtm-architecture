"""
Integration settings: one entry per external integration, showing which provider is in
use (demo or live), whether the live one is configured, what it needs and how to set it up.

Statuses (never claims a live integration that is not configured):
  Demo Active          demo provider in use (demo mode, or live not configured but a demo exists)
  Live Ready           live provider configured and in use (outside demo mode)
  Live Not Configured  a live adapter exists but its credentials are missing
  Integration Pending  no live adapter is built yet (the demo provider defines the interface)
  Error                the provider failed to construct

Connection tests: a demo provider runs a simulated call and says so; a live provider is
NEVER called from demo mode, and outside demo mode the test only checks configuration
(no message is sent, no paid API is called).
"""

from __future__ import annotations

import os
from typing import Any, Optional

CATALOGUE: list[dict[str, Any]] = [
    {"key": "apollo", "name": "Apollo", "type": "Lead discovery", "capability": "find_leads",
     "demo_provider": "demo_apollo", "live_provider": "apollo", "env": ["APOLLO_API_KEY"],
     "setup": ["Create an Apollo API key (Settings → Integrations → API).",
               "Set APOLLO_API_KEY in the server environment and restart.",
               "Use the development or production profile (demo mode never calls Apollo)."]},
    {"key": "openai", "name": "OpenAI", "type": "AI (LLM)", "llm_vendor": "openai",
     "demo_provider": "scripted-demo-playbook", "env": ["OPENAI_API_KEY"],
     "setup": ["Create an API key at platform.openai.com.", "Set OPENAI_API_KEY and restart.",
               "Models per role are configured in config/gtm.yaml (llm.profiles)."]},
    {"key": "anthropic", "name": "Anthropic", "type": "AI (LLM)", "llm_vendor": "anthropic",
     "demo_provider": "scripted-demo-playbook", "env": ["ANTHROPIC_API_KEY"],
     "setup": ["Create an API key in the Anthropic Console.", "Set ANTHROPIC_API_KEY and restart.",
               "Used as the fallback planner by default (config/gtm.yaml llm.profiles)."]},
    {"key": "smtp", "name": "Email sending (SMTP)", "type": "Email", "capability": "send_email",
     "demo_provider": "mock_outreach_send", "live_provider": "smtp_email",
     "env": ["SMTP_HOST", "SMTP_FROM_ADDRESS"], "optional_env": ["SMTP_USERNAME", "SMTP_PASSWORD"],
     "setup": ["Use your mail provider's SMTP relay (Google Workspace, Microsoft 365, SES, Postmark...).",
               "Set SMTP_HOST and SMTP_FROM_ADDRESS (and SMTP_USERNAME/SMTP_PASSWORD if the relay needs login).",
               "Configure SPF, DKIM and DMARC for the sending domain before any real send."]},
    {"key": "imap", "name": "Inbox (IMAP)", "type": "Email replies", "capability": "receive_email",
     "demo_provider": "demo_inbox", "live_provider": "imap_inbox",
     "env": ["IMAP_HOST", "IMAP_USERNAME", "IMAP_PASSWORD"],
     "setup": ["Use the mailbox that receives replies to the sending address.",
               "Set IMAP_HOST, IMAP_USERNAME, IMAP_PASSWORD (an app password where supported)."]},
    {"key": "google_calendar", "name": "Google Calendar", "type": "Calendar", "capability": "book_meeting",
     "demo_provider": "demo_google_calendar", "live_provider": None, "env": [],
     "setup": ["Live adapter not built yet: implement book_meeting against the Google Calendar API "
               "(see docs/FUTURE_API_INTEGRATION.md).",
               "Until then, real invitations go out as standard iCalendar email via the SMTP integration."]},
    {"key": "calendly", "name": "Calendly", "type": "Scheduling", "capability": "book_meeting",
     "demo_provider": "demo_calendly", "live_provider": None, "env": [],
     "setup": ["Live adapter not built yet: implement book_meeting using a Calendly scheduling link "
               "(see docs/FUTURE_API_INTEGRATION.md)."]},
    {"key": "research", "name": "Website research", "type": "Research", "capability": "research_prospect",
     "demo_provider": "demo_research", "live_provider": "llm_research", "env": [], "needs_llm": True,
     "setup": ["Live research reads only the prospect company's own website and needs an LLM key.",
               "It is SSRF-guarded; route its egress through a proxy in production."]},
    {"key": "billionmail", "name": "BillionMail", "type": "Email", "capability": "send_email",
     "demo_provider": None, "live_provider": "billionmail",
     "env": ["BILLIONMAIL_URL", "BILLIONMAIL_API_KEY", "BILLIONMAIL_ADDRESSER"],
     "setup": ["Optional self-hosted sender. Set the three variables and restart."]},
]


def _find(name: Optional[str], capability: Optional[str] = None):
    if not name:
        return None
    from app.providers import get_provider_registry
    return get_provider_registry().record(name)


def status_of(entry: dict) -> dict:
    from app.core.gtm_config import get_gtm_config
    cfg = get_gtm_config()
    demo_mode = cfg.profile == "demo"
    missing = [e for e in entry.get("env", []) if not os.environ.get(e)]
    if entry.get("needs_llm") and not _any_llm_key():
        missing.append("OPENAI_API_KEY or ANTHROPIC_API_KEY")
    cap = entry.get("capability")
    demo_rec = _find(entry.get("demo_provider"), cap) if cap else None
    live_rec = _find(entry.get("live_provider"), cap) if cap else None
    if entry.get("llm_vendor"):
        prof_vendors = {p.provider for p in cfg.llm.profiles.values()}
        live_configured = not missing
        demo_active = demo_mode and "demo" in prof_vendors
        live_in_use = (not demo_mode) and live_configured and entry["llm_vendor"] in prof_vendors
        error = None
    else:
        live_configured = bool(entry.get("live_provider")) and not missing and (
            live_rec is not None and live_rec.available) and not (entry.get("needs_llm") and not _any_llm_key())
        demo_active = bool(demo_rec and demo_rec.available and demo_rec.config.enabled) and (
            demo_mode or not live_configured)
        live_in_use = live_configured and not demo_mode and bool(live_rec and live_rec.config.enabled)
        reason = getattr(live_rec, "unavailable_reason", None) if live_rec else None
        error = reason if reason and reason.startswith("construction failed") else None
    if error:
        status = "Error"
    elif live_in_use:
        status = "Live Ready"
    elif demo_active:
        status = "Demo Active"
    elif not entry.get("live_provider") and not entry.get("llm_vendor"):
        status = "Integration Pending"
    else:
        status = "Live Not Configured"
    note = None
    if demo_mode and live_configured:
        note = "Live credentials are present but live calls are disabled in demo mode."
    elif not entry.get("live_provider") and not entry.get("llm_vendor"):
        note = "No live adapter yet; the demo provider defines the interface it must implement."
    return {"key": entry["key"], "name": entry["name"], "type": entry["type"], "status": status,
            "mode": "Demo" if (demo_active and not live_in_use) else "Live" if live_in_use else "—",
            "demo_provider": entry.get("demo_provider"), "live_provider": entry.get("live_provider"),
            "live_configured": live_configured, "requirements": entry.get("env", []),
            "optional": entry.get("optional_env", []), "missing": missing, "setup": entry["setup"], "note": note,
            "error": error}


def _any_llm_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


def overview() -> list[dict]:
    return [status_of(e) for e in CATALOGUE]


async def test_connection(key: str) -> dict:
    from app.core.gtm_config import get_gtm_config
    entry = next((e for e in CATALOGUE if e["key"] == key), None)
    if entry is None:
        raise ValueError("unknown integration")
    st = status_of(entry)
    demo_mode = get_gtm_config().profile == "demo"
    if st["mode"] == "Demo" or demo_mode:
        detail = await _simulated_call(entry)
        return {"key": key, "ok": detail is not None, "simulated": True,
                "message": "Simulated call to the demo provider succeeded (no external service was contacted)."
                if detail is not None else "Demo provider unavailable.", "detail": detail}
    if st["status"] == "Live Ready":
        return {"key": key, "ok": True, "simulated": False,
                "message": "Configuration complete. Live connectivity is verified on first real use; this test "
                           "does not send email or call paid APIs."}
    return {"key": key, "ok": False, "simulated": False,
            "message": f"{st['status']}: missing {', '.join(st['missing']) or 'a live adapter'}."}


async def _simulated_call(entry: dict) -> Optional[dict]:
    key = entry["key"]
    if key == "apollo":
        from app.integrations.demo_providers import DemoApolloProvider
        from app.integrations.lead_source_record import LeadSearchCriteria
        recs = await DemoApolloProvider().search_leads(LeadSearchCriteria(company_domain="northwind-analytics.example"))
        return {"contacts_returned": len(recs), "sample": recs[0].job_title if recs else None}
    if key in ("openai", "anthropic"):
        from app.llm.demo_client import MODEL, ScriptedDemoClient
        data, _, model = await ScriptedDemoClient().complete_json(
            model=MODEL, system="", user='{"subject": "Re", "reply": "Tell me more"}', schema={},
            schema_name="reply_classification")
        return {"model": model, "classified_as": data["category"]}
    if key in ("google_calendar", "calendly"):
        from app.integrations.demo_providers import demo_slots
        return {"next_free_slots": [s.isoformat() for s in demo_slots()]}
    if key == "smtp":
        return {"mode": "dry_run", "would_send": False}
    if key == "imap":
        from app.integrations.demo_providers import DemoInboxProvider
        return await DemoInboxProvider().poll(None)
    if key == "research":
        from app.pipeline.demo_data import COMPANIES, signals_for
        return {"signals_for_sample_company": len(signals_for(COMPANIES[0], "demo"))}
    return None
