"""Phase G — real email/meeting protocols: SMTP send (against a local SMTP server), iCalendar
invitations, IMAP message parsing, inbox polling and the BillionMail HTTP adapter."""

import json
import uuid
from datetime import datetime, timezone
from email import message_from_bytes
from email.message import EmailMessage

import httpx
import pytest
from sqlalchemy import select

from app.core.gtm_config import ProviderConfig
from app.execution.models import ExecutionContext
from app.integrations.billionmail import BillionMailProvider
from app.integrations.email_imap import ImapInboxProvider, parse_message
from app.integrations.email_smtp import SmtpEmailProvider, SmtpMeetingProvider, next_slot
from tests.test_phaseA_memory import _prospect, db  # noqa: F401


@pytest.fixture
def smtp_server():
    from aiosmtpd.controller import Controller

    class Sink:
        def __init__(self):
            self.messages = []

        async def handle_DATA(self, server, session, envelope):
            self.messages.append((envelope.rcpt_tos, envelope.content))
            return "250 OK"

    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    sink = Sink()
    sink.port = port
    ctl = Controller(sink, hostname="127.0.0.1", port=port)
    ctl.start()
    yield sink
    ctl.stop()


async def _task(db, lead, task_type="send_email", payload=None):
    from app.models.base import Task
    t = Task(lead_id=lead.id, task_type=task_type, status="in_progress", payload=json.dumps(payload or {}))
    db.add(t)
    await db.flush()
    return t


def _smtp(cls, port, **kw):
    return cls(host="127.0.0.1", port=port, security="none", from_address="alex@oxygen.test",
               from_name="Alex Morgan", **kw)


class TestSmtp:
    @pytest.mark.anyio
    async def test_sends_to_prospect_with_stable_message_id(self, db, smtp_server):
        from app.models.base import Interaction
        _, person, lead = await _prospect(db)
        task = await _task(db, lead)
        ctx = ExecutionContext(task_id=task.id, task_type="send_email", lead_id=lead.id,
                               payload={"subject": "Quick question", "body": "Hi Dana"})
        res = await _smtp(SmtpEmailProvider, smtp_server.port).execute(ctx, db)
        assert res.success and res.result["sent"] is True
        rcpt, raw = smtp_server.messages[0]
        msg = message_from_bytes(raw)
        assert rcpt == [person.email] and msg["Message-ID"] == f"<oxygen-{task.id}@oxygen.test>"
        assert msg["Subject"] == "Quick question" and "unsubscribe" in msg["List-Unsubscribe"]
        row = (await db.execute(select(Interaction).where(Interaction.task_id == task.id))).scalar_one()
        assert row.channel_message_id == msg["Message-ID"] and row.direction == "outbound"

    @pytest.mark.anyio
    async def test_refuses_other_recipient_and_missing_content(self, db, smtp_server):
        _, _, lead = await _prospect(db)
        task = await _task(db, lead)
        p = _smtp(SmtpEmailProvider, smtp_server.port)
        bad = await p.execute(ExecutionContext(task_id=task.id, task_type="send_email", lead_id=lead.id, payload={
            "recipient_email": "someone@else.com", "subject": "s", "body": "b"}), db)
        empty = await p.execute(ExecutionContext(task_id=task.id, task_type="send_email", lead_id=lead.id,
                                                 payload={"subject": "s"}), db)
        assert (bad.error_code, empty.error_code) == ("RECIPIENT_MISMATCH", "MISSING_CONTENT")
        assert smtp_server.messages == []

    @pytest.mark.anyio
    async def test_dry_run_does_not_connect(self, db):
        _, _, lead = await _prospect(db)
        task = await _task(db, lead)
        p = SmtpEmailProvider(host="unreachable.invalid", from_address="a@oxygen.test", mode="dry_run")
        res = await p.execute(ExecutionContext(task_id=task.id, task_type="send_email", lead_id=lead.id,
                                               payload={"subject": "s", "body": "b"}), db)
        assert res.success and res.result["sent"] is False

    @pytest.mark.anyio
    async def test_meeting_invitation_is_icalendar_request(self, db, smtp_server):
        _, person, lead = await _prospect(db)
        task = await _task(db, lead, "book_meeting")
        p = _smtp(SmtpMeetingProvider, smtp_server.port,
                  availability={"timezone": "UTC", "weekdays": [0, 1, 2, 3, 4], "hours": [10], "min_notice_hours": 24})
        res = await p.execute(ExecutionContext(task_id=task.id, task_type="book_meeting", lead_id=lead.id,
                                               payload={"topic": "Intro call"}), db)
        assert res.success and res.result["meeting_uid"] == f"oxygen-{task.id}@oxygen.test"
        msg = message_from_bytes(smtp_server.messages[0][1])
        cal = [p for p in msg.walk() if p.get_content_type() == "text/calendar"][0]
        ics = cal.get_payload(decode=True).decode()
        assert "METHOD:REQUEST" in ics and f"mailto:{person.email}" in ics and "SUMMARY:Intro call" in ics

    def test_next_slot_respects_weekdays_and_notice(self):
        friday = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)   # Friday
        slot = next_slot({"timezone": "UTC", "weekdays": [0, 1, 2, 3, 4], "hours": [10, 14],
                          "min_notice_hours": 24}, now=friday)
        assert slot == datetime(2026, 11, 30, 10, 0, tzinfo=timezone.utc)  # skips the weekend

    def test_unconfigured_provider_is_unavailable(self):
        from app.providers.loader import build_provider
        pc = ProviderConfig(name="smtp_email", factory="app.integrations.email_smtp:SmtpEmailProvider",
                            capabilities=["send_email"], credentials={"host": "OXY_TEST_NO_SUCH_HOST_VAR"})
        instance, reason = build_provider(pc)
        assert instance is None and "OXY_TEST_NO_SUCH_HOST_VAR" in reason


