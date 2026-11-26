# Oxygen — Reference Repository Assessment & Revised Plan

Date: 2026-11-25 · Status: assessment complete; platform Phases 1–2, **Phase A (memory + compliance)**, **Phase B (AI Sales Controller)**, **Phase C (LLM capabilities)** **Phases D/E (lead sources + event loop)** **Phase F (RevOps & learning)** and **Phase G (email/inbox, dashboard, demo, one-command start)** implemented; nothing committed.

Each repository was inspected at source level (code, schema, routes, licence), not from READMEs alone.
Statements marked **(live)** still need a running instance to confirm.

---

## 1. Decisions at a glance

| Repo | Licence | What it really is | Decision | Integration boundary |
|---|---|---|---|---|
| **Scout** (kiryano/Scout) | MIT | Interactive Python CLI that scrapes **one known profile at a time** (GitHub, YouTube, Instagram, TikTok, LinkedIn via session cookie, Twitch, Pinterest, Linktree) + regex / pattern / SMTP email guessing. **No search or discovery.** No API, no JSON output, no tests. | **Narrow adapter only.** Not a TAM / lead-discovery source. | `ScoutProfileProvider`: vendored scraper functions (MIT notice kept), run in a thread, **GitHub (official API) and YouTube only**. LinkedIn cookie scraping, free proxies and SMTP probing disabled. All Scout emails are stored as *unverified hints* unless independently verified. |
| **Comp AI CRM** (trycompai/crm) | MIT | TypeScript / Next.js / NestJS / Prisma on Vercel infrastructure (eve agents, AI Gateway). **Single-tenant.** No public agent API, no MCP server. | **Reuse patterns, build in our Postgres.** No second CRM. | Port: evidence ledger (evidence kinds with weights, noisy-OR scoring, contradiction cap, "verified needs a primary source"), fact lifecycle (applied / proposed / superseded / dismissed; never overwrite human-entered values), leased `SKIP LOCKED` work queue, scheduled rechecks via `due_at`, per-run budgets, persisted agent runs / actions. |
| **BillionMail** | **AGPL-3.0** | Self-hosted mail server (Postfix / Dovecot / Rspamd) + Go campaign manager. Send API `POST /api/batch_mail/api/send` (`x-api-key`) only takes `{addresser, recipient, attribs}`; subject/body come from a pre-created template; **queued, sent within ~60 s, no message id returned, no idempotency, no webhooks, no reply detection.** | **Optional email-infrastructure adapter, run as a separate unmodified container.** Not the default sender. | HTTP only; never vendor or link its code (AGPL §13). Pass our idempotency key in `attribs`; de-duplicate on our side; read delivery / bounce / open / click via a read-only DB view polled on a cursor **(live)**. Replies via our own IMAP poll. Suppression, rate limits and idempotency stay in Oxygen. A generic **SMTP provider** is the primary email adapter; BillionMail is one deployment option behind the same `send_email` capability. |
| **AutoClip** | MIT | Python / FastAPI / ffmpeg pipeline that turns long videos into captioned short clips; CLI, MCP (stdio) and HTTP job API (submit → poll). LLM defaults are China-centric (Qwen) but OpenAI / Ollama work. **No auth on its API.** Adds an AutoClip logo outro by default; downloads YouTube/Bilibili with yt-dlp. | **Optional capability, later phase, behind a feature flag.** | Separate private container. Oxygen fetches only **allow-listed, authorised** source media itself and hands AutoClip a local file (never a URL); branding, analytics and publishing disabled; every clip goes to human review. Capability `generate_video_clip`. Estimated 3–5 engineer-days + quality evaluation **(live)**. |

**Lead discovery (TAM sourcing)** therefore needs a real search source: **Apollo** (API key, adapter behind
`find_leads`), plus **CSV / inbound** imports. Without an Apollo key the system runs on clearly labelled
sample data; it will not claim live discovery.

---

## 2. What already exists (Phases 1–2) and maps onto the brief

| Brief requirement | Status |
|---|---|
| Configurable LLM / execution providers, adapter architecture, environment secrets | ✅ config layers, unified capability catalogue + provider registry, credentials by env-var name |
| Provider selection by availability, cost, limits, reliability; health checks; graceful degradation | ✅ selector + Redis-shared health / rate limits, failover, unavailable providers reported with reasons |
| Provider retries, failover, idempotent external actions | ✅ retries with backoff, ambiguity safety (`needs_review`), DB-unique idempotency keys, recovery API |
| Identity resolution, duplicate detection, domain / website matching (Layer 2 core) | ✅ single `IdentityResolver`, tenant-scoped constraints, source-record mapping |
| Tenant isolation, audit logging foundations | ✅ `tenant_id` everywhere, append-only events |
| Deterministic guardrails | ✅ metadata-driven GR-001…010 |

