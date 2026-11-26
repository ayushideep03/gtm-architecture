# Autonomous AI Sales / GTM System

An enterprise-grade, event-driven **Autonomous AI Sales / Go-To-Market (GTM) Architecture** built with FastAPI, PostgreSQL, SQLAlchemy Async, Redis, and a deterministic capability orchestration loop.

> "The system is designed so specialized GTM capabilities are replaceable providers. Oxygen is the decision/orchestration layer, not the implementation of every GTM capability."

> "PostgreSQL CRM state and Task state are authoritative. Events provide the append-only feedback/audit stream."

## Quick start (one command, macOS / Linux / Windows)

Prerequisites: Python 3.11+, PostgreSQL and Redis running (see §6), Node.js 18+ (only to build the dashboard once).

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python start.py
```

`start.py` checks PostgreSQL and Redis, applies migrations, builds the dashboard if needed, starts the API **and**
the background worker, and opens **http://127.0.0.1:8000/**. Ctrl+C stops everything.

- **No LLM key set →** it runs **DEMO MODE** (`demo` profile): the complete 12-stage pipeline with demo
  providers behind the live interfaces (Apollo, OpenAI/Anthropic, SMTP, IMAP, Google Calendar, Calendly,
  research). No API keys are needed and **nothing leaves the machine**: the profile disables every live provider
  and the executor refuses non-demo providers for external actions. It loads two fictional organisations
  (*Northstar Revenue Cloud*, *Helix Clinical Systems*) with admin / operator / approver users, a campaign each
  and their target accounts. Sign in by picking a demo user.
- **`OPENAI_API_KEY` or `ANTHROPIC_API_KEY` set →** it runs the `development` profile with the real AI Sales
  Controller (mock providers only where no real credentials exist).
- Options: `--profile <name>`, `--port`, `--no-worker`, `--no-browser`, `--no-seed`, `--rebuild`.

| Guide | |
| :--- | :--- |
| [docs/DEMO_PRESENTATION_GUIDE.md](docs/DEMO_PRESENTATION_GUIDE.md) | How to present it (storyline, scenarios, reset) |
| [docs/12_STAGE_WORKFLOW.md](docs/12_STAGE_WORKFLOW.md) | What each pipeline stage does and records |
| [docs/DEMO_FRAMEWORK_ARCHITECTURE.md](docs/DEMO_FRAMEWORK_ARCHITECTURE.md) | Architecture, tenancy (row-level security), roles, safety |
| [docs/MOCK_INTEGRATION_GUIDE.md](docs/MOCK_INTEGRATION_GUIDE.md) | Every demo provider and how it is labelled |
| [docs/FUTURE_API_INTEGRATION.md](docs/FUTURE_API_INTEGRATION.md) | Switching to live integrations; go-live checklist |
| [docs/FINAL_DEMO_VALIDATION.md](docs/FINAL_DEMO_VALIDATION.md) | What was tested and the results |

### The dashboard (`frontend/`, React + TypeScript)

| Area | Screens |
| :--- | :--- |
| Home | **Dashboard** (management KPIs, conversion rates, funnel, trend, campaign performance) · **Demo Control Centre** (demo mode) |
| Pipeline | **Campaigns** · **Target Accounts** (stages 1–2) · **Contacts** (3) · **Qualification** (4) · **Research** (5) · **AI Drafts** (6) · **Approval Centre** (7) · **Follow-ups** (8) · **Inbox** (9–10) · **Meetings** (11) |
| Operations | **RevOps** (12) · **Pipeline Board** · **Lead Intelligence** (prospect drawer with the 12-stage **Workflow** tab) · **Workflow Engine** (live decisions and feed) |
| Settings | **Integrations** (status, requirements, setup, connection test) · **Organisation & Users** (roles, API keys) |

Development: `cd frontend && npm install && npm run dev` (Vite on :5173, proxies `/api` to :8000).

### Demo scenarios (Demo Control Centre)

Successful conversion · Prospect objection · Prospect asks a question · Question Oxygen cannot answer (escalated) ·
Prospect requests a meeting · Prospect does not reply (demo clock + follow-up) · Prospect unsubscribes. Each runs
through the real workflow engine and approval gates; approvals are only auto-approved for an admin/approver
presenter and are recorded under their name. **Reset demo environment** makes every scenario repeatable.

---

## 1. Complete Architecture Diagram

![Oxygen architecture](docs/gtm_architecture.png)

(Source: `docs/gtm_architecture.svg`.) The original layered view:

```
                 Lead Sources (Apollo, Scout, CSV, Inbound)
                                      ↓
                         Lead Ingestion & Normalization
                                      ↓
                       CompAI CRM (PostgreSQL Authoritative)
                                      ↓
                     Identity Resolution & Enrichment Layer
                                      ↓
                          Oxygen Orchestrator
                       (Context Gathering & Decision)
                                      ↓
                                  Decision
                                      ↓
                        Guardrails & Policy Engine
                          (Authorize / Block Action)
                                      ↓
                               Task Created
                      (Persistent Handoff Boundary)
                                      ↓
                           Execution Registry
                      (Provider Discovery & Selection)
                                      ↓
                       Specialized GTM Capability Providers
                 (Research, Outreach, Meetings, CRM Mutation)
                                      ↓
                            Task Execution Result
                                      ↓
                           Append-Only Event Stream
                      (task_completed, task_failed, etc.)
                                      ↓
                        RevOps & Evaluation Layer
                     (Cursor-based Stream & Evals Runner)
                                      ↓
                       CompAI CRM / Feedback Update
                                      ↓
                         Oxygen Orchestration Loop
                                      ↺
