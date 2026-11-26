# Final Demo Validation

Branch `feature/oxygen-autonomous-pipeline`, management-readiness review of 2026-11-26. All evidence comes from runs on this machine in demo mode: local database, simulated providers, no API keys. **No email, calendar invite or API call left the machine.**

## Results

| Check | Result | How |
|---|---|---|
| Automated test suite | **442 passed** | `python -m pytest` (separate `gtm_test` database) |
| Demo-framework tests | **15 passed** | `tests/test_demo_framework.py`. Covers all 7 scenarios through the real API and workflow engine, metrics vs SQL, tenant isolation (API and PostgreSQL RLS), roles, campaign lifecycle, ICP engine, follow-up deduplication, demo-mode guard, integrations page, reply withdrawing queued outreach, and the three readiness findings below. |
| Live process run | **37 / 37 checks** | `python scripts/e2e/run_e2e.py`: real API and 2 workers, SIGKILL restart, real SMTP to a local sink, demo clock, no duplicate sends, every send approved |
| Management sequence, browser, fresh environment | **15 / 15 steps** | `python start.py --profile demo` on an empty database; the steps in DEMO_PRESENTATION_GUIDE.md were clicked through by hand |
| Dashboard vs database | **11 / 11 KPIs equal to SQL counts** | 12 accounts, 34 leads, 30 qualified, 4 disqualified, 219 signals, 30 drafts, 29 pending, 1 sent, 1 reply, 1 meeting booked, 0 opt-outs (at step 15) |
| Extra scenarios in the same environment | Objection, question, no reply, unsubscribe: **all completed** | Demo Control Centre buttons; `demo_scenario_completed` events |
| Earlier browser run | 7 / 7 scenarios completed | Fresh environment |
| Pages | 18 / 18 with no error banner or console error; **no horizontal scroll at 375 px** | Desktop and phone width, after the final changes |
| Roles | Operator sees "can review but not approve"; backend answered the operator's approve call **403** | Browser and request log |
| Isolation | Helix admin sees its 1 campaign and 4 accounts and **none** of Northstar's data | Browser session |
| Frontend | TypeScript typecheck clean; production build succeeds | No ESLint is configured in this project; `tsc --noEmit` is the static check |
| Python lint | No errors in changed files (ruff E9/F) | — |

## Readiness findings and their resolution

| Finding | Resolution | Proof |
|---|---|---|
| **Run Now** must persist the new due time before the due query | `db.flush()` before `process_due` (the session has autoflush off). Making it due and flagging it happen in the same transaction; a second Run Now is refused (409). | `test_run_now_makes_follow_up_due_immediately`, which fails when the flush is removed |
| Contradictory **Recalculate** note | Now reads: "Only leads not yet contacted are re-scored; contacted leads keep their stage and score." | `test_recalculate_note_is_consistent` |
| Scripted AI **intent assessments** must say DEMO everywhere | Each AI-written finding (intent, AI research, AI qualification) now **stores** its provenance: `llm.vendor/model`, `demo: true`, and source "… (scripted demo AI)". The Research API, the Research page (list and detail), the prospect drawer's research tab and the Approval Centre's evidence list all show DEMO from that stored flag. | `test_scripted_intent_assessments_are_labelled_demo_everywhere`; seen in the browser |

## Other issues found and fixed in this review

- **Demo providers looked live:** the Workflow Engine labelled only `mock` providers, so `demo_*` providers appeared unlabelled. All of them now show DEMO.
- **Approval Centre wording:** it now states that approved sends and invitations are simulated in demo mode, on the page and in the toast.
- **Subject lines:** demo email subjects were cut mid-word ("…instead of h"). They are now complete; the variants were reworded.
- **Funnel:** RevOps showed "enriched 0" for demo companies whose firmographics come from the dataset. They are now recorded as enriched by "demo dataset (simulated firmographics)".
- **Live updates:**
  - The event stream could skip events committed late by the worker. It now re-reads a sliding window and deduplicates by id.
  - Screens now refresh on reconnect and every 20 s as a safety net.

## What is real and what is simulated

**Real in demo mode** (the same code runs live): workflow engine, decisions and guardrails, approvals with payload lock, compliance and suppression, follow-ups, meetings lifecycle, metrics, RLS tenancy, roles, ICP scoring, and the response-intelligence knowledge-base gate.

**Simulated:** lead discovery data, research signals, AI reasoning and text (scripted), email delivery, inbox replies (presenter-triggered), and calendar invitations and responses. Each is labelled DEMO.

## Known limits

- **Scripted AI:** it is deterministic and keyword-based. An unanswerable question is classified as a question and escalated at drafting. OpenAI and Anthropic adapters exist and are wire-tested, but have not run live.
- **Not built:**
  - Google Calendar and Calendly live adapters (real invitations go out via SMTP iCalendar today).
  - Quiet hours.
  - Scheduled automatic discovery (discovery runs when a campaign starts or on request).
- **Demo clock:** advancing it shifts the organisation's timestamps, so "3d ago" labels and the trend chart move earlier.
- **Demo controls:** Reset and Load act on both demo organisations, by design for presentations. They are admin-only and need a demo-capable profile.
- **Scenario roles:** only admin presenters get auto-approval. An operator's run stops at the approval checkpoint, and approvers cannot run scenarios.
- **Webhooks:** inbound webhooks write to the default organisation.
