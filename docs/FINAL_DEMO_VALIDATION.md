# Final Demo Validation

Branch `feature/oxygen-autonomous-pipeline`, 2026-11-26. All evidence below comes from runs on this machine in demo mode: local database, simulated providers, no API keys. **No email, calendar invite or API call left the machine.**

## Results

| Check | Result | Evidence |
|---|---|---|
| Automated test suite | **439 passed** | `python -m pytest` (separate `gtm_test` database) |
| Demo-framework tests | **12 passed** | `tests/test_demo_framework.py`: all 7 scenarios through the real API and workflow engine, metrics vs database, tenant isolation (API and PostgreSQL RLS), role permissions, campaign controls, ICP engine, follow-up deduplication, demo-mode guard, integrations page, reply withdraws queued outreach |
| Live process run (SMTP) | **37 / 37 checks** | `scripts/e2e/run_e2e.py`: real API plus 2 workers, SIGKILL restart, real SMTP to a local sink, demo clock, no duplicate sends, every send approved |
| Browser, full flow | **7 / 7 scenarios completed** from the Demo Control Centre buttons | Fresh database via `python start.py --profile demo`; `demo_scenario_completed` events for all seven |
| Browser, every page | 18 / 18 render with no error banner and no console errors | Desktop and 375 px phone width; no horizontal page scroll on any page |
| Roles in the browser | Operator sees "can review but not approve"; the backend answered the operator's approve call with **403** | Request log |
| Isolation in the browser | Helix admin sees 1 campaign and 4 accounts, **0** of Northstar's 34 contacts, 10 sends and 1 meeting | API responses under the Helix session |
| Frontend build | `tsc` clean; production build succeeds | — |

## The 12 stages, verified

| # | Stage | Verified by |
|---|---|---|
| 1 | Target accounts: manual, CSV, priorities, selection, ICP | `TestCampaignControls`, Target Accounts page |
| 2 | Lead discovery via `demo_apollo` (same interface as Apollo) | Start pipeline: 12 accounts → 34 contacts |
| 3 | Contacts with verification status and source | Contacts page; ingestion tests |
| 4 | ICP qualification: explained, recalculable | `TestIcpEngine`; preview + recalculate test (widened ICP qualifies more) |
| 5 | Research signals, each labelled simulated with source and "why it matters" | Research page; 219 signals in the browser run |
| 6 | Drafts: generate, regenerate (new version), edit, submit; explanation and evidence | `test_draft_regenerate_edit_submit_and_reject_returns_for_redraft` |
| 7 | Approval: approve, edit (diff shown), reject → redraft; payload hash lock | Approval tests; reviewer = signed-in user |
| 8 | Follow-ups: scheduled once per step, due via the demo clock, cancelled on reply, postponed on out-of-office | `no_reply` scenario; `TestFollowUpSequence` |
| 9 | Reply matching and classification (7 trigger types) | Scenarios; inbound tests |
| 10 | Grounded answers for questions and objections; unsupported question escalated | `objection`, `question`, `unsupported_question` scenarios; `TestResponseIntelligenceGate` |
| 11 | Meetings: proposal → approval → simulated invite with slots → accept / decline / reschedule | `successful_conversion`, `meeting_request` scenarios |
| 12 | Dashboard and RevOps from records | Metrics test compares every KPI with direct SQL counts |

## Defects found and fixed during this build

- The orchestrator rejected grounded reply drafts as "not a completed draft", so answers to questions were never proposed.
- The 2-day send cooldown blocked answering a prospect who had just replied. It is now waived after a reply; count limits still apply.
- A follow-up email queued for approval stayed queued after the prospect replied. It is now withdrawn automatically and recorded as `system (prospect replied)`.
- Two workers starting at once produced a reactions-cursor race and an attribution race; earlier session.
- Follow-up "run now" didn't persist before its query (no autoflush). Demo providers logged `mode: live`. Metrics `GROUP BY` failed on bound parameters.
- An opted-out lead stayed "replied". It now moves to `closed_lost`.
- Demo-tenant writes defaulted to the default organisation, which RLS would reject. Every default now uses the request's or job's tenant.

## Known limits (honest)

- **AI is scripted in demo mode.** It is deterministic and keyword-based (for example, an unanswerable question is classified as a "question" and escalated at drafting). Real OpenAI and Anthropic adapters exist and are wire-tested, but have not run with live keys.
- **Not built yet:**
  - Live Google Calendar and Calendly adapters ("Integration Pending"; real invitations go out via SMTP iCalendar today).
  - Quiet hours.
  - Scheduled automatic discovery (discovery runs when a campaign starts or on request).
- **Demo clock:** advancing it shifts the organisation's timestamps, so trend charts show those events earlier in time.
- **Demo resets are global:** "Load" and "Reset" act on both demo organisations, by design for presentations. They are admin-only and available only in demo/development/test.
- **Scenarios and roles:** the approver role cannot run scenarios (an operational action). An operator can, but the run stops at the approval checkpoint.
- **Inbound webhooks are single-tenant:** they write to the default organisation.
