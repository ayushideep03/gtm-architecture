# Adaptive Oxygen — Codebase Audit & Target Architecture

**Scope:** every module under `app/` (≈8,200 lines), plus models, migrations, evals and tests.
**Goal:** move Oxygen from a fixed rule chain to an adaptive, AI-driven GTM agent, while keeping
safety, compliance, identity and state management deterministic.
**Status:** audit and proposal only. No code has been changed.

---

## 1. Executive summary

The codebase has good foundations: clean interfaces (`DecisionEngine`, `ExecutionProvider`,
`LeadSourceProvider`, enrichment providers), a fail-closed guardrail chain, row-locked task
execution and an append-only event log. These should be **kept and extended**, not rewritten.

What makes it rigid today:

| # | Problem | Where | Effect |
|---|---------|-------|--------|
| 1 | **Oxygen is a 5-step if/else chain** | `app/oxygen/decision.py:33-130` | Every lead follows enrich company → enrich person → qualify → stop. No research, outreach or meetings. |
| 2 | **The GTM capabilities are invisible to Oxygen** | `app/oxygen/registry.py:33-67` | Only 4 capabilities are registered. Research/outreach/meeting providers exist, but any decision to use them would be **blocked by GR-001**. |
| 3 | **Four separate provider registries, all hard-wired to mocks at import** | `integrations/registry.py:60-64`, `integrations/enrichment_registry.py:91-97`, `execution/workers.py:309-318`, `integrations/gtm/__init__.py:12-21` | Adding a provider means editing code. No config, no enable/disable, no fallback. |
| 4 | **One provider per task type, last registration wins** | `execution/registry.py:21-22` | Cannot run two email or enrichment vendors, choose between them or fail over. |
| 5 | **`"mock_enrichment"` hard-coded in 9 places** | `execution/workers.py:188,282`; `services/enrichment.py:24,87,270`; `api/schemas/enrichment.py:15,22,29`; `api/routes/enrichment.py:62,101` | Registering a real enrichment provider changes nothing at runtime. |
| 6 | **No retries, timeouts or rate limits anywhere** | `execution/executor.py:85-88` (failed is terminal); `ExecutionContext.attempt` unused; Redis only used for `/health/redis` | One network error permanently ends that step for the lead. |
| 7 | **Outreach and research are fixed templates** | `integrations/gtm/outreach.py:48-54`, `research.py:51-64` | Same email and the same "buying signals" for every lead. |
| 8 | **Task outputs are never chained** | `outreach.py:95-96` (send defaults to `"Automated Outreach"`) | A drafted email is not what gets sent; research never feeds drafting. |
| 9 | **Duplicate leads on every import; identity logic duplicated** | `services/lead_ingestion.py:217-223` always creates a Lead; `IdentityResolver` is **only used by tests** | Repeated imports duplicate leads. People without email and companies without domain are always duplicated. No DB unique constraints. |
| 10 | **Nothing runs by itself** | `app/workers/`, `app/agents/` are empty placeholders | Every step needs an API call. |
| 11 | **"Feedback" does nothing** | `revops/processor.py:104-126` returns strings like `"CRMFeedbackApplied"` | No outcome tracking, so there is nothing to learn from. |
| 12 | **Guardrails hard-code task types inside rules, and lack compliance rules** | `guardrails/rules.py:79-89,139,173-183,225-229` | Every new capability needs guardrail code edits. No suppression list, contact caps, consent or approval gates, which are mandatory before any real send. |

---

## 2. Full inventory of hard-coded dependencies and fixed decisions

### 2.1 Oxygen (decision layer)

| Location | What is fixed | Target |
|---|---|---|
| `oxygen/decision.py:14-130` | Entire decision policy: 5 ordered rules, fixed sequence, `confidence=1.0` everywhere | `LLMDecisionEngine` chooses the next action from context, objective and available capabilities. The current engine becomes the **fallback/safety-net** engine. |
| `oxygen/decision.py:34` | `context.events[:5]`: arbitrary evidence window | Context budget from config; the engine cites the evidence it actually used. |
| `oxygen/decision.py:111-119` | Qualification completed → `no_action` forever | The agent decides the next step (research, outreach, nurture, wait-for-reply, stop) based on the objective. |
| `oxygen/interfaces.py:21-22` | `decide()` is **synchronous** | Make async (needed for LLM calls). Small, contained change. |
| `oxygen/orchestrator.py:41` | Defaults to `DeterministicDecisionEngine()` | Engine chosen by config (`oxygen.decision_engine`), with fallback chain. |
| `oxygen/orchestrator.py:183-208` | Duplicate check uses `task_type == decision.action` only | Idempotency key per capability (e.g. `send_email:{lead}:{draft_id}`), from capability metadata. |
| `oxygen/orchestrator.py:125` | Only `wait` / `no_action` as non-actions; `wait` has no "until" | Add `wait_until(time | event)`, `stop(reason)`, `escalate_to_human`. |
| `oxygen/registry.py:33-67` | 4 capabilities hand-registered; GTM capabilities missing; capability names ≠ task type names (`lead_qualification` vs `qualify_lead`) | One capability catalogue, generated from provider declarations, with schemas and side-effect classes (see §4.1). |
| `oxygen/context.py:85-101` | "Enriched" = `enriched_at` set or specific event types present; enriched once = enriched forever | Freshness policy from config; context includes field-level data, research, task results, interactions and outcomes. |
| `oxygen/context.py` (whole) | Context has no research results, task outputs, campaign objective or history summary | Extend context builder (see §4.3). |

