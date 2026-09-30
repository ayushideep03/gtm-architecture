# Autonomous AI Sales / GTM System

An enterprise-grade, event-driven **Autonomous AI Sales / Go-To-Market (GTM) Architecture** built with FastAPI, PostgreSQL, SQLAlchemy Async, Redis, and a deterministic capability orchestration loop.

> "The system is designed so specialized GTM capabilities are replaceable providers. Oxygen is the decision/orchestration layer, not the implementation of every GTM capability."

> "PostgreSQL CRM state and Task state are authoritative. Events provide the append-only feedback/audit stream."

---

## 1. Complete Architecture Diagram

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

## 4. Provider Replaceability Strategy

Every GTM capability implements the `ExecutionProvider` interface:
```python
class ExecutionProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @property
    @abstractmethod
    def supported_task_types(self) -> list[str]: ...

    @abstractmethod
    async def execute(self, context: ExecutionContext, session: Optional[AsyncSession] = None) -> ExecutionResult: ...
```

To replace any mock provider with a real external provider (e.g., Apollo, SendGrid, Calendly, Salesforce):
1. Implement the provider conforming to `ExecutionProvider`.
2. Register the provider instance in `ExecutionRegistry.register_provider(new_provider)`.
3. **Zero changes** are required to Oxygen, Guardrails, CRM models, TaskExecutor, RevOps, or Evals.

---

## 5. Current Mock-Only Safeguards & Limitations

To guarantee safety during autonomous operation:
- **No Real Outbound Email**: Outreach send capability is strictly simulated (`mode: "simulation"`). No SMTP, SendGrid, or Gmail connections exist.
- **No Real LinkedIn Messaging**: No browser automation or private APIs.
- **No Real Meeting Booking**: Calendar invites and meetings are recorded as simulated interactions without calling Google Calendar or Calendly.
- **No Real Prospecting / Scraping**: Prospect research uses deterministic mock responses labeled `mock-v1`.
- **No Real Purchasing or Financial APIs**: The system cannot perform financial transactions.
- **No Autonomous Infinite Loops**: Execution loops are bounded and triggerable on demand or by scheduled worker invocations.

---

## 6. How to Run Locally

### Prerequisites
- Python 3.11+
- PostgreSQL running at `localhost:5432` (`gtm_db`)
- Redis running at `localhost:6379`

### Environment Configuration
Create `.env` or use environment variables:
```bash
ENVIRONMENT=development
LOG_LEVEL=INFO
DATABASE_URL=postgresql://gtm_user:gtm_password@localhost:5432/gtm_db
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=dev-secret-key-change-in-prod
```

### Database Migrations
Apply all migrations to head:
```bash
alembic upgrade head
alembic current
```

### Running the Application
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive API documentation available at `http://localhost:8000/docs`.

---

## 7. How to Run Tests

Run the complete test suite (with full database isolation and automatic fixture cleanup):
```bash
python -m pytest -v
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
