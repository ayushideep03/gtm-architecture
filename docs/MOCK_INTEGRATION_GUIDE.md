# Mock / Demo Integration Guide

Every external integration has a **demo provider** that implements the same interface as its live counterpart. The demo profile (`GTM_PROFILE=demo`, chosen automatically by `python start.py` when no LLM key is set) uses only demo providers and disables the live ones.

| Integration | Interface | Demo provider | What it does in demo mode | Live provider |
|---|---|---|---|---|
| Apollo (lead discovery) | `LeadSourceProvider.search_leads(criteria)` | `DemoApolloProvider` (`demo_apollo`) | Returns deterministic fictional contacts for a company domain (2–3 per company, with title, department, seniority, location and verification status). Unknown domains get generated contacts. | `ApolloProvider` (`apollo`) |
| OpenAI / Anthropic | `JSONCompletionClient.complete_json(...)` through `LLMRouter` | `ScriptedDemoClient` (vendor `demo`) | Answers planner, research, intent, qualification, drafting, reply classification and reply drafting with a transparent scripted playbook, labelled `scripted-demo-playbook`. | `OpenAIJSONClient`, `AnthropicJSONClient` |
| SMTP (sending) | `ExecutionProvider` for `send_email` | `MockOutreachSendProvider` (`mock_outreach_send`, `dry_run`) | Records the outbound email with a simulated Message-ID; sends nothing. | `SmtpEmailProvider` (`smtp_email`), `BillionMailProvider` |
| IMAP (inbox) | `receive_email` provider with `poll(session)` | `DemoInboxProvider` (`demo_inbox`) | `poll` reads nothing. `inject` delivers a presenter-triggered reply through the **real** inbound pipeline: matching, classification, suppression, follow-up cancellation. | `ImapInboxProvider` (`imap_inbox`) |
| Google Calendar | `ExecutionProvider` for `book_meeting` | `DemoCalendarProvider` (`demo_google_calendar`) | Proposes the next 3 free slots from demo availability and stores a simulated invitation (subject, body, iCalendar preview). | Not built yet (`smtp_meetings` sends real iCalendar invitations today) |
| Calendly | same | `DemoCalendarProvider` (`demo_calendly`, flavour `calendly`) | Same as above. | Not built yet |
| Research | `ExecutionProvider` for `research_prospect` | `DemoResearchProvider` (`demo_research`) | Simulated signals: news, hiring, technology, expansion, funding, digital transformation, challenge. Each has a source, a "why it matters" and `demo: true`. | `LLMResearchProvider` (`llm_research`: the company's own website and an LLM) |
| Qualification | `qualify_lead` | `icp_rules` | Deterministic ICP scoring. Not a mock: it is the production qualifier in every profile. | (same) |
| Response intelligence | `draft_reply` | `response_intelligence` with the demo LLM | Knowledge-base gate plus scripted phrasing. | Same provider with OpenAI/Anthropic phrasing |

## Labelling rules (never present mock output as live)

- **Data:** demo companies use reserved `.example` domains, and all people are fictional. Company descriptions and contact source records say *DEMO DATA — fictional*.
- **Research:** every simulated signal carries `demo: true` and a "simulated" source. The Research page shows a DEMO tag on each one and a banner.
- **Sends:** simulated sends are recorded with `mode: simulation` / `dry_run`. Calendar invitations are labelled *SIMULATED Google Calendar / Calendly invitation*.
- **AI:** scripted AI output is labelled `scripted-demo-playbook`, and the dashboard shows a demo-mode banner.
- **Integrations page:** status is **Demo Active** for demo providers. Connection tests in demo mode say *Simulated call … no external service was contacted*.

## Adding a demo provider

1. Implement the capability's interface (`ExecutionProvider` with `provider_name`, `supported_task_types`, `execute(context, session)`; or `LeadSourceProvider` for lead sources).
2. Register it in `config/gtm.demo.yaml` with `tags: [demo]` (required for external actions in demo mode) and a priority.
3. Add it to `CATALOGUE` in `app/pipeline/integrations.py` so the Integrations page shows it.

## Running without any keys

```bash
python start.py
```

This picks the demo profile when no `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set. Postgres and Redis must be running locally (see README).