### 2.2 Guardrails

| Location | What is fixed | Target |
|---|---|---|
| `guardrails/rules.py:79-89` (GR-003) | Required payload fields hard-coded per task type | Validate parameters against the capability's **input JSON schema**. Same rule for every capability. |
| `guardrails/rules.py:139` (GR-005) | "Already completed" only checked for `qualify_lead` | Capability declares repeatability (`once_per_lead`, `once_per_target`, `repeatable(max, cooldown)`). |
| `guardrails/rules.py:173-183` (GR-007) | `KNOWN_TASK_TYPES` set literal | Derived from the capability catalogue plus an allow-list in config. |
| `guardrails/rules.py:225-229` (GR-008) | Entity requirements hard-coded per task type | Capability declares `requires: [company, person, email, draft]`. |
| `guardrails/rules.py:26,51,74…` | `("wait","no_action")` string literals repeated in every rule | Single `Decision.is_actionable` property. |
| `guardrails/registry.py:111-123` | Rule set fixed at import | Rules stay **code** (deterministic), but enable/severity/thresholds come from config. |
| *(missing)* | No suppression/unsubscribe, contact-frequency caps, send quotas, quiet hours, consent/region rules, human approval, content checks, cost caps, loop limits | **New deterministic rules**, see §5. Required before any real outbound action. |

### 2.3 Execution layer

| Location | What is fixed | Target |
|---|---|---|
| `execution/registry.py:17-22` | `task_type → single provider`; later registration silently overwrites | `capability → [providers]`, with selection policy (see §4.2). |
| `execution/executor.py:85-88` | `failed` is terminal; "explicit retry" does not exist | Retry state machine: `failed_retryable → pending (next_attempt_at)` up to `max_attempts`, then `failed` → event that wakes Oxygen to adapt. |
| `execution/executor.py:176-191` | Provider call has no timeout | Timeout per capability/provider from config. |
| `execution/models.py:83` | `attempt: int = 1` never incremented | Persist `attempt_count` on Task. |
| `execution/workers.py:24-116` | `MockLeadQualificationProvider`: qualified if domain **or** email exists; scores `85/75/25` literals | Qualification becomes an AI capability scored against a **configurable ICP** (per campaign). Mock stays for tests only. |
| `execution/workers.py:188,282` | `provider_name="mock_enrichment"` | Provider selected by `ProviderSelector`. |
| `execution/workers.py:318`, `integrations/gtm/__init__.py:21`, `execution/__init__.py:49` | Providers self-register **on import**; mocks always active | Explicit bootstrap from config at app/worker start-up; mocks only when `providers.<x>.enabled` in a test/dev profile. |
| All mock providers | `force_fail` test hooks inside production provider code | Move to test doubles. |
| `models/base.py:339,348` | `Task.priority` and `Task.due_at` exist but are never used by execution | Use for scheduling (priority queue, `wait_until`). |

### 2.4 GTM capability providers

