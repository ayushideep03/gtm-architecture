# Autonomous AI Sales / GTM System — Project Overview & Handover Guide

Welcome to the **Autonomous AI Sales / Go-To-Market (GTM) Architecture** repository.

This project is an enterprise-grade, event-driven backend system engineered to autonomously orchestrate B2B sales development representative (SDR) and GTM workflows. It handles everything from raw lead ingestion to automated enrichment, policy authorization, task execution, and event-driven feedback loops.

---

## 1. Executive Summary & Purpose

Modern AI agents often suffer from "god-agent" antipatterns—where a single LLM prompt attempts to query databases, write emails, make decisions, and trigger external APIs directly. This leads to unpredictable behaviors, hallucinations, duplicate outreach, and compliance failures.

This architecture solves those issues by enforcing **strict separation of concerns**:
- **Oxygen DECIDES**: Gathers context from CRM and event history; determines the next logical action.
- **Guardrails AUTHORIZE / BLOCK**: Evaluates proposed actions against deterministic business policies; fails closed.
- **Task REPRESENTS the handoff**: An approved action is persisted in PostgreSQL as a stateful `Task`.
- **Execution Layer EXECUTES**: Claims tasks with row-level database locking (`FOR UPDATE`), dispatches them to pluggable capability providers, and records results.
- **PostgreSQL STORES authoritative state**: Single source of truth for CRM entities and tasks.
- **Events REPORT what happened**: Append-only audit log enabling event-driven RevOps and continuous feedback loops.

---

## 2. Complete Architecture Diagram

