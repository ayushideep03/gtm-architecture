# Oxygen — End-to-End Functional Validation Report

Branch `feature/oxygen-autonomous-pipeline` · validated 2026-11-26 · nothing pushed.

## How it was tested

| Method | What it proves |
|---|---|
| **Live run** (`scripts/e2e/run_e2e.py`): real API (uvicorn) + **two** real workers on an isolated `gtm_e2e` database, real SMTP providers pointed at a local SMTP sink on 127.0.0.1, API-key authentication on, scripted demo AI (no LLM key on this machine) | The whole pipeline works as running processes, through HTTP, with concurrency, a SIGKILL restart of both workers, and real SMTP traffic. Nothing leaves the machine. |
| **427 automated tests** (separate `gtm_test` database) | Unit, integration and API behaviour, including 36 new regression tests for every defect fixed here. 18 of them fail on the previous code. |
| **Browser** (dashboard against an isolated `gtm_ui_check` database, sign-in on, no email provider configured) | Every page, sign-in/out, edit-approve, reject, reply simulation, error/empty states, 375 px phone layout. |
| **Independent security review** (separate reviewer, read-only) | Authentication, tenant isolation, SSRF, injection, secret exposure. |

Final live result: **36/36 stage checks pass**.

## Pipeline stages: what actually works

| # | Stage | Result | Evidence (live run unless noted) |
|---|---|---|---|
| 1 | Target account selection | **Partial** | The ICP is configuration (`oxygen.campaign`) used by qualification. There is no autonomous account-selection step: discovery is triggered by API, CSV import or website form, not on a schedule. |
| 2 | Lead discovery | Works (mock source) | `POST /leads/ingest` via `find_leads` (mock Apollo) created 3 leads; 4 sample scenarios seeded. Real Apollo is request-shape tested only. |
| 3 | Contact identification | Works | 7 leads → 7 people + 7 companies; dedup and identity-resolution tests pass. |
| 4 | Qualification | Works | Score 81, `qualified`; two off-ICP leads correctly `disqualified` → Oxygen `stop`. |
| 5 | Research & intent | Works (mock research) | Findings: intent assessment, intent signal, news. Website research is disabled in the live run (it would fetch real sites). |
| 6 | Personalised message | Works | Draft cites the company's own evidence ("Northwind Analytics … Germany and the Nordics"); nothing sent before approval. |
| 7 | Email sending | Works | After approval, a real SMTP delivery to the prospect only, with a deterministic Message-ID and List-Unsubscribe; lead → `contacted`. |
| 8 | Follow-up scheduling | Works **after fix** | Re-evaluation scheduled 72 h after the send; after simulated time passed with no reply, a follow-up draft was proposed. |
| 9 | Reply detection | Works | Reply matched to the sent email by `In-Reply-To`; a duplicate webhook delivery is ignored. IMAP parsing is tested with real RFC 822/DSN/iMIP messages; the IMAP network fetch has not run against a live mailbox. |
| 10 | Classification & response | Works / **partial** | `meeting_request` → meeting proposal → approval → iCalendar invite over SMTP. Opt-out → suppression → stop. **Questions and objections are escalated to a human; Oxygen does not draft a written answer.** |
| 11 | CRM updates | Works | Stages follow real events: qualified → contacted → meeting_booked; interactions, decisions and evidence stored. Note: an opted-out lead stays in stage `replied` (suppressed) rather than moving to a closed stage. |
| 12 | Analytics & dashboard | Works | Overview KPIs match the database; RevOps attributes touches (reply rate 0.4, meeting rate 0.2) and the funnel matches the pipeline. Demo sample history is included in the analytics (labelled `sample`). |

## Autonomous execution (Phase 3)

| Area | Verdict |
|---|---|
| Orchestration (events → work items → Oxygen cycle → tasks) | Works; decision sequences per prospect are clean (no loops). |
| Redis state (pause switch, provider health, rate limits) | Shared across workers; falls back to memory if Redis is down. Tested. |
| Retries, recovery, leases | Work **after fix**: the task claim is now committed before the provider call, so a crash leaves a leased `in_progress` task that is flagged for review, never re-sent. |
| Idempotency and duplicate prevention | Live: 2 workers plus a SIGKILL restart → 4 emails, 4 unique Message-IDs, sends = completed tasks, at most one open work item per lead, every send approved. |
| Scheduling | Works **after fix**: wake-ups and "wait 72h" re-checks that arrive while an item is being processed are no longer lost. |
| Human approval | Hash-locked content, re-checked compliance at send time, reviewer = authenticated principal. |

Caveat: the live SIGKILL is not guaranteed to land *during* an SMTP call. The crash-mid-send case is proven by a deterministic test, `TestCrashMidSend`.

## Integrations (Phase 4)