| Location | What is fixed | Target |
|---|---|---|
| `gtm/research.py:51-64` | Canned findings ("High" fit, fixed buying signals and pain points) for every lead | Research capability: real sources (web/news/company data providers) + LLM synthesis into a structured, cited schema. |
| `gtm/outreach.py:48-54` | One subject/body template; signature "Sales Engineering"; product pitch hard-coded | LLM drafting from lead context, research and **campaign messaging config** (value props, tone, persona, CTA). Versioned prompt templates in config/DB. |
| `gtm/outreach.py:94-96` | Send takes subject/body from payload or falls back to defaults | Send references an **approved draft ID**; guardrail verifies the content being sent equals the approved draft. |
| `gtm/outreach.py:114` | `sim-msg-…` ID; simulation is the provider itself | Simulation becomes a **mode** (`dry_run`) of any provider, set in config. |
| `gtm/meetings.py:38-41` | Topic literal; meeting always "now + 3 days" | Calendar provider proposes real availability; topic from context. |
| `gtm/meetings.py:47` | Sets `lead.status = "meeting_booked"` directly inside the provider | Providers return results; state transitions happen centrally (validated state machine). |
| `gtm/crm.py:66-149` | `crm_action` if/elif chain of 3 actions | CRM is a provider type (internal DB, HubSpot, Salesforce…) behind one interface. Actions are capabilities with schemas. |
| Task type names | `send_outreach_simulation`, `book_meeting_simulation` bake "simulation" into the capability name | Capability names are vendor- and mode-neutral (`send_email`, `book_meeting`). |

### 2.5 Ingestion, identity and enrichment

| Location | What is fixed | Target |
|---|---|---|
| `services/lead_ingestion.py:217-223` | Always creates a new Lead (`status="new"`) | One open lead per person per campaign; re-sighting adds a `lead_resourced` event and merges data. Re-open window configurable. |
| `services/lead_ingestion.py:280-301, 320-338` | Matching = exact domain / exact email only; no email → always new Person; no domain → always new Company | Deterministic matching cascade: source external ID → email → LinkedIn URL → (normalised name + company domain). Existing records **updated**, not ignored. |
| `services/identity_resolution.py` | Full resolver exists, but **only tests use it**; ingestion duplicates simpler logic | Ingestion uses the resolver. One identity service. |
| `models/base.py:124,173` | `domain`, `email` indexed but **not unique** | Partial unique indexes (`WHERE domain IS NOT NULL`), plus a `source_records(source, external_id)` mapping table. Prevents races between concurrent imports. |
| `integrations/registry.py:60-68` | Lead sources: two mocks; real ones commented out | Config-loaded. |
| `integrations/enrichment_registry.py:91-97` | Enrichment: one mock | Config-loaded; multiple providers; waterfall/fallback by field coverage. |
| `services/enrichment.py:233-260` | Last writer wins for every field; no per-field source/confidence/timestamp | Field provenance (`source`, `observed_at`, `confidence`) and merge policy from config (prefer verified, prefer newest, etc.). |
| `integrations/mock_*.py` | Fixture data | Fine as test/dev fixtures. Must not be the default runtime registration. |

### 2.6 Data model and events

| Location | What is fixed | Target |
|---|---|---|
| `models/base.py:81-112, 387-391` | `event_type` is a **Postgres ENUM**. 4 of the 7 migrations exist only to add event names. | `String` column validated against an event-type registry in code/config. New capabilities stop requiring migrations. |
| `models/base.py:50-60` | `LEAD_STATUS_VALUES` enum fixed | Keep a deterministic **lifecycle** state machine (it is a compliance/reporting boundary), but make stage names and allowed transitions configurable. |
| `models/base.py:62-70` | Interaction types fixed (email, call, LinkedIn…) | Channel registry; new channels without migrations. |
| `payload`, `result` columns | JSON stored as `Text` | `JSONB`: queryable for learning/analytics. |
| *(missing)* | No campaign/objective, no decision-outcome link, no suppression list, no approvals, no provider stats | New tables (see §4.7). |

### 2.7 RevOps, evals, config, runtime

| Location | What is fixed | Target |
|---|---|---|
| `revops/processor.py:104-126` | Event "reactions" are log strings; nothing is applied | Event handler registry: update lead state, record outcomes, **wake Oxygen** for that lead. |
| `revops/analytics.py:27-31,38` | Metrics hard-code `"email_sent"`, `"meeting"`, `"completed"/"done"` | Metrics defined from the event registry; outcome funnel per campaign. |
| `evals/cases.py`, `evals/runner.py:84,101,118` | Evals assert the **exact action** of the deterministic engine | Split into (a) deterministic **invariant** evals (safety must always hold), (b) **scenario** evals with acceptable-action sets and rubric/LLM-judge scoring, (c) replay of recorded real contexts. |
| `core/config.py:30-46` | Only DB/Redis/LLM keys/secret. No provider, model, retry, rate-limit, campaign or feature config | Layered, validated config (see §4.6). |
| `core/redis.py` | Connected but unused (health check only) | Rate limiting, circuit breaker state, queue. |
| `app/workers`, `app/agents` | Empty placeholders | Worker/scheduler process (§4.5). |
| `api/*` | No authentication | Auth + per-tenant scoping (deterministic). |
| `tests/test_oxygen.py` (20 assertions on exact deterministic actions) | Tests couple to the rule order | Keep for the fallback engine; add contract tests for any `DecisionEngine`. |