```

---

## 2. Component Responsibilities

| Subsystem | Responsibility | Persistent Boundary |
| :--- | :--- | :--- |
| **Ingestion Layer** | Normalizes external source payloads, prevents duplicate contacts, maps to CRM entities. | `leads`, `companies`, `people` |
| **CRM Layer** | Authoritative database storing companies, persons, leads, tasks, interactions, events. | PostgreSQL |
| **Identity & Enrichment** | Deterministic deduplication, company domain resolution, contact enrichment. | `companies`, `people` |
| **Oxygen Orchestrator** | Coordinates context gathering from CRM and events; evaluates decisions. Does NOT execute actions. | Emits `oxygen_decision` events |
| **Guardrails & Policy** | Formal authorization boundary evaluating decisions before Task creation. Fails closed. | Emits `guardrail_allowed` or `guardrail_blocked` |
| **Task Model** | Immutable/stateful handoff unit representing approved actions (`pending` -> `in_progress` -> `completed`/`failed`). | `tasks` table with row locking |
| **Execution Registry & Executor** | Dispatches tasks to registered capability providers with concurrency locks and idempotency. | Emits `task_started`, `task_completed`, `task_failed` |
| **GTM Providers** | Replaceable pluggable capability adapters (prospect research, outreach drafting/sim, meetings, CRM updates). | Standardized `ExecutionResult` |
| **Event Stream** | Strictly append-only audit trail and reactive trigger mechanism. Historical events are never mutated. | `events` table |
| **RevOps Layer** | Durable cursor-based event processor, real-time analytics aggregation, chronological lead timelines. | `event_processing_states` |
| **Evals Framework** | Isolated test-suite runner verifying system behavior against 10 core invariant test cases. | Deterministic, non-destructive |

---

## 3. Data Flow & Event Flow

### Data Flow
1. **Ingest Lead**: An external webhook or batch source pushes raw prospect information. Ingestion resolves Company and Person identities idempotently and assigns a Lead.
2. **Enrichment**: Enrichment providers enrich company intelligence (industry, tech stack, size) and contact attributes (title, department).
3. **Oxygen Context Gathering**: `OxygenContextBuilder` loads the lead, associated person, company, existing tasks, and chronological events into an immutable `OxygenContext`.
4. **Oxygen Decision**: `DeterministicDecisionEngine` analyzes the context and determines the next logical action (e.g., `qualify_lead`, `enrich_company`, `wait`, `no_action`).
5. **Policy Authorization**: `GuardrailEvaluator` passes the decision and context through 9 deterministic safety rules.
6. **Task Creation**: On approval, a persistent `Task` is created in PostgreSQL with status `pending`.
7. **Execution**: `TaskExecutor` locks the row (`with_for_update`), transitions status to `in_progress`, resolves the registered provider from `ExecutionRegistry`, executes, records the result, and sets status to `completed` or `failed`.
8. **Feedback Loop**: Events trigger the `EventProcessor`, advancing the durable cursor in `event_processing_states` and feeding back into subsequent Oxygen decision cycles.

### Event Flow
- Append-only events emitted throughout lifecycle:
  - `lead_ingested`
  - `company_enriched` / `person_enriched` / `enrichment_completed`
  - `oxygen_decision`
  - `guardrail_allowed` / `guardrail_blocked`
  - `task_started` / `task_completed` / `task_failed`
  - `prospect_researched` / `outreach_drafted` / `outreach_simulated` / `meeting_booking_simulated` / `crm_updated`

---

## 4. Capabilities, Providers & Configuration

The system separates **what** can be done from **who** does it:

- **Capabilities** (`app/capabilities/`) are vendor-neutral contracts: `find_leads`, `enrich_company`,
  `enrich_person`, `qualify_lead`, `research_prospect`, `draft_outreach`, `send_email`, `book_meeting`,
  `crm_update`. Each declares an input schema, side-effect class (`read_only`, `internal_write`,
  `external_communication`, `spend`), required data (e.g. `person.email`), repeatability policy and aliases.
  Guardrails and Oxygen read this metadata; no rule names a specific task type.
- **Providers** are concrete integrations. Several providers can serve one capability. All providers are
  loaded from configuration into one registry (`app/providers/`); nothing registers itself on import.
- **Provider selection** happens per call (`ProviderSelector`): providers are filtered by tenant policy,
  credentials, constraints (e.g. region), cost ceiling, failure cooldown and rate limits, then ranked by
  configured priority, observed reliability (from the event log), latency and cost. The executor fails
  over to the next provider on retryable errors (timeout, exception, rate limit) and records every attempt.

### Configuration files (`config/`)
| File | Purpose |
| :--- | :--- |
| `gtm.yaml` | Base, production-safe config: LLM profiles (OpenAI default), selection weights, approval policy, the built-in CRM provider. **No mock providers.** |
| `gtm.<profile>.yaml` | Profile overlay, chosen by `GTM_PROFILE` (falls back to `ENVIRONMENT`). `development` and `test` include `providers/mocks.yaml`. |
| `providers/mocks.yaml` | Mock/simulation providers, enabled only by explicit include. |
| `$GTM_CONFIG_FILE` | Optional deployment overlay (real vendors, keys, tenants). |

Providers merge by `name` across layers. Secrets are referenced by environment-variable **name** only:

```yaml
providers:
  - name: acme_enrichment
    factory: my_integrations.acme:AcmeEnrichment   # implements CompanyEnrichmentProvider
    capabilities: [enrich_company, enrich_person]
    priority: 70
    cost_per_call: 0.02
    credentials: {api_key: ACME_API_KEY}            # read from the environment
    resilience: {timeout_s: 10, max_retries: 2}
    rate_limit: {requests: 100, per_seconds: 60}
    constraints: {region: [EU, US]}