def _mail(**headers) -> EmailMessage:
    m = EmailMessage()
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    return m


class TestParsing:
    def test_reply(self):
        m = _mail(From="Dana <dana@acme.io>", To="alex@oxygen.test", Subject="Re: Quick question",
                  In_Reply_To="<oxygen-1@oxygen.test>", Message_ID="<r1@acme.io>")
        m.set_content("Sounds interesting, send times.\n\n> quoted original")
        [ev] = parse_message(m.as_bytes())
        assert (ev.type, ev.in_reply_to, ev.from_email, ev.event_id) == ("reply", "<oxygen-1@oxygen.test>",
                                                                          "dana@acme.io", "<r1@acme.io>")
        assert "Sounds interesting" in ev.body

    def test_hard_bounce(self):
        raw = (b"From: MAILER-DAEMON@mx.test\r\nTo: alex@oxygen.test\r\nSubject: Undelivered\r\n"
               b"Message-ID: <dsn1@mx.test>\r\nMIME-Version: 1.0\r\n"
               b"Content-Type: multipart/report; report-type=delivery-status; boundary=B\r\n\r\n"
               b"--B\r\nContent-Type: text/plain\r\n\r\nFailed.\r\n"
               b"--B\r\nContent-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; mx.test\r\n\r\n"
               b"Final-Recipient: rfc822; dana@acme.io\r\nAction: failed\r\nStatus: 5.1.1\r\n"
               b"--B\r\nContent-Type: text/rfc822-headers\r\n\r\nMessage-ID: <oxygen-9@oxygen.test>\r\n"
               b"--B--\r\n")
        [ev] = parse_message(raw)
        assert (ev.type, ev.bounce_type, ev.message_id) == ("bounce", "hard", "<oxygen-9@oxygen.test>")

    def test_calendar_accept_and_auto_mail(self):
        m = _mail(From="dana@acme.io", To="alex@oxygen.test", Subject="Accepted: Intro call", Message_ID="<c1@acme.io>")
        m.set_content("Accepted")
        m.add_alternative("BEGIN:VCALENDAR\r\nMETHOD:REPLY\r\nBEGIN:VEVENT\r\nUID:oxygen-5@oxygen.test\r\n"
                          "ATTENDEE;PARTSTAT=ACCEPTED:mailto:dana@acme.io\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
                          subtype="calendar", params={"method": "REPLY"})
        [ev] = parse_message(m.as_bytes())
        assert (ev.type, ev.meeting_id, ev.attendee_email) == ("booked", "oxygen-5@oxygen.test", "dana@acme.io")
        auto = _mail(From="noreply@x.io", Subject="Your receipt", Auto_Submitted="auto-generated")
        auto.set_content("x")
        ooo = _mail(From="dana@acme.io", Subject="Automatic reply: away", Auto_Submitted="auto-replied")
        ooo.set_content("I am out of office until Monday")
        assert parse_message(auto.as_bytes()) == [] and parse_message(ooo.as_bytes())[0].type == "reply"