---

## 3. Deterministic workflows that should become adaptive

| Workflow today | Adaptive version | Stays deterministic inside it |
|---|---|---|
| Fixed sequence enrich → enrich → qualify → stop | Oxygen plans the **next best action** each cycle from context, objective, available capabilities and past outcomes. It may skip enrichment, research first, wait for a reply, stop, or escalate. | Allowed action set, parameter schema validation, guardrails, idempotency. |
| Qualification = "has email or domain", fixed scores | AI scoring against a configurable ICP and campaign objective, with written rationale and confidence; low confidence → human review | Score range, required rationale fields, audit event. |
| Canned research | Multi-source research, synthesised into a structured schema with citations | Source allow-list, PII handling, cost caps. |
| One email template | Personalised drafts using research + persona + campaign messaging; channel choice (email, LinkedIn…) by the agent | Compliance footer, suppression, frequency caps, content policy, approval gate, sending quotas. |
| One provider per job, no fallback | Provider chosen per call by config, health, rate budget, cost and historical success, with automatic failover | Credentials, rate limits, circuit breaker thresholds. |
| Failure = dead end | Retry transient errors; on permanent failure Oxygen sees the failure in context and **re-plans** (other provider, other channel, different data, give up) | Retry/backoff mechanics, max attempts. |
| Human triggers every step | Scheduler + workers drive leads that have new events, expired waits or due retries; inbound webhooks (replies, bounces, bookings) wake Oxygen | Kill switch, concurrency limits, per-cycle action caps. |
| No learning | Outcome attribution → provider stats and "what worked for similar leads" retrieved into Oxygen's context | Learning only changes **inputs** (rankings, retrieved examples), never code, guardrails or permissions. Prompt/policy changes are proposals for human approval. |

---

## 4. Target architecture

```
           Inbound webhooks (replies, bounces, bookings)    Lead source schedules
                              │                                      │
                              ▼                                      ▼
   ┌──────────────────── Event log (append-only, JSONB) ◄──── Ingestion + Identity (deterministic)
   │                          │
   │                          ▼
   │              Scheduler / Wake-up queue  (leads with new events, due waits, due retries)
   │                          │
   │                          ▼
   │   ┌───────────────── OXYGEN AGENT ─────────────────────────────┐
   │   │  ContextBuilder  →  Planner (LLM, structured output)        │
   │   │   lead+company+person, research, task results,              │
   │   │   interactions, campaign objective, capability catalogue,   │
   │   │   retrieved insights                                        │
   │   │                    ↓ Decision{capability, params, rationale,│
   │   │                       confidence, expected_outcome}         │
   │   │  Schema validation → repair once → fallback engine/escalate │
   │   └────────────────────────────┬───────────────────────────────┘
   │                                ▼
   │          GUARDRAILS (deterministic, config-tuned, fail-closed)
   │          schema · entity · idempotency · suppression · caps ·
   │          consent · quiet hours · content policy · approval gate · budget
   │                                ▼
   │                         Task (idempotency key, attempts, next_attempt_at)
   │                                ▼
   │          Executor workers (SKIP LOCKED claim, timeout, retry policy)
   │                                ▼
   │          ProviderSelector → Resilient provider client
   │          (config · health · circuit breaker · rate limit · cost · learned stats)
   │                                ▼
   │          Providers: lead sources · enrichment · research · LLM · email ·
   │                     LinkedIn · calendar · CRM   (each real or dry-run)
   │                                │
   └──────────── results / failures / outcomes ─────┘
                                    ▼
                    Outcome store + Insights (learning, read-only to Oxygen)
```

### 4.1 Unified capability catalogue *(replaces 4 registries' worth of hard-coding)*

Each **capability** (vendor-neutral) declares, in one place:

- `name`, `description` (what the LLM reads)
- `input_schema` / `output_schema` (Pydantic → JSON schema)
- `side_effect`: `read_only` | `internal_write` | `external_communication` | `spend`
- `requires`: entities/fields that must exist (`person.email`, `draft`, …)
- `repeatability`: `once_per_lead` | `once_per_target` | `repeatable(max, cooldown)`
- `idempotency_key` template

Each **provider** declares which capabilities it implements, plus health check, cost hint and
dry-run support. Providers are **loaded from config** (import path + settings), not from module
import side effects. Lead sources and enrichment providers are wrapped as capabilities
(`find_leads`, `enrich_company`, `enrich_person`) so there is one registry, one selection
mechanism and one guardrail path. Existing provider interfaces stay. Thin adapters wrap them.