```
                 Lead Sources (Apollo, Scout, Inbound Webhooks)
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

## 3. Subsystem Breakdown

### 3.1 Lead Ingestion & Normalization (`app/integrations/`, `app/services/lead_ingestion.py`)
- Ingests raw prospect payloads from various sources (Apollo, Scout, inbound CSV/forms).
- Normalizes emails, phone numbers, and domains into standard representations.
- Performs deduplication: matching company domains and contact emails to prevent duplicate entity creation.
- Emits `lead_ingested` events into the event log.

### 3.2 CRM Data Models (`app/models/base.py`)
- **Company**: Accounts, domain, employee count, industry, enriched attributes.
- **Person**: Contacts, email, job title, seniority, linked to a company.
- **Lead**: Central sales opportunity entity tied to a Person.
- **Task**: Persistent work unit with status state machine (`pending` → `in_progress` → `completed` / `failed`).
- **Interaction**: Logged touchpoints (emails sent, replies received, meetings held).
- **Event**: Append-only domain event stream.
- **EventProcessingState**: Durable consumer cursor table for RevOps stream processing.

### 3.3 Identity Resolution & Enrichment (`app/services/enrichment.py`)
- Resolves identity across domains and emails.
- Connects to enrichment providers (mock Apollo, mock Scout) to enrich company intelligence (technologies, funding, revenue) and contact attributes.
- Safe to run repeatedly (updates existing entities idempotently, never duplicates).

### 3.4 Oxygen Orchestration Layer (`app/oxygen/`)
- **OxygenContextBuilder**: Compiles an immutable snapshot of CRM entities, task history, and chronological events for a given lead.
- **DeterministicDecisionEngine**: Analyzes context to output structured `Decision` objects (`action`, `capability`, `parameters`, `reason`).
- **CapabilityRegistry**: Tracks available capabilities.
- **OxygenOrchestrator**: Coordinates context gathering, decision engine evaluation, guardrail checks, task creation, and `oxygen_decision` event emission.

### 3.5 Guardrails & Policy Enforcement (`app/guardrails/`)
- Sits directly between Oxygen decisions and Task creation.
- Evaluates 9 deterministic rules (`GR-001` through `GR-009`):
  1. *Unknown Capability*: Blocks if capability is not registered.
  2. *Missing Target*: Blocks if target UUID is missing.
  3. *Missing Payload*: Blocks if required parameters are absent.
  4. *Duplicate Active Task*: Blocks if a task of the same type is already pending or in progress.
  5. *Completed Task*: Blocks duplicate execution if already finished.
  6. *Unregistered Execution Provider*: Blocks if no provider can execute the task.
  7. *Unsupported Task Type*: Blocks invalid task actions.
  8. *Missing CRM Entity*: Blocks if target entity does not exist in DB.
  9. *Safe Valid Internal Execution*: Approves when all checks pass.
- Fails closed. If blocked, emits `guardrail_blocked` and creates NO task. If approved, emits `guardrail_allowed` and creates a `Task`.

### 3.6 Execution Layer (`app/execution/`)
- **TaskExecutor**: Handles row-level DB locking (`SELECT ... FOR UPDATE`) to prevent race conditions during concurrent worker runs.
- Enforces strict state transitions (`pending` → `in_progress` → `completed`/`failed`).
- Dispatches tasks to providers via `ExecutionRegistry`.
- Emits `task_started`, `task_completed`, or `task_failed` events with execution metadata.

### 3.7 GTM Capability Providers (`app/integrations/gtm/`)
- Implements replaceable adapters conforming to `ExecutionProvider`:
  - `MockProspectResearchProvider`: Generates structured prospect research.
  - `MockOutreachDraftingProvider`: Generates personalized cold email drafts.
  - `MockOutreachSendProvider`: Simulates email sends (`mode: "simulation"`).
  - `MockMeetingBookingProvider`: Simulates meeting scheduling (`mode: "simulation"`).
  - `CRMActionProvider`: Performs safe internal CRM mutations (updating lead score/status).

### 3.8 RevOps & Event Stream Processing (`app/revops/`)
- **EventProcessor**: Consumes the append-only `events` table using PostgreSQL-backed cursors (`event_processing_states`).
- Idempotent stream processing without mutating historical records.
- **Analytics**: Calculates live conversion metrics (ingestion count, enrichment rate, task success rate, simulated outreach).
- **Timeline**: Assembles unified chronological lead journeys.

### 3.9 Evaluation Framework (`app/evals/`)
- Runs 10 core invariant test cases (`EVAL-001` through `EVAL-010`) in isolation without mutating production data.
- Measures pass rate, execution latency, and behavioral compliance.

### 3.10 Autonomous Loop Service (`app/services/gtm_orchestration.py`)
- Provides `GtmOrchestrationService.execute_cycle()` to drive a complete deterministic round-trip from decision to execution and feedback observation.

---

## 4. How to Run Locally

### Prerequisites
- Python 3.11 or higher
- PostgreSQL running at `localhost:5432` with database `gtm_db`
- Redis running at `localhost:6379`

### 1. Environment Setup
Create a virtual environment and install dependencies:
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

Verify your `.env` configuration (see `.env.example`):
```env
ENVIRONMENT=development
LOG_LEVEL=INFO
DATABASE_URL=postgresql://gtm_user:gtm_password@localhost:5432/gtm_db
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=dev-secret-key-change-in-prod
```

### 2. Database Migrations
Run Alembic migrations to apply all schemas:
```bash
alembic upgrade head
alembic current
# Should output: 0007 (head)
```

### 3. Running the Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Interactive OpenAPI documentation will be accessible at:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

### 4. Running Tests
The suite contains 227 tests covering unit, integration, database isolation, API routes, and end-to-end flows:
```bash
# Run the complete test suite
python -m pytest -v