class TestRoundTrip:
    @pytest.mark.anyio
    async def test_send_then_reply_and_accept_via_inbox(self, db, smtp_server, monkeypatch):
        """SMTP send → prospect replies (matched by Message-ID) → accepts invitation → lead advances."""
        from app.models.base import Lead
        _, person, lead = await _prospect(db)
        lead.status = "contacted"
        task = await _task(db, lead)
        await _smtp(SmtpEmailProvider, smtp_server.port).execute(ExecutionContext(
            task_id=task.id, task_type="send_email", lead_id=lead.id, payload={"subject": "Hi", "body": "Hello"}), db)
        await db.commit()
        sent_id = message_from_bytes(smtp_server.messages[0][1])["Message-ID"]

        reply = _mail(From=person.email, To="alex@oxygen.test", Subject="Re: Hi", In_Reply_To=sent_id,
                      Message_ID=f"<{uuid.uuid4().hex}@acme.io>")
        reply.set_content("Yes, let's talk next week.")
        accept = _mail(From=person.email, To="alex@oxygen.test", Subject="Accepted", Message_ID=f"<{uuid.uuid4().hex}@a>")
        accept.set_content("ok")
        accept.add_alternative(f"BEGIN:VCALENDAR\r\nMETHOD:REPLY\r\nBEGIN:VEVENT\r\nUID:oxygen-{task.id}@oxygen.test\r\n"
                               f"ATTENDEE;PARTSTAT=ACCEPTED:mailto:{person.email}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
                               subtype="calendar", params={"method": "REPLY"})
        inbox = ImapInboxProvider(host="imap.test", username="u", password="p")
        seen = []
        monkeypatch.setattr(inbox, "_fetch", lambda: [(b"1", reply.as_bytes()), (b"2", accept.as_bytes())])
        monkeypatch.setattr(inbox, "_mark_seen", lambda nums: seen.extend(nums))
        report = await inbox.poll(db)
        assert report == {"provider": "imap", "messages": 2, "events": 2} and seen == [b"1", b"2"]
        await db.refresh(lead)
        assert (await db.get(Lead, lead.id)).status == "meeting_booked"


class TestBillionMail:
    @pytest.mark.anyio
    async def test_request_shape_and_auth_failure(self, db):
        _, person, lead = await _prospect(db)
        task = await _task(db, lead)
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"], seen["key"], seen["body"] = request.url.path, request.headers["x-api-key"], json.loads(request.content)
            return httpx.Response(200, json={"success": True})

        p = BillionMailProvider(base_url="http://bm.test", api_key="k1", addresser="alex@oxygen.test",
                                transport=httpx.MockTransport(handler))
        ctx = ExecutionContext(task_id=task.id, task_type="send_email", lead_id=lead.id,
                               payload={"subject": "S", "body": "B"})
        res = await p.execute(ctx, db)
        assert res.success and seen["path"] == "/api/batch_mail/api/send" and seen["key"] == "k1"
        assert seen["body"]["recipient"] == person.email and seen["body"]["attribs"]["oxygen_key"] == f"oxygen-{task.id}"
        denied = BillionMailProvider(base_url="http://bm.test", api_key="bad", addresser="a@o.test",
                                     transport=httpx.MockTransport(lambda r: httpx.Response(401)))
        assert (await denied.execute(ctx, db)).error_code == "UNAVAILABLE"