| Integration | Classification | Evidence |
|---|---|---|
| SMTP send + iCalendar invites | **Implemented; tested end to end against a local SMTP server** (incl. AUTH) | Not yet run against a production mail provider (TLS and deliverability unverified). |
| IMAP inbox | **Implemented, partially tested** | Parser and pipeline tested; IMAP login/fetch untested against a server. |
| OpenAI | **Implemented, untested live** | Real `openai` SDK with a fake transport: request shape, parsing, refusal, truncation; all schemas strict-mode compatible. Model `gpt-4.1` and live acceptance unverified. |
| Anthropic | **Implemented, untested live** | Real `anthropic` SDK with a fake transport: `output_config` structured output, refusal-fallback beta header, refusal, truncation. |
| Apollo | **Implemented, mock-tested only** | Request shape and rate-limit handling against a mock transport. |
| BillionMail | **Implemented, mock-tested only** | Request shape and auth failure against a mock transport. |
| Mock providers (`mock_apollo`, `mock_enrichment`, research, drafting, send, meetings) | **Mocked**: development/test only | Loaded only by `providers/mocks.yaml` in the dev/test/demo profiles. |
| Scripted demo AI | **Placeholder** (labelled everywhere) | Keyword-based; e.g. "send a couple of times" is classified as a question. |
| AutoClip, Google Calendar/Calendly API, LinkedIn/phone automation | **Not implemented** | LinkedIn/phone become rep tasks by design. |

## Defects found and fixed

| Severity | Defect | Fix | Test |
|---|---|---|---|
| Critical | SMTP, SMTP-meetings and IMAP providers could **never** be enabled from config: `host`/`from_address` were read from settings, not the env credentials. Unit tests built the providers directly, so they missed it. | Read merged settings + credentials; SMTP login optional but all-or-nothing. | `TestEmailProvidersFromConfig` |
| Critical | A crash during an external call rolled the task back to `pending` → **the email would be sent twice**; stale-task recovery never saw a lease. | Commit the `in_progress` claim and lease before calling the provider. | `TestCrashMidSend` |
| High | Oxygen's own "wait 72h" re-check, and any event arriving mid-evaluation, merged into the item being processed and was **lost** when it completed: follow-ups never happened autonomously. | Keep the earliest requested time; leave the item open; `stop` still closes it. | `TestWorkQueueRerun`, `TestWorkQueueScheduledRecheck` |
| High | Two workers: outcome attribution crashed the tick (unique violation); the reactions cursor row raced on an empty database. | Advisory lock; `INSERT … ON CONFLICT DO NOTHING`. | `TestConcurrentWorkers`, `TestReactionCursorRace` |
| Medium | One prospect's failing cycle aborted the whole tick. | Per-item isolation; the failed item retries after its lease. | `test_one_failing_cycle_does_not_stop_the_tick` |
| Critical (security) | **No authentication**: anyone reaching the port could approve and send email, lift suppressions or retry ambiguous sends; the reviewer was free text. | API keys (header or HttpOnly SameSite=Strict cookie); fail closed (503) outside local profiles; reviewer = principal; dashboard sign-in. | `TestApiAuthentication` |
| High (security) | Unset `ENVIRONMENT` meant development: unsigned webhooks, demo routes, CORS `*` with credentials. | Default `production`; CORS from `CORS_ORIGINS` only; `/docs` off in production. | — |
| High (security) | SSRF: research could fetch `169.254.169.254`, `localhost`, private ranges, or follow redirects there; unbounded body read. | Public-hostname/public-IP check, manual same-site redirects re-checked per hop, streamed byte cap. | `TestResearchSsrfGuard` |
| Medium (security) | iCalendar property injection via meeting topic/agenda. | RFC 5545 escaping. | `TestIcsEscaping` |
| Low (security) | Health endpoints returned DB/Redis exception text; unbounded CSV/list sizes. | Exception type only; size and limit caps. | — |
| Medium (UI) | Approval evidence showed raw ids; Approval Centre unusable on phones; tables widened pages to 460–729 px at 375 px; an unreachable API showed "Local mode: no sign-in". | Resolved descriptions; responsive layout; scrolling tables; retry screen. | Verified in browser |

## Remaining before a real-world deployment

1. **Tenant isolation at the API.** Data carries `tenant_id`, but API keys are not bound to a tenant and queries do not filter by it. Run one instance per organisation until this exists.
2. **Roles.** Any key can approve sends and lift suppressions.
3. **Live credentials, one at a time, to internal addresses only:** OpenAI/Anthropic (confirm the model names), a real mailbox (`SMTP_*`, `IMAP_*`, TLS, SPF/DKIM/DMARC), Apollo (small search).
4. **Webhook replay:** signatures have a time window but no nonce; replays inside the window are caught only by `event_id` de-duplication.
5. **SSRF residual:** DNS rebinding between the check and the connection; put research egress behind a proxy that blocks private ranges.
6. **Not implemented:** scheduled lead discovery, written replies to prospect questions, quiet hours and recipient time zones, a closed stage for opted-out leads, AutoClip.
7. **Operations:** attribution scans all outbound touches on every tick (fine now, slow at scale); `index.html` is served without no-cache headers (users need a reload after a deploy); PII from unmatched inbound mail is stored in events.
8. **Analytics** include labelled demo sample history; use a clean database for real reporting.