### 4.2 Provider selection and resilience

`ProviderSelector.select(capability, context)`:

1. Candidates = enabled providers for the capability (config).
2. Filter: healthy, circuit closed, rate budget available, region/tenant allowed.
3. Rank: config priority, then learned success rate, latency and cost for this capability/segment.
4. Execute with fallback to the next candidate on retryable failure.

`ResilientClient` (wraps `httpx`), per provider in config: `timeout`, `max_retries`,
`backoff` (exponential + jitter), `retry_on` (status codes / error classes),
`rate_limit` (Redis token bucket), `circuit_breaker` (failure threshold, cool-down).
Errors are classified as `retryable`, `permanent` or `rate_limited`, and the classification is
recorded on the task.

### 4.3 Oxygen as an agent

- **ContextBuilder** gathers lead/company/person fields with provenance, research findings,
  results of previous tasks, interaction history (summarised when long), open waits, failures,
  the **campaign objective** (ICP, goal, messaging, constraints, from config/DB) and retrieved
  **insights** (similar leads that converted, provider performance).
- **Planner** (`LLMDecisionEngine`) receives the context plus the catalogue of *currently
  allowed and available* capabilities (filtered deterministically first), and returns a
  structured decision: capability, parameters, rationale, confidence, expected outcome, or
  `wait_until`, `stop`, `escalate`.
- **Validation:** parameters must match the capability's input schema. One repair attempt, then
  fallback to the deterministic engine or escalate. Never execute unvalidated output.
- **Model choice:** `llm.planner.provider/model/temperature/max_tokens` in config. The LLM itself is
  a provider (Anthropic/OpenAI/…) behind the same selector, with the same retries and rate limits.
- **Prompt-injection posture:** research text, scraped pages and inbound emails are passed as
  quoted data and can never change allowed capabilities or guardrails. Guardrails run after
  the planner regardless of what it says.
- Every decision is logged with its inputs hash, model, prompt version and rationale, so it is
  fully auditable and replayable for evals.

### 4.4 Content generation

Drafting, research synthesis and qualification are **LLM capabilities** using versioned prompt
templates stored in config/DB (not code), parameterised by campaign messaging. Output schemas
are enforced. Sending references a stored draft by ID. A deterministic content guardrail checks
it (unsubscribe footer, banned claims/phrases, link allow-list, length, unresolved placeholders),
and an optional human approval gate applies per campaign/side-effect class.

### 4.5 Background execution

- **Worker process** (separate entry point, same codebase). Claims tasks with
  `SELECT … FOR UPDATE SKIP LOCKED`, honouring `priority`, `due_at`, `next_attempt_at`.
  Postgres-based queue recommended first (no new infrastructure). ARQ/Celery later if volume needs it.
- **Scheduler** loop: leads with unprocessed events, expired `wait_until`, due retries, scheduled
  source pulls. It enqueues an Oxygen cycle per lead (one in flight per lead).
- **Inbound webhooks** (email replies/bounces/unsubscribes, calendar bookings) become events that
  wake the lead.
- **Controls:** global kill switch, per-campaign pause, max actions per lead per cycle/day,
  worker concurrency, all from config.

### 4.6 Configuration model

| Layer | Holds | Changed by |
|---|---|---|
| Environment / secret store | API keys, DSNs (referenced by name, never in config files) | Ops |
| Config files (YAML, validated by Pydantic) | Provider definitions, retries/timeouts/rate limits, model choices, guardrail thresholds, feature flags | Engineers, via review |
| Database | Campaigns (ICP, objective, messaging, constraints), suppression lists, approval settings, prompt template versions | Operators at runtime, audited |

### 4.7 Learning loop (without self-modification)

- **Outcome store:** links each decision/task to later outcomes (delivered, opened, replied,
  positive/negative reply, meeting booked, unsubscribed, bounced), with an attribution window.
- **Insights service** computes read-only aggregates: provider success/latency/cost per
  capability; message angle/channel performance per segment; action sequences that led to
  meetings; failure patterns.
- **Use:** (a) the ProviderSelector ranking; (b) retrieved into the planner's context as
  evidence ("for similar leads, X worked"); (c) *proposals* for prompt or campaign changes,
  which require human approval and must pass evals before activation.
- **Never learnable:** guardrails, permissions, suppression, caps, state machine, code.

### 4.8 Identity and deduplication (deterministic)