## 3. What must be built

1. **Persistent sales memory (Layer 3):** evidence ledger + fact lifecycle, research findings, agent runs /
   decisions / actions, suppression list, consent records, objections, intent signals — all in our Postgres.
2. **Sales Controller (Layer 4):** LLM planner (OpenAI default via config) producing structured *action
   proposals* with: action, reason, evidence references, expected outcome, capability, inputs, confidence,
   uncertainty, risks, approval requirement. Schema validation, deterministic fallback, every decision persisted.
3. **Approval Centre + compliance guardrails:** approval queue (review / edit / approve / reject),
   suppression, contact-frequency caps, consent, quiet hours, spending limits. **Built before any real send.**
4. **Capabilities:** LLM research synthesis (web fetch with source citations), intent analysis,
   qualification against configurable ICP, personalised drafting, SMTP / BillionMail email, LinkedIn and
   phone *task preparation* (no automation of LinkedIn), calendar (mock + Google Calendar adapter), CRM update,
   optional video clip.
5. **Event processing + autonomy:** Postgres `SKIP LOCKED` worker and scheduler, webhooks / pollers for replies,
   bounces, meetings; reply classification (LLM) with deterministic opt-out detection → suppression.
6. **RevOps & learning:** outcome attribution, channel / segment / provider / cost metrics, retrieval of
   similar successful journeys into the planner's context.
7. **Dashboard:** React (Vite) SPA — Overview, Lead Intelligence, Oxygen Control Centre, Pipeline,
   Approval Centre, RevOps — on real API data with live updates (SSE). One-command start.
8. **Demo scenarios:** four seeded, clearly labelled sample prospects (high intent, insufficient
   information, no response, negative response / opt-out) that take visibly different paths.

## 4. Revised implementation order (and why it differs from the brief)

| # | Phase | Why here |
|---|---|---|
| A | **Persistent memory + compliance foundations** (evidence ledger, findings, decisions, suppression, consent, approval queue tables) | The controller's context and the approval workflow both read from it. Brief's Phase 3, moved first. |
| B | **Sales Controller** (LLM planner, proposal schema, validation, fallback, persisted reasoning) | Core differentiator; depends on A. |
| C | **Capabilities wired to the controller** (research, intent, qualification, drafting, email via SMTP/BillionMail in dry-run, CRM, meetings mock) + **approval enforcement** | Gives the controller real actions; approval and suppression enforced before any send. |
| D | **Lead sources** (Apollo adapter, CSV / inbound import, Scout profile adapter) | Independent of B/C; can run in parallel. Brief's Phase 2. |
| E | **Event loop** (worker, scheduler, inbound events, reply classification, re-evaluation) | Makes it continuous instead of request-driven. |
| F | **RevOps & learning** | Needs outcomes from E. |
| G | **Dashboard + demo scenarios + one-command start** | Built on the real APIs from A–F; a thin version of the Control Centre can start alongside B for early demos. |
| H | **AutoClip video capability** (optional, flagged) | Lowest value per effort; needs a separate service and rights controls. |

## 5. Honesty rules for the demo

- Every provider shows its mode (`live` / `dry_run` / `mock`) in the UI and API.
- Without `OPENAI_API_KEY` the controller runs on the deterministic fallback and **says so** in each decision.
- Sample prospects are labelled as sample data; no real people are contacted in dry-run mode.
- BillionMail, Apollo and Google Calendar paths are marked *unverified* until run against a live instance.


---

## 6. Implementation log (revised plan)

### Phase A — Persistent sales memory + compliance/approval foundations — ✅ complete

Built inside the existing Postgres CRM (no second CRM); new tables only where no equivalent existed
(migration `0010`, round-trip tested).