```

A provider with missing credentials, a broken factory or the wrong interface is registered as
*unavailable* (with the reason, visible at `GET /api/v1/execution/providers`) and is never selected.

Per-tenant overrides (`tenants: {<org-id>: {...}}`) can disable providers or capabilities, change
priorities or approval policy for one organisation. Every tenant-owned table has `tenant_id`.

To add an integration: implement the provider interface for its capability and add a config entry.
**No changes** to Oxygen, guardrails, the executor or the selector.

### Reliability, identity & recovery (Phase 2)
- **Identity:** one `IdentityResolver` (tenant-scoped, exact keys in configured order: source record → email →
  LinkedIn → name+company). Re-importing a person attaches to their open lead; the database enforces one open
  lead per person and unique domains / emails per tenant.
- **Shared provider state:** failure cooldowns and rate limits are kept in Redis, shared by all workers.
- **Retries:** transient failures retry on the same provider, fail over, then reschedule the task with
  backoff (`execution.retry`). External actions with an unknown outcome are **never** retried automatically;
  they are flagged `needs_review`.
- **Recovery:** `POST /api/v1/execution/tasks/{id}/retry` re-queues a failed task (same row, audited);
  `POST /api/v1/execution/recover-stale` handles tasks whose execution lease expired.

### AI Sales Controller (Phase B)
Oxygen's decision engine is an LLM planner (`oxygen.decision_engine: llm`). For each prospect it reads the
full sales memory and the campaign objective (`oxygen.campaign` in config), chooses one action from the
capabilities currently possible for that lead (or `wait` / `stop` / `escalate`), and returns a structured
proposal with reasoning, cited evidence, expected outcome, confidence, uncertainty, risks and alternatives.
Every proposal is validated, then passes the same guardrails, compliance checks and human approval as
before. Without an API key it falls back to the deterministic rules engine and labels the decision so.

Enable it by adding a key to `.env` (the model per role is set in `config/gtm.yaml` → `llm.profiles`):
```bash
OPENAI_API_KEY=sk-...          # default planner (gpt-4.1)
ANTHROPIC_API_KEY=sk-ant-...   # backup planner (claude-opus-5-5)
```
Check: `GET /api/v1/oxygen/engine`. Run one cycle for a lead: `POST /api/v1/oxygen/leads/{id}/cycle`.

With a key, the AI capabilities switch on automatically (they outrank the mocks):
- **Research** reads only the prospect company's own website; every finding must quote the page verbatim
  (checked in code) and website facts stay *proposed* until corroborated.
- **Intent analysis** and **qualification** (against `oxygen.campaign`, with a deterministic score floor)
  cite memory evidence for every signal and score.
- **Drafting** writes email / LinkedIn / call scripts that must pass the content policy in
  `oxygen.outreach` (banned phrases, length, placeholders, evidence-backed personalisation, opt-out footer).
- **Rep tasks** hand LinkedIn and phone actions to a person with the prepared message or script.

### Email, meetings & inbox (Phase G)
- `smtp_email` (`send_email`): any SMTP server. The recipient is always the prospect's stored address (a payload
  naming anyone else is refused), and the `Message-ID` is derived from the task id, so replies map back to the
  exact touch. SMTP has no server-side de-duplication, so an ambiguous failure goes to human review instead of a
  retry. `mode: dry_run` records the touch without connecting.
- `smtp_meetings` (`book_meeting`): sends an iCalendar invitation for the next slot in configured availability
  (`settings.availability`: timezone, weekdays, hours, minimum notice). Booked when the attendee accepts.
- `imap_inbox` (`receive_email`): the worker polls it every tick. Delivery-status reports become bounces
  (5.x.x = hard → suppression), iCalendar replies become meeting booked / cancelled, everything else becomes a reply
  matched by `In-Reply-To` / `References` / sender, then flows through opt-out rules, classification and reactions.
- `billionmail` (`send_email`, optional): HTTP adapter for a self-hosted BillionMail (AGPL — run as a separate,
  unmodified container).
- All four stay **unavailable** (with the missing env vars shown in the Control Centre) until configured.
- Compliance: the 48h minimum gap between touches is waived when the prospect has replied since our last touch
  (`compliance.waive_gap_after_reply`) — answering someone is not an unsolicited follow-up. Caps still apply.

### Event-driven autonomy & inbound events (Phases D/E)
- **Run the loop:** `python -m app.worker` (continuous) or `POST /api/v1/autonomy/tick` (one step, handy in demos).
  Events listed under `autonomy.reactions` (new lead, task done, reply, bounce, meeting…) wake Oxygen for that
  prospect. Pause everything with `POST /api/v1/autonomy/pause`; a daily per-prospect cycle cap applies.
- **Inbound webhooks** (`/api/v1/webhooks/email`, `/calendar`, `/inbound-lead`) take a vendor-neutral JSON format,
  signed with `X-Oxygen-Timestamp` + `X-Oxygen-Signature` = HMAC-SHA256(`OXYGEN_WEBHOOK_SECRET`, `"<ts>.<body>"`).
  Opt-out replies are detected deterministically and always suppress the contact.
- **Lead sources:** CSV (`POST /api/v1/leads/import` with `{"source": "...", "csv": "..."}`), website forms,
  Apollo (`APOLLO_API_KEY`; search + capped credit-consuming enrichment — not yet verified live), and a narrow
  Scout profile adapter (`providers[scout].settings.scout_path`).

### RevOps & learning (Phase F)
- `GET /api/v1/revops/dashboard?days=90` — funnel, conversion, reply / positive / meeting rates by channel, angle,
  engine, segment and research, qualification accuracy, provider reliability and cost, approvals, guardrail
  blocks, and evidence-backed recommendations (for humans; nothing changes automatically).
- Every outbound touch is attributed to its outcome (`GET /api/v1/revops/outcomes`), refreshed by the worker.
- Oxygen learns from outcomes in two bounded ways: the planner receives peer-segment insights as evidence
  (`GET /api/v1/revops/insights/leads/{id}`), and provider selection weighs each provider's positive-outcome rate.

### Sales memory & compliance (Phase A)
- **Prospect memory:** `GET /api/v1/memory/leads/{id}` returns the CRM record, every fact with its evidence
  (source, time, confidence band, lifecycle status), findings, interactions, Oxygen's decision history,
  tasks, approvals, scheduled rechecks and live compliance status.
- **Evidence ledger:** ingestion, enrichment and humans write values as facts; confidence comes from
  configured evidence weights (`memory:` in config). Weak or conflicting claims are *proposed* for review.
- **Approval:** every external communication waits in `GET /api/v1/approvals` until a person approves
  (optionally editing it) or rejects it. The approved content is hash-locked.
- **Compliance:** suppression lists, lawful basis and contact-frequency caps (`compliance:` in config) are
  enforced by guardrails and again by the executor immediately before sending.

---

## 5. Integration status & limitations (honest)

| Integration | Status | Needs |
| :--- | :--- | :--- |
| PostgreSQL CRM memory, identity resolution, evidence ledger | **Working, tested** | — |
| Compliance (suppression, lawful basis, frequency caps, approvals) | **Working, tested** | — |
| Worker, retries, leases, recovery, Redis health/rate limits | **Working, tested** | — |
| SMTP email sending (`smtp_email`) | **Working end to end against a local SMTP server** (live run: API + 2 workers, approval → send → reply match → invite); login tested against a server requiring AUTH; not yet run against a production mail provider | `SMTP_HOST`, `SMTP_FROM_ADDRESS`; `SMTP_USERNAME` + `SMTP_PASSWORD` unless the relay accepts unauthenticated mail |
| IMAP inbox (replies, bounces, calendar accept/decline) | **Parser and pipeline tested with real RFC 822 / DSN / iMIP messages**; the IMAP network fetch itself is not yet run against a live mailbox | `IMAP_HOST`, `IMAP_USERNAME`, `IMAP_PASSWORD` |
| Meeting invites over email (iCalendar `METHOD:REQUEST`, `smtp_meetings`) | **Tested against a local SMTP server**; acceptance arrives via the IMAP poller | same SMTP settings |
| OpenAI / Anthropic (AI Sales Controller, research, intent, qualification, drafting, reply classification) | **Adapters tested through the real installed SDKs with a fake HTTP transport** (request shape, parsing, refusals, truncation; every schema checked against OpenAI strict mode); controller logic tested with scripted responses; **not yet run with a live key** (model names and server-side schema acceptance unverified) | `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` |
| Apollo lead search | Built from Apollo's API reference; **mock-tested only** | `APOLLO_API_KEY` |
| BillionMail sender | Built from BillionMail's source; **mock-tested only** (template must render `{{subject}}`/`{{body}}`) | `BILLIONMAIL_URL`, `BILLIONMAIL_API_KEY`, `BILLIONMAIL_ADDRESSER` |
| Scout profile source | Tested against a fake checkout only | local Scout checkout path |
| AutoClip video clips | **Not implemented** (lowest value per effort; needs a separate service and rights controls) | — |
| LinkedIn / phone | Not automated by design: Oxygen prepares the message/script and creates a **rep task** for a person | — |
| Google Calendar / Calendly APIs | Not implemented (standards-based email invitations are used instead) | — |

Authentication: every `/api/v1` route except health, signed webhooks and sign-in requires an API key
(`OXYGEN_API_KEYS="name=key,..."`, header `X-API-Key` or the dashboard's HttpOnly sign-in cookie). Outside the
development/demo/test profiles the API refuses requests (503) until keys are set. The key's name is recorded
as the reviewer on approvals. There are no roles yet: every key can approve.

Further limitations: the API is **not tenant-scoped** (tenancy exists in the data model, but a key sees all
tenants: deploy one instance per organisation until per-key tenants exist); quiet hours / recipient time zones
are not enforced; lead discovery runs on request (API, CSV, website form), not on a schedule; replies that ask a
question are escalated to a human rather than answered by Oxygen; the scripted demo playbook is a demo aid, not
an AI. Full findings: `docs/E2E_VALIDATION_REPORT.md`.

Safety that holds in every profile: an AI decision is a proposal, not permission. Every external communication
needs human approval (hash-locked content, re-checked against suppression/consent/frequency at send time), mock
providers are never production defaults, and nothing changes code, permissions, guardrails or compliance policy
automatically.

---

## 6. How to Run Locally

The project runs the same on macOS, Windows and Linux. Only the commands for
installing services and activating the virtualenv differ.

### Prerequisites
- Python 3.11+ (3.12 recommended)
- PostgreSQL running at `localhost:5432` (`gtm_db`, user `gtm_user` / `gtm_password`)
- Redis running at `localhost:6379`

### Option A — Docker (easiest, identical on Mac & Windows)
Install Docker Desktop, then:
```bash
docker compose up -d postgres redis   # just the databases
# or: docker compose up --build       # databases + API on :8000
```

### Option B — Native services
**macOS (Homebrew)**
```bash
brew install postgresql@16 redis
brew services start postgresql@16
brew services start redis
psql postgres -c "CREATE USER gtm_user WITH PASSWORD 'gtm_password';"
createdb -O gtm_user gtm_db
```

**Windows**
Install PostgreSQL 16 from postgresql.org and Redis via Memurai or WSL
(or use Docker, Option A), then in a terminal:
```powershell
psql -U postgres -c "CREATE USER gtm_user WITH PASSWORD 'gtm_password';"
psql -U postgres -c "CREATE DATABASE gtm_db OWNER gtm_user;"
```

### Python environment
**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

**Windows (PowerShell)**
```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