1. Ingestion uses `IdentityResolver` (one implementation).
2. Matching cascade: `source_records(source, external_id)` → normalised email → LinkedIn URL →
   normalised full name + company domain (exact). Fuzzy matches are only *suggested* for review.
3. Partial unique indexes on `companies.domain` and `people.email`. Upserts handle race conditions.
4. One open Lead per person (per campaign). A re-sighting appends a `lead_resourced` event and
   merges new fields under the provenance policy.
5. Backfill migration to merge existing duplicates.

---

## 5. What must remain deterministic

| Area | Why |
|---|---|
| Guardrail evaluation (existing GR-001…009, data-driven) | Safety boundary; must be predictable, testable and auditable. |
| **New compliance guardrails:** suppression/unsubscribe list, contact frequency caps (person/domain/day), sending quotas per sender/domain, quiet hours by recipient time zone, consent/region rules (GDPR, CAN-SPAM, CASL), content policy checks, human-approval gate, per-lead/per-campaign cost caps, max actions per cycle | Legal exposure and sender reputation. Required before any real outbound action. |
| Task state machine, row locking, idempotency keys | Prevents double sends and races. |
| Identity resolution and normalisation | Duplicates directly cause duplicate outreach. |
| Schema validation of all LLM outputs and API inputs | The LLM proposes; code decides what is valid. |
| Retry/backoff/rate-limit/circuit-breaker mechanics | Their *parameters* are configurable; their behaviour must be exact. |
| Lead lifecycle transitions | Reporting and compliance rely on them; stage names configurable. |
| Append-only event log and decision audit trail | Accountability and eval replay. |
| AuthN/AuthZ, tenant isolation, secret handling | Security. |
| Kill switch and pause controls | Operational safety. |

---

## 6. Phased implementation plan

Each phase keeps the test suite green and the demo working. Phases 1–3 need **no** real API
keys or LLM spend.

### Phase 1: Foundations: configuration and unified capabilities *(~1.5–2 weeks)*
- Layered settings (YAML + env + Pydantic) for providers, retries, timeouts, rate limits, models, feature flags.
- Unified capability catalogue with schemas, side-effect class, requirements, repeatability, idempotency.
- Config-driven provider loading. Remove import-time self-registration. Mocks enabled only in dev/test profile.
- `ProviderSelector` (priority + availability + fallback). Remove all 9 `"mock_enrichment"` literals.
- Guardrails rewritten to read capability metadata (GR-003/005/007/008), with no task-type literals.
- `event_type` → validated string; `payload`/`result` → JSONB (migrations).
- **Depends on:** nothing. **Unblocks:** everything.

### Phase 2: Identity and resilience *(~1.5 weeks)*
- Ingestion uses `IdentityResolver`; matching cascade; `source_records` table; partial unique indexes; one-open-lead rule; duplicate backfill/merge.
- Field provenance and merge policy for enrichment.
- `ResilientClient`: timeouts, retries with backoff, Redis rate limiting, circuit breaker.
- Task retry state (`attempt_count`, `max_attempts`, `next_attempt_at`, error classification); failures surfaced to Oxygen's context.
- **Depends on:** Phase 1 (config, selector).

### Phase 3: Background execution *(~1–1.5 weeks)*
- Worker entry point with `SKIP LOCKED` claiming, priority and due times.
- Scheduler (new events, `wait_until`, retries, source pulls); one cycle in flight per lead.
- Real event handlers in RevOps (state updates + wake-ups); inbound webhook endpoints (signature-verified).
- Kill switch, concurrency and per-lead action caps.
- **Depends on:** Phase 2 (retries), Phase 1.

### Phase 4: Compliance guardrails *(~1 week, must finish before Phase 6 sends anything real)*
- Suppression list and unsubscribe handling, frequency caps, send quotas, quiet hours, consent/region rules, content policy, human-approval queue, cost caps.
- Deterministic invariant evals for each rule.
- **Depends on:** Phase 1 (capability side-effect classes).

### Phase 5: Adaptive Oxygen *(~2–3 weeks)*
- Async `DecisionEngine`; `LLMDecisionEngine` with structured output, schema validation, repair, fallback to the current engine, escalation.
- Context builder extended (research, task results, interactions, objective, failures).
- Campaigns/objectives table and config; `wait_until`, `stop`, `escalate`.
- LLM as a provider (model from config); decision audit (prompt version, model, rationale).
- AI capabilities: research synthesis, ICP qualification, personalised drafting (versioned prompts); send references approved draft.
- Evals reworked: invariant (deterministic, CI) + scenario/rubric + replay.
- **Depends on:** Phases 1–4.

