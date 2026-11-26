# Adding the Live Integrations

The workflow engine only talks to **capabilities** (`find_leads`, `research_prospect`, `draft_outreach`, `draft_reply`, `send_email`, `book_meeting`, `receive_email`, ...). Providers are chosen from configuration by priority, availability (credentials present), health and rate limits. Going live means adding credentials (and, for two integrations, an adapter). It does not change business logic.

## Switching a profile to live

1. Run with `GTM_PROFILE=development` (live providers plus mocks for what is not configured) or `production` (no mocks at all).
2. Set the environment variables below on the server (never in config files or the browser).
3. Restart. **Settings → Integrations** shows *Live Ready* for each configured integration and *Live Not Configured* (with the missing variables) for the rest.
4. Set `OXYGEN_API_KEYS` and a real `SECRET_KEY` (outside development/test the API refuses requests without them), and create users and roles under **Organisation & Users**.

| Integration | Variables | Adapter | Status |
|---|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `app/llm/openai_client.py` | Built; wire-tested with the real SDK; not yet run live |
| Anthropic | `ANTHROPIC_API_KEY` | `app/llm/anthropic_client.py` | Built; wire-tested with the real SDK; not yet run live |
| Apollo | `APOLLO_API_KEY` | `app/integrations/apollo.py` | Built; tested against a mock transport |
| SMTP | `SMTP_HOST`, `SMTP_FROM_ADDRESS` (+ `SMTP_USERNAME`, `SMTP_PASSWORD`) | `app/integrations/email_smtp.py` | Built; tested end to end against a local SMTP server, including AUTH |
| IMAP | `IMAP_HOST`, `IMAP_USERNAME`, `IMAP_PASSWORD` | `app/integrations/email_imap.py` | Built; parser and pipeline tested; mailbox login untested |
| BillionMail | `BILLIONMAIL_URL`, `BILLIONMAIL_API_KEY`, `BILLIONMAIL_ADDRESSER` | `app/integrations/billionmail.py` | Built; mock-tested |
| Google Calendar | — | **to build** | Interface defined by `DemoCalendarProvider` |
| Calendly | — | **to build** | Interface defined by `DemoCalendarProvider` |

Model choices per role are in `config/gtm.yaml` under `llm.profiles` (planner, researcher, writer, with fallbacks).

## Building the Google Calendar or Calendly adapter

Follow `DemoCalendarProvider` (`app/integrations/demo_providers.py`):

```python
class GoogleCalendarProvider(ExecutionProvider):
    provider_name = "google_calendar"; supported_task_types = ["book_meeting"]
    @classmethod
    def from_config(cls, pc, credentials): ...   # OAuth service account / refresh token from env
    async def execute(self, context, session):
        # 1. find free slots on the rep's calendar (freebusy)
        # 2. create the event with the prospect as attendee (sendUpdates=all)
        # 3. call app.pipeline.meetings.invite_meeting(...) with the real slots and event id as uid
        # 4. return ExecutionResult(success=True, result={"meeting_uid": ..., "interaction_recorded": True})
```

Attendee responses arrive through `process_calendar_event` (a webhook or a calendar watch channel), which already updates the meeting record and lead stage. Register it in `config/gtm.yaml` with `credentials: {...}` and `capabilities: [book_meeting]`, then add `live_provider` to its entry in `app/pipeline/integrations.py`.

## Go-live checklist

- [ ] One integration at a time, sending only to internal test addresses first.
- [ ] SPF, DKIM and DMARC for the sending domain; a reply-to mailbox that IMAP reads.
- [ ] `OXYGEN_API_KEYS` or user keys, a long random `SECRET_KEY`, and `ENVIRONMENT=production`.
- [ ] Webhook signing secret configured (unsigned webhooks are refused in production).
- [ ] Research egress through a proxy that blocks private address ranges.
- [ ] Review knowledge-base answers (`app/pipeline/demo_data.py` `KNOWLEDGE_BASE` or campaign `value_points` entries) before replies are drafted for real prospects.
- [ ] Quiet hours / recipient time zones (not implemented yet).