> Line endings are normalised to LF by `.gitattributes`, so files edited on
> either OS commit cleanly.

### Environment Configuration
Create `.env` or use environment variables:
```bash
ENVIRONMENT=development
LOG_LEVEL=INFO
DATABASE_URL=postgresql://gtm_user:gtm_password@localhost:5432/gtm_db
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=dev-secret-key-change-in-prod
# API/dashboard sign-in (required outside development/demo/test): name=key pairs, keys >= 16 chars
OXYGEN_API_KEYS=alex=<python -c "import secrets; print(secrets.token_urlsafe(32))">
# Optional: which config/gtm.<profile>.yaml overlay to use (defaults to ENVIRONMENT)
GTM_PROFILE=development
```

### Database Migrations
Apply all migrations to head:
```bash
alembic upgrade head
alembic current
```

### Running the Application
Everything at once (recommended): `python start.py` — see *Quick start*. Or run the parts yourself:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload   # API (+ dashboard if frontend/dist exists)
python -m app.worker                                        # background worker (autonomy loop, inbox polling)
```

Interactive API documentation available at `http://localhost:8000/docs`.

### Demo
The full demo runs from the dashboard (see *Demo scenarios* above). The older CLI walkthrough still works —
with the API running, in a second terminal:
```bash
python scripts/demo.py --reset
```
This walks one lead through ingest → Oxygen decision → guardrails → task →
execution (enrich company, enrich person, qualify), then shows the safety
checks, the lead's event timeline, RevOps metrics and the eval suite. It pauses
between steps; add `--no-pause` to run straight through.