# Run targeted subsystem test suites
python -m pytest tests/test_guardrails.py -v
python -m pytest tests/test_gtm_capabilities.py -v
python -m pytest tests/test_revops.py -v
python -m pytest tests/test_evals.py -v
python -m pytest tests/test_end_to_end_gtm.py -v
```

---

## 5. Current Shortcomings & Constraints

While the architectural foundation is complete and production-grade, the following areas are intentionally constrained at this stage:

1. **Mocked Side Effects (Safety by Design)**:
   - Outreach sending (`MockOutreachSendProvider`) and meeting booking (`MockMeetingBookingProvider`) operate strictly in simulation mode. No live emails or calendar events are created.
   - Prospect research uses deterministic templates rather than live web scraping.
2. **Deterministic Rules vs. Commercial LLMs**:
   - The decision engine (`DeterministicDecisionEngine`) and outreach drafter use deterministic templates. They establish reliable baseline contracts before introducing probabilistic LLM outputs.
3. **Absence of a Continuous Background Worker Daemon**:
   - The orchestration service exposes step-by-step function calls and API triggers. There is currently no active background daemon (e.g., Celery beat, Temporal, ARQ worker) executing loops automatically every N seconds.
4. **Single-Tenant & No Route Authentication**:
   - The API currently does not enforce user authentication (JWT, API keys) or organization-level tenant partitioning.
5. **Headless / API-Only**:
   - There is no frontend user interface or dashboard; all interactions occur via REST endpoints or programmatic service calls.
6. **Autonomous Loop Ends at Qualification**:
   - Oxygen's decision rules currently cover enrich company → enrich person → qualify lead, then stop (`no_action`).
   - The research, outreach drafting, outreach send and meeting booking providers are implemented and tested individually, but Oxygen does not yet choose them, so a qualified lead does not progress to outreach automatically.
7. **Re-ingesting Existing Contacts Creates Duplicate Leads**:
   - Companies (by domain) and people (by email) are de-duplicated correctly, but every ingestion run creates a new `Lead` for the same person. Running the same import twice doubles the lead count.

---

## 6. Required Next Steps & Improvements

Here is the recommended roadmap to take this architecture to full commercial production:

### Priority 1: Close the Known Functional Gaps
- **Extend the decision engine** (`app/oxygen/decision.py`): after `qualify_lead` completes, decide `research_prospect` → `draft_outreach` → `send_outreach_simulation` → `book_meeting_simulation`, each gated by guardrails and the existing pending/completed-task checks.
- **De-duplicate leads on ingestion** (`app/services/lead_ingestion.py`): reuse the person's existing open lead instead of creating a new one.

### Priority 2: Connect Live External Providers (Provider Plug-ins)
Because the system is designed around the `ExecutionProvider` interface, you can swap mocks for real providers without altering CRM, Oxygen, Guardrails, or Tasks:
- **Email Sending**: Implement a `SendGridOutreachProvider` or `ResendOutreachProvider` conforming to `ExecutionProvider`.
- **Enrichment**: Implement an `ApolloLiveEnrichmentProvider` calling the official Apollo Search & Enrichment API.
- **Meeting Scheduling**: Implement a `CalendlyMeetingProvider` or `GoogleCalendarMeetingProvider` using OAuth2.

### Priority 3: Introduce Real LLM Decision & Drafting Agents
- Replace or augment `DeterministicDecisionEngine` with an `LLMDecisionEngine` utilizing structured outputs (e.g., OpenAI Function Calling / Pydantic models).
- Add prompt guardrails (input sanitization, prompt injection defenses, sentiment verification) inside the `app/guardrails/` package.

### Priority 4: Add Distributed Worker Execution (Celery / Temporal / ARQ)
- Set up a background worker daemon to poll `tasks` in `pending` status or consume events from Redis / PostgreSQL LISTEN-NOTIFY.
- Configure recurring cron tasks to run `GtmOrchestrationService.execute_cycle()` on new leads automatically.

### Priority 5: Authentication, Authorization & Multi-Tenancy
- Add FastAPI dependencies for API Key / JWT verification (`OAuth2PasswordBearer`).
- Introduce an `organizations` table and add `tenant_id` to `companies`, `people`, `leads`, and `events` for strict multi-tenant data isolation.

### Priority 6: Web UI / RevOps Dashboard
- Build a Next.js / React dashboard connecting to `/api/v1/revops/metrics`, `/api/v1/revops/events`, and `/api/v1/revops/leads/{lead_id}/timeline`.
- Allow human-in-the-loop review of drafted outreach before approving task execution.

---

## 7. Key File Index

- `app/main.py`: FastAPI application entry point, lifecycle events, and middleware.
- `app/models/base.py`: Authoritative SQLAlchemy models (`Company`, `Person`, `Lead`, `Task`, `Interaction`, `Event`, `EventProcessingState`).
- `app/oxygen/orchestrator.py`: Oxygen context assembly and decision coordination.
- `app/guardrails/rules.py` & `evaluator.py`: Policy enforcement rules and evaluation engine.
- `app/execution/executor.py`: Row-locked task lifecycle state machine.
- `app/integrations/gtm/`: Pluggable GTM capability providers.
- `app/revops/processor.py`: PostgreSQL cursor-based event processor.
- `app/evals/runner.py`: Deterministic test runner for behavioral invariants.
- `app/services/gtm_orchestration.py`: End-to-end autonomous GTM cycle runner.
- `tests/`: 227 automated test cases (222 pass, 5 skipped).
- `scripts/demo.py`: Live end-to-end demo against the running API (`python scripts/demo.py --reset`).