| Requirement | Implementation | Where |
|---|---|---|
| Persistent company & prospect memory | `build_prospect_memory(lead)` assembles CRM record, facts with evidence, findings, interactions, decisions, tasks, approvals, open work and live compliance status. `GET /api/v1/memory/leads/{id}` | `app/memory/context.py` |
| Evidence ledger (source, timestamp, confidence) | `facts` + `fact_evidence`. Confidence is computed from configured evidence kinds (noisy-OR, contradiction cap, *verified needs a primary source*), never self-reported by an AI. | `app/memory/facts.py`, `memory:` config |
| Fact lifecycle | proposed / applied / superseded / dismissed. Empty fields filled by ≥ probable facts; only verified facts overwrite; human-entered values never overwritten; dismissed values never re-proposed; identity keys can't collide. Review: `POST /memory/facts/{id}/accept|dismiss`. | same |
| Ingestion & enrichment use it | Every ingested / enriched value goes through the ledger (lead-source evidence; scraped sources like Scout configurable as weak `pattern_guess`; vendor enrichment as `vendor_enrichment`). The old ad-hoc merge code was removed. | `lead_ingestion.py`, `enrichment.py` |
| Interaction & decision history | Interactions gain `task_id`, `channel`, `provider`, `details`; executor records every successful external touch. `agent_decisions` stores every Oxygen proposal (engine, reasoning, evidence refs, confidence, risks, guardrail result, approval need) and its outcome (queued → executed / failed / blocked / awaiting_approval / approved / rejected). | `models/memory.py`, orchestrator, executor |
| Research findings | `research_findings` (brief / intent_signal / news / objection…) with citations, confidence and freshness. | `app/memory/records.py` |
| Work queue & scheduled rechecks | `work_items`: one open item per (lead, kind), earliest due wins, `FOR UPDATE SKIP LOCKED` leasing, attempts + retirement. `POST /memory/leads/{id}/rechecks`, `GET /memory/work-items`. | same |
| Suppression lists | email / domain / person / company; checked against the contact's email domain and company domain. `POST /compliance/suppressions`, `/lift`. | `app/compliance/rules.py` |
| Contact frequency limits | Configurable caps per person / domain / company over rolling windows + minimum gap between touches, counted from real outbound interactions. | same, `compliance:` config |
| Lawful basis | `contact_consents`; external contact requires an un-withdrawn allowed basis (B2B legitimate-interest default recorded at ingestion is configurable / can be disabled). | same |
| Human approval for all external communication | Tasks for external capabilities are created `awaiting_approval` with an `approvals` row (proposed content, reasoning, evidence, expiry). Approve (optionally edited — re-validated against the schema, compliance re-checked) binds a SHA-256 of the exact payload; reject / expiry cancels. `GET/POST /api/v1/approvals…` | `app/compliance/approvals.py` |
| Deterministic enforcement, twice | Guardrails GR-011 (suppression), GR-012 (lawful basis), GR-013 (frequency) — fail closed when status is unknown. The executor re-checks right before any provider call: approval present and payload hash unchanged, compliance still allows the contact, daily spend limit. Otherwise the task is cancelled and no provider is called. | `rules.py` (guardrails), `executor.py` |
| Tests | 26 new (`tests/test_phaseA_memory.py`); external-action tests now go through consent + approval. Full suite **310 passed, 5 skipped**. | `tests/` |

Not yet: authentication (reviewer identity is passed explicitly and audited, but not authenticated);
quiet-hours by recipient time zone; automatic suppression from inbound unsubscribe/bounce events
(Phase E, the event loop).

### Phase B — Adaptive AI Sales Controller — ✅ complete

