# Oxygen Demo Framework — Architecture

Oxygen runs the complete 12-stage autonomous sales pipeline in **demo mode** with no API keys and no external calls. Demo mode is not a separate application or a mock-up. It is the production code path with **demo providers** plugged into the same interfaces the live integrations use. Switching to a live provider is a configuration change; the workflow engine stays the same.

```
             ┌──────────────────────────── React dashboard (frontend/) ─────────────────────────────┐
             │ Dashboard · Campaigns · Accounts · Contacts · Qualification · Research · AI Drafts ·  │
             │ Approvals · Follow-ups · Inbox · Meetings · RevOps · Integrations · Organisation ·    │
             │ Demo Control Centre                                    (live updates over SSE)        │
             └───────────────────────────────────────────┬──────────────────────────────────────────┘
                                                         │ /api/v1 (session cookie / API key)
┌───────────────────────────────────────── FastAPI (app/) ┴─────────────────────────────────────────────┐
│ auth: principal → organisation (tenant) + role → ROLE_POLICY → tenant context (row-level security)    │
│ pipeline routes: campaigns, accounts, discovery, contacts, qualification, research, drafts,           │
│ follow-ups, inbox, meetings, metrics, workflow · admin · integrations · demo control                  │
├───────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Services (app/pipeline): campaigns+discovery · ICP engine · drafts · response intelligence ·          │
│ follow-ups · meetings · metrics · integrations · demo clock · demo control · hooks                    │
├───────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Workflow engine (app/services/autonomy.py, app/oxygen, app/execution)                                  │
│ events → reactions → work items (leased, one per lead/kind) → Oxygen cycle (planner + guardrails)     │
│ → task (+ approval for external actions) → executor (provider selection, retries, failover,           │
│ idempotency, leases, compliance gates) → events …   follow-ups due → wake Oxygen                      │
├───────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Provider layer (app/providers): config-driven registry + selector (priority, health, rate limits)     │
│   find_leads: demo_apollo ↔ apollo          research_prospect: demo_research ↔ llm_research           │
│   qualify_lead: icp_rules                    draft_outreach / draft_reply: LLM router (demo ↔ OpenAI/Anthropic)│
│   send_email: mock_outreach_send ↔ smtp_email / billionmail    book_meeting: demo_google_calendar /    │
│   demo_calendly ↔ smtp_meetings (iCalendar)  receive_email: demo_inbox ↔ imap_inbox                   │
└───────────────────────────────────────────────────────────────────────────────────────────────────────┘
        PostgreSQL (all state, RLS per organisation)          Redis (pause flag, provider health, rate limits)
```

## Key design decisions

| Concern | How it is handled |
|---|---|
| Demo vs live | Profiles (`config/gtm.demo.yaml`) choose providers. The demo profile **disables every live provider**. The executor also refuses any provider not tagged `demo`/`mock` for external actions (`DEMO_MODE_BLOCKED`), so even configured credentials cannot be used. |
| AI | `LLMRouter` vendors: `openai`, `anthropic`, `demo`. The demo vendor (`ScriptedDemoClient`) answers every structured request deterministically, labelled `scripted-demo-playbook`. Its output goes through the same schema validation, guardrails and approvals as a real model's. |
| Tenancy | Every tenant-owned table has `tenant_id` and a PostgreSQL row-level-security policy (`FORCE`, migration 0012) keyed on the per-transaction `app.tenant_id`. The API sets it from the signed-in principal; the worker sets it per task and per lead cycle. New rows default to the current tenant. |
| Roles | `admin`, `operator`, `approver` (`app_users`). A central `ROLE_POLICY` (`app/core/auth.py`) decides every write. Approver: approve/reject only. Operator: run campaigns and workflows, but cannot approve. Admin: everything. The reviewer recorded on an approval is the signed-in user, never request text. |
| Authentication | Per-user API keys (stored as SHA-256), `OXYGEN_API_KEYS` for service keys, HMAC-signed HttpOnly session cookies. The demo profile adds a demo-user picker. Fails closed outside development/test. |
| Human approval | Every external action (email, meeting invite) creates an approval bound to a hash of the exact payload. It is re-checked against suppression, consent and frequency caps at send time. An AI decision is a proposal, not permission. |
| Idempotency and duplicates | Unique idempotency key per action (one send per draft); one open work item per lead and kind; follow-ups unique per (lead, step); the task claim is committed with a lease before any provider call (a crash is never re-sent); inbound events are deduplicated by provider event id. |
| Retries and recovery | Transient errors retry with backoff and fail over between providers. An ambiguous external result is flagged for human review, not retried. Stale leases are recovered (safe tasks resume; external ones go to review). Manual retry needs explicit acknowledgement. |
| Scheduling | The work queue (`work_items`) with due times and leases, follow-up rows with due times, and the demo clock, which shifts an organisation's timestamps to show delays live. |
| Observability | Every step writes an `events` row (workflow history, live feed, logs page). Decisions keep reasoning, evidence and guardrail results. |
| Live updates | Server-sent events (`/api/v1/stream`). The stream re-reads a sliding 15-second window and skips event ids it has already sent, so events from late-committing worker transactions are never dropped. Screens also refresh on reconnect and every 20 seconds as a safety net. |

## New data model (migration 0012)

`campaigns` (ICP, offer, value points, sequence, status), `campaign_accounts` (target accounts, priority, selection, discovery status), `follow_ups`, `meetings` (slots, invite preview, history), `drafts` (versioned, with explanation, strategy and evidence), `app_users` (role, hashed key), `demo_clock`; `leads.campaign_id`, `people.verification_status`.

## Where things live

| Path | Contents |
|---|---|
| `app/pipeline/` | campaigns, icp, drafts, responses, followups, meetings, metrics, integrations, demo_data, demo_clock, demo_control, hooks |
| `app/integrations/demo_providers.py` | demo Apollo, research, calendar (Google/Calendly flavours), inbox |
| `app/api/routes/pipeline.py`, `admin.py`, `demo.py`, `auth.py` | API |
| `app/core/auth.py`, `app/core/tenancy.py`, `app/core/database.py` | roles, sessions, tenant context, RLS hook |
| `config/gtm.demo.yaml` | demo profile |
| `frontend/src/views/` | one file per screen |
| `tests/test_demo_framework.py` | all scenarios end to end, isolation, permissions, controls |