class TestReplyGapWaiver:
    @pytest.mark.anyio
    async def test_gap_waived_only_when_prospect_replied(self, db):
        from datetime import timedelta
        from app.compliance.rules import check_contact_allowed
        from app.models.base import Interaction
        _, _, lead = await _prospect(db)
        now = datetime.now(timezone.utc)
        db.add(Interaction(lead_id=lead.id, interaction_type="email_sent", direction="outbound", channel="email",
                           occurred_at=now - timedelta(hours=2)))
        await db.flush()
        assert (await check_contact_allowed(db, lead)).frequency_ok is False      # cold follow-up: 48h gap
        db.add(Interaction(lead_id=lead.id, interaction_type="email_received", direction="inbound", channel="email",
                           occurred_at=now - timedelta(hours=1)))
        await db.flush()
        status = await check_contact_allowed(db, lead)
        assert status.frequency_ok is True and status.touches["last_reply_at"]   # answering the prospect


class TestDemoScenarios:
    @pytest.mark.anyio
    async def test_seed_is_idempotent_and_labelled(self, db):
        from app.services.demo import seed_scenarios
        first = await seed_scenarios(db)
        again = await seed_scenarios(db)
        assert [r["created"] for r in first] == [True] * 4 and [r["created"] for r in again] == [False] * 4
        from app.models.base import Lead
        lead = await db.get(Lead, uuid.UUID(first[0]["lead_id"]))
        assert lead.notes.startswith("[SAMPLE")

    @pytest.mark.anyio
    async def test_scripted_playbook_through_real_controller(self, db):
        """The scripted demo vendor goes through the real LLM controller, validation and guardrails."""
        from app.llm import LLMRouter
        from app.llm.demo_client import ScriptedDemoClient
        from app.oxygen.context import OxygenContextBuilder
        from app.oxygen.llm_engine import LLMDecisionEngine
        from app.services.demo import seed_scenarios
        rows = {r["scenario"]: uuid.UUID(r["lead_id"]) for r in await seed_scenarios(db)}
        await db.commit()
        engine = LLMDecisionEngine(router=LLMRouter(clients={"openai": ScriptedDemoClient(),
                                                             "anthropic": ScriptedDemoClient()}))
        actions = {}
        for key, lead_id in rows.items():
            d = await engine.decide(await OxygenContextBuilder().build(db, lead_id))
            actions[key] = (d.action, d.engine)
        # test profile has no intent provider, so the playbook skips that step and qualifies
        assert actions["high_intent"] == ("qualify_lead", "llm")
        assert actions["insufficient_info"][0] == "enrich_company"
        assert actions["negative_response"][0] == "wait"        # contacted yesterday: give time to reply

    @pytest.mark.anyio
    async def test_reply_classification_and_opt_out(self, db):
        from app.llm.demo_client import ScriptedDemoClient
        c = ScriptedDemoClient()
        data, _, model = await c.complete_json(model="x", system="", user=json.dumps(
            {"subject": "Re", "reply": "Happy to talk, can you send times?"}), schema={},
            schema_name="reply_classification")
        assert data["category"] == "meeting_request" and model == "scripted-demo-playbook"

    @pytest.mark.anyio
    async def test_demo_endpoints_hidden_in_production(self, app, monkeypatch):
        from httpx import ASGITransport, AsyncClient
        from app.services import demo
        monkeypatch.setattr(demo, "demo_allowed", lambda: False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.post("/api/v1/demo/seed")).status_code == 404