| Requirement | Implementation | Where |
|---|---|---|
| Vendor-neutral LLM layer | `LLMRouter` resolves a profile (planner / writer) to vendor + model from config and falls back through `fallback_profiles`. Adapters use the official SDKs: OpenAI (strict `json_schema`) and Anthropic (`output_config.format` + explicit `effort`, no sampling params, server-side refusal fallback `fallbacks: "default"` — configurable per profile). Keys by env-var name; `.env` is loaded into the environment. | `app/llm/` |
| Default model choice | `planner`: OpenAI `gpt-4.1` (your choice); `planner_backup`: Anthropic `claude-opus-5-5`. Both are config values. | `config/gtm.yaml` |
| Reasoning over memory | The planner receives the full prospect memory (facts with band/status/sources, findings, interactions, decision history, tasks, approvals, scheduled work, compliance) plus the campaign objective from config. | `app/oxygen/planner.py` |
| Dynamic next best action | Allowed actions are generated per lead: catalogue capabilities that are enabled, have an eligible provider and whose data requirements are met, plus `wait` / `stop` / `escalate`. No fixed sequence. | `app/oxygen/llm_engine.py` |
| Structured proposals | Strict schema: situation summary, missing information, interest signals, worth pursuing, action, channel, parameters, reasoning, evidence refs, expected outcome, confidence, uncertainty, risks, approval view, wait time, alternatives considered. | `planner.py` |
| Validation of every proposal | Action must be allowed; parameters validated against the capability's input schema; evidence ids must exist in memory (others dropped and recorded); one repair round with the errors; still invalid → rules fallback. Confidence below `oxygen.min_confidence` → escalated to a human. | `llm_engine.py` |
| Deterministic fallback | No key / vendor errors / refusals on every profile → rules engine decides; the decision is labelled `rules_fallback` with the reason. | same |
| Adapts its workflow | `wait` schedules a re-evaluation work item; `escalate` creates a `human_review` item; `stop` closes open work. Email proposals must reference a completed draft for that lead; its subject/body are copied into the proposal so the reviewer approves the real text. | `orchestrator.py` |
| Nothing bypasses controls | Every LLM decision goes through the same guardrails (GR-001…013), approval routing and executor gates as before. | unchanged |
| Audit | `agent_decisions` stores engine, model, prompt version, reasoning, evidence, confidence, risks, guardrail result, alternatives, LLM latency/usage/fallback attempts. | `orchestrator._persist_decision` |
| API | `GET /oxygen/engine` (configured vs effective engine, which profiles are usable and why), `GET /oxygen/leads/{id}/decisions`, `POST /oxygen/leads/{id}/cycle` (decide → execute if allowed → next step), richer `decide` / `orchestrate` responses. | `app/api/routes/oxygen.py` |
| Tests | 16 new, all with scripted vendor clients (no network / no spend), including exact vendor request shapes and refusal handling. Full suite **326 passed, 5 skipped**. | `tests/test_phaseB_controller.py` |

Honest status: the controller has only been exercised with scripted LLM responses and the rules
fallback. It has **not yet been run against a live OpenAI or Anthropic key**; prompt quality on real
prospects still needs evaluation once a key is configured.

### Phase C — LLM-backed capabilities — ✅ complete

| Capability | What it does | Grounding / safety (deterministic) |
|---|---|---|
| `research_prospect` (`llm_research`) | Reads the prospect company's **own website only** (configured paths, robots.txt respected, size/time caps, untrusted text), synthesises a brief, findings (initiatives, hiring, news, pain points, technologies, intent signals) and candidate facts. | Every finding/fact must carry a **verbatim quote** that is checked against the fetched page text; ungrounded items are dropped and counted. Facts are limited to an allow-list of fields and recorded as `web_cited_claim` evidence, so they are *proposed*, not applied, unless corroborated. An unreachable site is reported, never filled in. |
| `analyze_intent` (`llm_intent`, new) | Intent level, 0–100 score, signals and counter-signals, timing hypothesis → `intent_assessment` finding. | Every signal must cite an id that exists in memory (one repair round). |
| `qualify_lead` (`llm_qualification`) | Scores fit against the configured ICP across dimensions; updates lead score/status (only in early lifecycle stages) and records a `qualification` finding. | Cited dimensions; deterministic floor `campaign.qualification_min_score`; any disqualifier ⇒ not qualified. |
| `draft_outreach` (`llm_drafting`) | Personalised email, LinkedIn message or phone call script from memory + research, sender persona from config. | Content policy (config `oxygen.outreach`): channel match, subject required/length, word limits per channel, banned phrases, no placeholders, ≥1 evidence-backed personalisation point, every claim cites memory; one repair round; email opt-out footer appended. |
| `create_rep_task` (`internal_rep_tasks`, new) | Hands LinkedIn/phone actions to a human rep with the prepared message or script (`rep_task_<channel>` work item). | No automated LinkedIn/phone actions. |

