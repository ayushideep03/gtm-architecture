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
# Should output: 0011 (head)
```

### 3. Running the Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Interactive OpenAPI documentation will be accessible at:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

### 4. Running Tests
The suite contains 427 tests (run against a separate `gtm_test` database) covering unit, integration, database isolation, API routes, and end-to-end flows:
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

## 5. Current State, Shortcomings & Constraints

**What runs end to end today** (`python start.py`): lead sources (CSV / inbound webhook / Apollo / Scout) →
identity resolution → enrichment → persistent sales memory (evidence-scored facts, findings, interactions,
decisions) → AI Sales Controller (OpenAI default, Anthropic fallback; structured proposals with reasoning,
evidence, confidence, risks) → guardrails + compliance → human approval for every external action → execution
with provider selection, retries, failover and recovery → SMTP email / calendar invitations → IMAP inbox
(replies, bounces, accept/decline) → reply classification and opt-out handling → follow-ups / meetings / stop →
outcome attribution, RevOps dashboard and learning. A background worker runs the loop; a React dashboard
(Overview, Lead Intelligence, Control Centre, Pipeline, Approval Centre, RevOps) shows it live.

**Not yet proven live (needs credentials):** OpenAI / Anthropic calls, Apollo, BillionMail, a real SMTP/IMAP
mailbox. Each is implemented and tested against scripted responses, mocks or a local SMTP server, and shows as
*unavailable* with the missing env var until configured. See README §5 for the per-integration table.

**Constraints:**
1. API keys protect the API and dashboard (`OXYGEN_API_KEYS`), but there are no roles and the API is not
   tenant-scoped: run one instance per organisation.
2. Quiet hours and recipient time zones are not enforced.
3. LinkedIn and phone are prepared by Oxygen and executed by a person (rep tasks); no automation by design.
4. AutoClip video capability, Google Calendar / Calendly APIs are not implemented.
5. The `demo` profile's scripted playbook is a demo aid, not an AI; it is labelled everywhere it appears.

---

## 6. Recommended Next Steps

1. **Go live with credentials, one at a time:** set `OPENAI_API_KEY`, run the four demo scenarios with real AI and
   review decisions and drafts; then a real mailbox (`SMTP_*`, `IMAP_*`) sending to internal test addresses only;
   then `APOLLO_API_KEY` with a small search.
2. **Roles and per-key tenants** on top of the API-key authentication (who may approve, which organisation a
   key belongs to).
3. **Quiet hours / time-zone aware sending** in the compliance layer.
4. **Evaluation on real data:** use the RevOps dashboard and attribution to compare prompts/angles once there are
   enough outcomes (the dashboard flags small samples).
5. **Scale-out:** the Postgres `SKIP LOCKED` queue is modular; move to Celery/Temporal only if volume requires it.

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
- `tests/`: 375 automated test cases (370 pass, 5 skipped).
- `scripts/demo.py`: Live end-to-end demo against the running API (`python scripts/demo.py --reset`).