`--reset` wipes and re-creates the **local** database so every run starts clean.
Leave it off to keep existing data.

---

## 7. How to Run Tests

Run the complete test suite (442 tests). Tests use a separate `gtm_test` database (created and migrated
automatically) and their own Redis key prefix, so they never touch your development data:
```bash
python -m pytest -v
```

Live end-to-end validation (real API + two workers + a local SMTP sink, isolated `gtm_e2e` database,
37 stage checks; no mail leaves the machine):
```bash
python scripts/e2e/run_e2e.py
```

Run targeted subsystem suites:
```bash
python -m pytest tests/test_guardrails.py -v
python -m pytest tests/test_gtm_capabilities.py -v
python -m pytest tests/test_revops.py -v
python -m pytest tests/test_evals.py -v
python -m pytest tests/test_end_to_end_gtm.py -v
python -m pytest tests/test_oxygen.py -q
python -m pytest tests/test_execution.py -q
```

---

## 8. API Overview

### Health Probes
- `GET /health` — Application liveness
- `GET /health/db` — PostgreSQL connectivity check
- `GET /health/redis` — Redis connection pool check

### Lead Ingestion & Normalization
- `POST /api/v1/leads/ingest` — Ingest lead payload with deduplication
- `GET /api/v1/leads/providers` — List registered lead source providers