### Phase 6: Real integrations *(incremental, ~1 week each)*
Order by risk: lead source + enrichment (read-only) → CRM sync → email (dry-run first, then
limited live behind approval gate) → calendar → LinkedIn/other channels.
- **Depends on:** Phases 1–4 (Phase 5 for AI content).

### Phase 7: Learning loop *(~1.5–2 weeks, after real outcomes exist)*
- Outcome store and attribution; provider stats feeding the selector; retrieval of similar successful leads into planner context; human-approved prompt/campaign proposals gated by evals.
- **Depends on:** Phases 3, 5, 6 (needs real outcome data).

### Dependency graph

```
Phase 1 ──► Phase 2 ──► Phase 3 ──┐
   │                              ├──► Phase 5 ──► Phase 6 ──► Phase 7
   └──────► Phase 4 ──────────────┘
```

Phase 4 can run in parallel with Phases 2–3.

---

## 7. Risks and decisions needed

| Decision | Options | Recommendation |
|---|---|---|
| LLM provider(s) | Anthropic / OpenAI / both behind the selector | Both supported by design. Pick one default in config. |
| Queue technology | Postgres `SKIP LOCKED` / ARQ (Redis) / Celery / Temporal | Postgres first (no new infra). Revisit at scale. |
| Human approval for outbound | Always / until trust threshold / never | Always, at first. Relax per campaign by config once evals and live results justify it. |
| Multi-tenancy | Now / later | Add `tenant_id` in Phase 1 migrations if more than one client is expected. Cheap now, expensive later. |
| Lead model | One lead per person / per person per campaign | Per person per campaign. |

**Main risks:** LLM cost and latency per cycle (mitigate: decide only on new information, cheap
model for routine steps, caching); non-deterministic behaviour making tests flaky (mitigate:
contract tests + invariant evals; LLM evals outside the unit suite); deliverability and legal
exposure from real sends (mitigate: Phase 4 before Phase 6, dry-run mode, approval gate).

---

## 8. What is kept as-is

`TaskExecutor` locking and lifecycle (extended, not replaced) · event log · guardrail registry
and fail-closed evaluation · provider interfaces (`ExecutionProvider`, `LeadSourceProvider`,
enrichment providers) · normalisation utilities · `IdentityResolver` · RevOps cursor
processing · API structure · `DeterministicDecisionEngine` (as fallback and test reference).

---

## 9. Implementation log

Decisions confirmed before implementation: **OpenAI** as default LLM (vendor and model configurable),
**PostgreSQL-based queue** first, **human approval for all external communication** (configurable),
**multi-tenant** data model from the start.

### Phase 1: Configuration, capability catalogue, provider selection — ✅ complete

| Area | What changed | Where |
|---|---|---|
| Layered config | YAML base + profile overlay + deployment file, `include:`, providers merged by name, per-tenant overrides, secrets by env-var name | `app/core/gtm_config.py`, `config/` |
| No mock defaults | Base config enables only the built-in CRM. Mocks live in `config/providers/mocks.yaml`, included only by `development`/`test` profiles | `config/gtm.yaml`, `config/gtm.{development,test}.yaml` |
| Capability catalogue | 9 capabilities with input schemas, side effects, requirements, repeatability, aliases (old names still accepted) | `app/capabilities/` |
| Unified provider registry | One registry for lead sources, enrichment, GTM providers; many providers per capability; built from config; unavailable providers kept with reasons; old registries are views onto it | `app/providers/registry.py`, `loader.py`; `app/execution/registry.py`, `app/integrations/registry.py`, `app/integrations/enrichment_registry.py`, `app/oxygen/registry.py` |
| Provider selection | Tenant/capability policy, credentials, constraints, cost ceiling, failure cooldown, rate limits; ranked by priority, event-log reliability, latency, cost; explainable | `app/providers/selection.py`, `runtime.py` |
| Executor | Ranks providers per task, per-provider timeout, failover on retryable errors, every attempt + duration recorded in events | `app/execution/executor.py` |
| Hard-coded providers removed | All `"mock_enrichment"` defaults and import-time registrations removed; enrichment chooses via selector | `services/enrichment.py`, `execution/workers.py`, `integrations/gtm/__init__.py`, enrichment API |
| Guardrails | GR-001…008 read capability metadata (schema validation, target type, requirements, repeatability with max/cooldown, tenant enablement, live provider availability) | `app/guardrails/rules.py`, `evaluator.py`, `models.py` |
| Multi-tenancy | `organizations` table, default org seeded, `tenant_id` on companies/people/leads/interactions/tasks/events; tasks and events inherit the lead's tenant | `app/models/base.py`, migration `0008` |
| Extensible events | `events.event_type` is a validated string (registry in code), no migration per new event type | `app/events/types.py`, migration `0008` |
| Tests | 37 new tests (`tests/test_phase1_platform.py`), including a static check that core modules never name mock providers. Full suite: 259 passed, 5 skipped. | `tests/` |