Availability is honest: `llm_*` providers are constructed only when their LLM profile has a usable
key; otherwise they are registered as unavailable with the reason (visible at
`GET /api/v1/execution/providers`) and the selector uses the mock providers in dev/test, or no provider
in production (the planner then doesn't see the capability). With a key, they outrank the mocks
(priority 80 vs 50).

Tests: 14 new (`tests/test_phaseC_capabilities.py`), including an end-to-end loop — LLM planner →
research → draft → send proposal (draft text attached) → human approval → send → interaction recorded
→ planner waits — all with scripted LLM output. Full suite **340 passed, 5 skipped**.

Not yet: live-key evaluation of research/drafting quality; reply/bounce/meeting event processing and
automatic re-evaluation (Phase E); real email/calendar providers (SMTP / BillionMail / Google Calendar).


### Phases D & E — Lead sources + event-driven autonomy — ✅ complete

**Event loop (E)**

| Piece | Implementation | Where |
|---|---|---|
| Inbound webhooks | `POST /webhooks/email` (reply, bounce, delivered, opened, clicked, unsubscribe, complaint) and `/webhooks/calendar` (booked, cancelled, rescheduled) in a vendor-neutral format; provider adapters translate into it. HMAC-SHA256 signature over `timestamp.body` with replay window; unsigned only when `webhooks.allow_unsigned` (dev/test). Duplicate provider event ids ignored; unmatched events stored for review. | `app/events/inbound.py`, `app/api/routes/inbound.py` |
| Reply handling | Matched by our outbound message id, else sender email. Stored as inbound interaction. **Deterministic opt-out detection** (config regexes, quoted text ignored) always suppresses the contact and withdraws consent. AI classification (category, objection, follow-up timing, referral) must quote the reply verbatim; ungrounded or no LLM ⇒ human review. Objections and analyses become findings; out-of-office schedules a later re-evaluation. | same |
| Bounces / unsubscribes / complaints | Hard bounce ⇒ email suppressed; unsubscribe/complaint ⇒ suppressed + consent withdrawn; engagement events recorded (opens treated as weak signals). | same |
| Meetings | Booked ⇒ meeting interaction + lead `meeting_booked`; cancelled ⇒ back to `replied`. | same |
| Reactions | Config `autonomy.reactions` decides which events wake Oxygen (lead ingested, task completed/failed, approval rejected, reply, bounce, meeting…), with delay and priority; durable cursor. | `app/services/autonomy.py` |
| Worker | `python -m app.worker` (or `POST /autonomy/tick`): stale-task recovery, approval expiry, reactions → evaluation work items (leased, SKIP LOCKED) → Oxygen cycles → due tasks (retries and **human-approved actions**) executed. | `app/worker.py`, `autonomy.py` |
| Safety rails | Global pause shared via Redis (`POST /autonomy/pause|resume`), per-prospect daily cycle cap, one open work item per (lead, kind), plus all existing guardrails/approval/compliance gates. | same |

**Lead sources (D)**

| Source | Status |
|---|---|
| CSV import (`POST /leads/import`, header aliases in config) | ✅ working |
| Inbound website form (`POST /webhooks/inbound-lead`, signed) — records the consent the form captured | ✅ working |
| Apollo (`apollo` provider): People API Search (0 credits) then capped People Enrichment (credits) — obfuscated last names never stored | ⚠️ built from Apollo's API reference and tested against a mock server; **not verified live** (needs `APOLLO_API_KEY`) |
| Scout (`scout` provider): GitHub/YouTube profiles from a local checkout via subprocess; LinkedIn, proxies and SMTP probing never used | ✅ adapter tested with a fake checkout; scraped emails are weak evidence — **proposed facts only, never used for matching or contact** |

Also fixed: the rules engine retried enrichment forever for companies the vendor has no data on;
a failed attempt now counts as attempted.

Tests: 20 new (`tests/test_phaseDE_events_sources.py`). Full suite **360 passed, 5 skipped**. Verified
live on the dev server: CSV import → worker ticks ran enrich → enrich → qualify → no-action without
any manual cycle; an opt-out reply suppressed the contact; pause stopped the loop.

Not yet: real email sending/receiving (SMTP + IMAP or BillionMail poller → these webhooks), real
calendar provider, RevOps learning (Phase F), dashboard + one-command start + demo scenarios (Phase G).


### Phase F — RevOps, evaluation & learning — ✅ complete

| Piece | Implementation | Where |
|---|---|---|
| Outcome attribution | One `outcome_attributions` row per outbound touch (email, LinkedIn, call): outcome = strongest of meeting > positive reply > objection > reply > not interested > unsubscribed > bounced > none, attributed to the **latest touch before the outcome** within `revops.attribution_window_days`; `final` once the window closes. Each row records channel, angle, personalisation points, whether research preceded it, deciding engine, drafting and sending providers, and segment (industry, size band, seniority). Idempotent; refreshed every worker tick. | `app/revops/outcomes.py`, migration `0011` |
| RevOps dashboard | `GET /revops/dashboard?days=`: funnel with step conversion; reply / positive / meeting / negative rates overall and by channel, angle, engine, personalisation depth, research-first vs not, draft provider; segment conversion; qualification accuracy (score bucket vs outcome); provider success, latency and cost; cost per positive reply / meeting; approval status, edit rate, time to decision; decisions by engine and status, fallback rate, guardrail blocks by rule, send-time compliance blocks. Every rate carries `n` and a `weak` flag. | `app/revops/dashboard.py` |
| Recommendations | Deterministic, evidence-backed suggestions (under-performing channel, research effect, failing provider, heavy draft editing, high fallback rate) — proposals for a human only. | same |
| Learning → planning | The planner context now includes `learned_insights`: peer-segment outcome rates by channel / angle / research (with n and weak flags) and anonymised converted journeys (actions that preceded a meeting or positive reply). The prompt treats them as evidence weighed by sample size, never as permission to skip controls. `GET /revops/insights/leads/{id}` shows exactly what the planner sees. | `app/revops/insights.py`, planner |
| Learning → provider selection | New selector weight `outcome_quality`: Beta-smoothed positive-outcome rate of the touches each provider drafted/sent (final attributions only). Without outcome data the weight is not applied. | `selection.py`, `selection.weights` |
| Not learnable | Code, prompts, guardrails, compliance, permissions and configuration never change automatically. | — |

Tests: 10 new (`tests/test_phaseF_revops.py`). Full suite **370 passed, 5 skipped**.

Known issue to fix next: the test suite truncates the same database the dev server uses (`gtm_db`), so
running tests wipes demo data. Phase G should give tests their own database.

### Phase G — Real email & inbox, dashboard, demo, one-command start — ✅ complete

| Piece | Implementation | Where |
|---|---|---|
| Separate test database | Tests run against `gtm_test` (auto-created and migrated) with their own Redis prefix; the development database is never touched. Connection pool reset per test fixed 5 tests that had been silently skipping (two of them were stale and are corrected). | `tests/conftest.py` |
| SMTP sending | `SmtpEmailProvider` (`send_email`): recipient locked to the stored address, task-derived `Message-ID`, `List-Unsubscribe`, dry-run mode, no retries on ambiguous failures. Tested against a local SMTP server. | `app/integrations/email_smtp.py` |
| Meeting invitations | `SmtpMeetingProvider` (`book_meeting`): iCalendar `METHOD:REQUEST` for the next configured slot; booked on acceptance. | same |
| Inbox | `ImapInboxProvider` (`receive_email`, polled by the worker): DSN bounces, iMIP accept/decline, replies matched by `In-Reply-To`/`References`/sender, auto-mail filtered (OOO kept). Parser tested on real message formats; send → reply → accept round trip tested. | `app/integrations/email_imap.py`, `services/autonomy.poll_inboxes` |
| BillionMail | HTTP adapter (`/api/batch_mail/api/send`, `x-api-key`), mock-tested. | `app/integrations/billionmail.py` |
| Compliance fix | Minimum gap between touches is waived when the prospect replied since our last touch (found when the meeting invite after a positive reply was blocked). | `compliance/rules.py` |
| Pipeline fix | A successful send moves the lead to `contacted` (stage follows the real send, not the proposal). | `execution/executor.py` |
| Dashboard | React + TypeScript (Vite), six views, live SSE feed, approvals with editing, evidence and compliance; served by FastAPI from `frontend/dist`. Read models: `/overview`, `/prospects`, `/activity`, `/decisions`, `/stream`. | `frontend/`, `app/api/routes/dashboard.py` |
| Demo | `demo` profile with a labelled scripted playbook vendor (no key needed) that goes through the real controller, validation, guardrails and approvals; four SAMPLE scenarios; reply simulator via the real inbound pipeline; endpoints hidden outside dev/demo/test. | `app/llm/demo_client.py`, `app/services/demo.py`, `config/gtm.demo.yaml` |
| One-command start | `python start.py`: service checks, migrations, dashboard build, API + worker, browser; port check; clean shutdown on Ctrl+C/SIGTERM even with live connections open. | `start.py` |

Verified live: all four scenarios end to end through the dashboard with the worker running (edit + approve,
reject → redraft, positive reply → meeting invite → accept → booked → hand-off; opt-out → suppressed → stop;
missing data → escalation); data persisted across launcher restarts. Full suite **391 passed, 0 skipped**.

Not done: AutoClip (deferred), Google Calendar/Calendly APIs, authentication, quiet hours, live runs with real
credentials.