### Enrichment Layer
- `POST /api/v1/enrichment/company/{company_id}` — Enrich company
- `POST /api/v1/enrichment/person/{person_id}` — Enrich person
- `POST /api/v1/enrichment/lead/{lead_id}` — Enrich lead
- `GET /api/v1/enrichment/providers` — List enrichment providers

### Oxygen Orchestration
- `POST /api/v1/oxygen/leads/{lead_id}/decide` — Pure read-only decision evaluation
- `POST /api/v1/oxygen/leads/{lead_id}/orchestrate` — Full decision -> guardrails -> task pipeline
- `GET /api/v1/oxygen/capabilities` — List registered Oxygen capabilities

### Execution Layer
- `POST /api/v1/execution/tasks/{task_id}/execute` — Row-locked task execution
- `GET /api/v1/execution/tasks/{task_id}` — Inspect task status and execution result
- `GET /api/v1/execution/providers` — List registered execution providers

### Guardrails & Policy
- `GET /api/v1/guardrails/rules` — List all 9 policy enforcement rules
- `POST /api/v1/guardrails/evaluate` — Diagnostic policy evaluation endpoint

### RevOps & Analytics
- `GET /api/v1/revops/metrics` — Aggregate GTM operational metrics
- `GET /api/v1/revops/events` — Query append-only event stream (filterable)
- `GET /api/v1/revops/leads/{lead_id}/timeline` — Chronological lead event/interaction timeline

### Evaluation Framework
- `GET /api/v1/evals/cases` — List 10 core evaluation invariant test cases
- `POST /api/v1/evals/run` — Run isolated evaluation suite and generate report
- `GET /api/v1/evals/runs/{run_id}` — Retrieve evaluation run report by UUID