**Known Phase 1 limits (addressed later):** health/rate-limit state is per process (Phase 2 moves it to
Redis); a failed task is still terminal (Phase 2 adds retry scheduling); idempotency keys are defined on
capabilities but not yet persisted on tasks (Phase 2); the approval policy is configured and queryable
(`CapabilityCatalogue.requires_approval`) but enforced in Phase 4; Oxygen still uses the rules engine
(LLM planner arrives in Phase 5).

### Phase 2: Identity, deduplication, retries, idempotency — ✅ complete

| Area | What changed | Where |
|---|---|---|
| One matching implementation | `IdentityResolver` is now the only matching logic, tenant-scoped, with configurable ordered keys: company `source_record → domain → website → linkedin_url (→ name, opt-in)`, person `source_record → email → linkedin_url → name_and_company`. Exact matches only. | `app/services/identity_resolution.py`, `identity:` in config |
| Ingestion uses it | Ingestion no longer has its own matching. Existing records are merged by policy (`fill_missing` / `prefer_incoming`); identity keys (email, domain) are only filled, never overwritten. Inserts run in savepoints; a concurrent import that hits a unique index resolves to the winner's row. | `app/services/lead_ingestion.py` |
| Duplicate leads prevented | One **open** lead per person (DB-enforced). A re-sighting attaches to it (`lead_resourced` event). Terminal statuses and closed-lead policy (`attach` / `create_new`) are config. | `identity.lead` |
| Source records | `source_records(tenant, source, external_id)` maps a provider's own id to our entities, so a re-import matches even if the email changed. | `SourceRecord` model |
| DB constraints | Global `uq_companies_domain` / `uq_people_email` (from 0001) replaced by tenant-scoped partial unique indexes; `uq_leads_open_person`; `uq_tasks_tenant_idempotency`. Existing duplicate open leads are merged (history repointed to the oldest). | migration `0009` |
| Shared runtime state | Provider failure cooldowns and rate limits live in **Redis** (atomic Lua sliding window using the Redis clock), shared by every worker and API process; falls back to per-process state if Redis is down (configurable). Only infrastructure errors count against health. | `app/providers/runtime.py`, `runtime:` in config |
| Retries & timeouts | Per provider: timeout, in-place retries with backoff, failover. Per task: transient failures reschedule with exponential backoff + jitter (`next_attempt_at`), up to `max_attempts` (global or per capability). Vendor `RATE_LIMITED` + `retry_after_s` blocks the provider and delays the task. | `app/execution/executor.py`, `execution.retry` |
| No repeated external actions | If an external action (`send_email`, `book_meeting`, spend) times out or raises, it is **not** retried or failed over unless the provider declares `supports_idempotency`; it is flagged `needs_review`. Providers receive an idempotency key in the context metadata. | executor |
| Idempotency | Capabilities that declare idempotency fields (e.g. `send_email` → `draft_task_id`) get a stable key, stored on the task, unique per tenant in the DB; GR-010 blocks repeating a recorded action; the orchestrator handles a unique-index hit as "already recorded". | `spec.py`, `orchestrator.py`, GR-010 |
| Recovery | `retry_failed_task` re-queues the **same row** (audited, `task_retry_requested`); `needs_review` tasks require explicit acknowledgement. `recover_stale_tasks` uses execution leases: safe capabilities resume, external ones go to review. API: `POST /execution/tasks/{id}/retry`, `POST /execution/recover-stale`. | `app/execution/recovery.py` |
| Tests | 25 new tests (`tests/test_phase2_reliability.py`), including concurrent imports, cross-worker Redis cooldown/rate limits, Redis outage fallback, ambiguous-send safety. Full suite: **284 passed, 5 skipped**. | `tests/` |

Notes: two existing ingestion tests asserted the old duplicate-lead behaviour and were updated deliberately.
Alembic runs migrations non-transactionally in this setup, so a failed migration can leave partial changes;
0009's duplicate merge was rewritten without temp tables so a full `downgrade base` → `upgrade head` works.

### Next
The product brief of 2026-11-25 ("Oxygen — Autonomous AI Sales Operating System") supersedes the
remaining phases here. See `docs/INTEGRATION_ASSESSMENT.md` for the reference-repo decisions and the
revised plan.
