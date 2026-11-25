"""Script to generate a professional PDF handover report using ReportLab."""

import os
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Preformatted,
    KeepTogether,
    HRFlowable,
)
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    """Canvas that adds 'Page X of Y' and header to all pages."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))

        # Header (pages after page 1)
        if self._pageNumber > 1:
            self.drawString(54, letter[1] - 36, "Autonomous AI Sales / GTM Architecture — Handover Document")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(54, letter[1] - 42, letter[0] - 54, letter[1] - 42)

        # Footer
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(letter[0] - 54, 30, page_str)
        self.drawString(54, 30, "Confidential — Engineering & Architecture Specifications")
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(54, 40, letter[0] - 54, 40)

        self.restoreState()


def build_pdf(filename: str):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    c_primary = colors.HexColor("#0F172A")    # Slate 900
    c_accent = colors.HexColor("#2563EB")     # Blue 600
    c_secondary = colors.HexColor("#334155")  # Slate 700
    c_muted = colors.HexColor("#64748B")      # Slate 500
    c_card_bg = colors.HexColor("#F8FAFC")    # Slate 50
    c_card_border = colors.HexColor("#E2E8F0")# Slate 200

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=28,
        textColor=c_primary,
        spaceAfter=6,
    )

    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=12,
        leading=16,
        textColor=c_accent,
        spaceAfter=15,
    )

    h1_style = ParagraphStyle(
        "SectionH1",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=19,
        textColor=c_primary,
        spaceBefore=16,
        spaceAfter=8,
        keepWithNext=True,
    )

    h2_style = ParagraphStyle(
        "SectionH2",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=c_secondary,
        spaceBefore=12,
        spaceAfter=6,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        "BodyDark",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=6,
    )

    bullet_style = ParagraphStyle(
        "BulletDark",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor("#1E293B"),
        leftIndent=15,
        firstLineIndent=-10,
        spaceAfter=3,
    )

    callout_style = ParagraphStyle(
        "Callout",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#1E3A8A"),
        spaceAfter=6,
    )

    code_style = ParagraphStyle(
        "CodeText",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#0F172A"),
    )

    story = []

    # Title block
    story.append(Paragraph("Autonomous AI Sales / GTM System", title_style))
    story.append(Paragraph("Project Architecture, Technical Overview & Engineering Handover Guide", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=c_accent, spaceBefore=0, spaceAfter=14))

    # 1. Executive Summary
    story.append(Paragraph("1. Executive Summary & Purpose", h1_style))
    p1 = (
        "Modern autonomous AI agents frequently suffer from the <b>'god-agent' antipattern</b>: a single LLM "
        "attempting to directly query databases, write emails, make high-level decisions, and trigger external APIs. "
        "This inevitably results in hallucinations, non-deterministic side effects, race conditions, and compliance failures."
    )
    story.append(Paragraph(p1, body_style))
    p2 = (
        "This repository provides a production-grade backend architecture built with <b>FastAPI, PostgreSQL, "
        "SQLAlchemy Async, Redis, and Alembic</b>. It strictly enforces separation of concerns across 5 core pillars:"
    )
    story.append(Paragraph(p2, body_style))

    # Core pillars callout
    pillars_table = Table(
        [
            [Paragraph("<b>Pillar</b>", body_style), Paragraph("<b>Architectural Mandate & Guarantee</b>", body_style)],
            [Paragraph("<b>Oxygen</b>", body_style), Paragraph("<b>DECIDES</b>: Assembles context from CRM & events; determines the next logical action. Never executes directly.", body_style)],
            [Paragraph("<b>Guardrails</b>", body_style), Paragraph("<b>AUTHORIZE / BLOCK</b>: Evaluates proposed actions against 9 deterministic business policies. Fails closed.", body_style)],
            [Paragraph("<b>Task Model</b>", body_style), Paragraph("<b>HANDOFF BOUNDARY</b>: Only authorized decisions become persistent Tasks in PostgreSQL with state machine tracking.", body_style)],
            [Paragraph("<b>Execution Layer</b>", body_style), Paragraph("<b>EXECUTES</b>: Uses DB row-level locking (<code>FOR UPDATE</code>) to guarantee concurrency safety and idempotency.", body_style)],
            [Paragraph("<b>PostgreSQL CRM</b>", body_style), Paragraph("<b>AUTHORITATIVE STATE</b>: Single source of truth for accounts, leads, tasks, and state.", body_style)],
            [Paragraph("<b>Events & RevOps</b>", body_style), Paragraph("<b>AUDIT & FEEDBACK</b>: Strictly append-only event stream powering continuous operational feedback.", body_style)],
        ],
        colWidths=[110, 394],
    )
    pillars_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
        ("TEXTCOLOR", (0, 0), (-1, -1), c_primary),
        ("GRID", (0, 0), (-1, -1), 0.5, c_card_border),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(pillars_table)
    story.append(Spacer(1, 12))

    # 2. Complete Architecture Diagram
    story.append(Paragraph("2. Complete Closed-Loop Architecture", h1_style))
    diagram_text = (
        "         Lead Sources (Apollo, Scout, Inbound Webhooks)\n"
        "                               ↓\n"
        "                  Lead Ingestion & Normalization\n"
        "                               ↓\n"
        "                CompAI CRM (PostgreSQL Authoritative)\n"
        "                               ↓\n"
        "              Identity Resolution & Enrichment Layer\n"
        "                               ↓\n"
        "                   Oxygen Orchestrator Engine\n"
        "                 (Context Gathering & Decision)\n"
        "                               ↓\n"
        "                           Decision\n"
        "                               ↓\n"
        "                 Guardrails & Policy Engine\n"
        "                   (Authorize / Block Action)\n"
        "                               ↓\n"
        "                         Task Created\n"
        "                (Persistent Handoff Boundary)\n"
        "                               ↓\n"
        "                     Execution Registry\n"
        "                (Provider Discovery & Selection)\n"
        "                               ↓\n"
        "                 Specialized GTM Capability Providers\n"
        "           (Research, Outreach, Meetings, CRM Mutation)\n"
        "                               ↓\n"
        "                     Task Execution Result\n"
        "                               ↓\n"
        "                    Append-Only Event Stream\n"
        "               (task_completed, task_failed, etc.)\n"
        "                               ↓\n"
        "                 RevOps & Evaluation Layer\n"
        "              (Cursor-based Stream & Evals Runner)\n"
        "                               ↓\n"
        "                CompAI CRM / Feedback Update\n"
        "                               ↓\n"
        "                  Oxygen Orchestration Loop ↺"
    )
    diag_table = Table([[Paragraph(f"<pre>{diagram_text}</pre>", code_style)]], colWidths=[504])
    diag_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(diag_table)
    story.append(Spacer(1, 14))

    # 3. Subsystem Breakdown
    story.append(Paragraph("3. Subsystem Breakdown & Implementation", h1_style))

    story.append(Paragraph("3.1 CRM Data Models (app/models/base.py)", h2_style))
    story.append(Paragraph(
        "Authoritative relational schema modeling the entire GTM domain: "
        "<b>Company</b> (accounts, domain, headcount, tech stack), <b>Person</b> (contacts, emails, titles), "
        "<b>Lead</b> (central sales opportunity tied to contacts), <b>Task</b> (work units with status transitions), "
        "<b>Interaction</b> (logged emails, responses, calls), <b>Event</b> (append-only stream), and "
        "<b>EventProcessingState</b> (durable consumer cursor tracking).",
        body_style
    ))

    story.append(Paragraph("3.2 Lead Ingestion & Normalization (app/services/lead_ingestion.py)", h2_style))
    story.append(Paragraph(
        "Ingests prospect batches from Apollo, Scout, or webhooks. Normalizes company domains and emails "
        "into canonical lowercase formats, applies deduplication logic to prevent creating redundant accounts, "
        "and emits <code>lead_ingested</code> events.",
        body_style
    ))

    story.append(Paragraph("3.3 Identity Resolution & Enrichment (app/services/enrichment.py)", h2_style))
    story.append(Paragraph(
        "Matches incoming contacts against existing database records. Connects to enrichment providers to "
        "populate industry, tech stack, funding stage, and seniority level without creating duplicate CRM entities.",
        body_style
    ))

    story.append(Paragraph("3.4 Oxygen Orchestration & Decision Layer (app/oxygen/)", h2_style))
    story.append(Paragraph(
        "Coordinates context gathering and decision evaluation. <code>OxygenContextBuilder</code> compiles "
        "an immutable view of the lead, accounts, task history, and past events. "
        "<code>DeterministicDecisionEngine</code> evaluates next steps (e.g., <code>qualify_lead</code>, "
        "<code>enrich_company</code>, <code>wait</code>, <code>no_action</code>).",
        body_style
    ))

    story.append(Paragraph("3.5 Guardrails & Policy Enforcement (app/guardrails/)", h2_style))
    story.append(Paragraph(
        "Acts as a formal policy gateway between Oxygen decisions and Task persistence. "
        "Evaluates 9 deterministic rules (GR-001 through GR-009):",
        body_style
    ))
    story.append(Paragraph("• <b>GR-001</b>: Unknown Capability Rule (Blocks unregistered capabilities)", bullet_style))
    story.append(Paragraph("• <b>GR-002</b>: Missing Target Rule (Blocks if required entity UUID is absent)", bullet_style))
    story.append(Paragraph("• <b>GR-003</b>: Missing Task Payload Rule (Blocks if parameters are missing)", bullet_style))
    story.append(Paragraph("• <b>GR-004</b>: Duplicate Active Task Rule (Blocks if task is already pending/in-progress)", bullet_style))
    story.append(Paragraph("• <b>GR-005</b>: Completed Task Rule (Blocks duplicate re-execution of completed tasks)", bullet_style))
    story.append(Paragraph("• <b>GR-006</b>: Unregistered Execution Provider Rule (Blocks if no execution provider exists)", bullet_style))
    story.append(Paragraph("• <b>GR-007</b>: Unsupported Task Type Rule (Blocks invalid action types)", bullet_style))
    story.append(Paragraph("• <b>GR-008</b>: Missing CRM Entity Rule (Blocks if target entity does not exist in DB)", bullet_style))
    story.append(Paragraph("• <b>GR-009</b>: Safe Valid Execution Rule (Approves valid, verified decisions)", bullet_style))
    story.append(Paragraph(
        "Fails closed: if blocked, records a <code>guardrail_blocked</code> event and stops task creation. "
        "If approved, records <code>guardrail_allowed</code> and persists the Task in PostgreSQL.",
        body_style
    ))

    story.append(Paragraph("3.6 Execution Layer & Task State Machine (app/execution/)", h2_style))
    story.append(Paragraph(
        "<code>TaskExecutor</code> executes approved tasks safely with database row locks (<code>with_for_update()</code>). "
        "Enforces strict state transitions: <code>pending</code> → <code>in_progress</code> → <code>completed</code> or <code>failed</code>. "
        "Emits <code>task_started</code>, <code>task_completed</code>, and <code>task_failed</code> events.",
        body_style
    ))

    story.append(Paragraph("3.7 GTM Capability Providers (app/integrations/gtm/)", h2_style))
    story.append(Paragraph(
        "Implements replaceable providers conforming to <code>ExecutionProvider</code>:<br/>"
        "• <b>MockProspectResearchProvider</b>: Structured account and prospect intelligence.<br/>"
        "• <b>MockOutreachDraftingProvider</b>: Template-based contextual email drafting.<br/>"
        "• <b>MockOutreachSendProvider</b>: Simulated email send (<code>mode: 'simulation'</code>, no SMTP calls).<br/>"
        "• <b>MockMeetingBookingProvider</b>: Simulated calendar scheduling (<code>mode: 'simulation'</code>).<br/>"
        "• <b>CRMActionProvider</b>: Validated CRM mutations (lead status, interaction logs).",
        body_style
    ))

    story.append(Paragraph("3.8 RevOps Stream Processing & Feedback (app/revops/)", h2_style))
    story.append(Paragraph(
        "<code>EventProcessor</code> consumes append-only domain events using PostgreSQL cursors "
        "(<code>event_processing_states</code>). Aggregates operational metrics (conversion rates, task success rates) "
        "and reconstructs chronological prospect journeys.",
        body_style
    ))

    story.append(Paragraph("3.9 Evaluation Framework (app/evals/)", h2_style))
    story.append(Paragraph(
        "<code>EvalRunner</code> deterministically executes 10 invariant cases (EVAL-001 through EVAL-010) "
        "in complete isolation, verifying system behavior without touching production data.",
        body_style
    ))

    story.append(Paragraph("3.10 Unified Control Loop (app/services/gtm_orchestration.py)", h2_style))
    story.append(Paragraph(
        "Exposes <code>GtmOrchestrationService.execute_cycle()</code>: coordinates a complete round-trip "
        "from decision to guardrails, execution, event logging, RevOps cursor update, and subsequent Oxygen feedback.",
        body_style
    ))
    story.append(Spacer(1, 10))

    # 4. Setup & Running Instructions
    story.append(Paragraph("4. Setup & Running Instructions", h1_style))
    setup_code = (
        "# 1. Unzip codebase and enter directory\n"
        "cd \"GTM architecture\"\n\n"
        "# 2. Create and activate virtual environment\n"
        "python -m venv .venv\n"
        "source .venv/bin/activate       # Windows: .venv\\Scripts\\activate\n\n"
        "# 3. Install dependencies\n"
        "pip install -r requirements.txt\n\n"
        "# 4. Apply database migrations to head\n"
        "alembic upgrade head\n"
        "alembic current                 # Expected: 0007 (head)\n\n"
        "# 5. Run full test suite (227 tests)\n"
        "python -m pytest -v\n\n"
        "# 6. Start the FastAPI development server\n"
        "uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
    )
    setup_table = Table([[Paragraph(f"<pre>{setup_code}</pre>", code_style)]], colWidths=[504])
    setup_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(setup_table)
    story.append(Spacer(1, 10))

    # 5. API Inventory Table
    story.append(Paragraph("5. API Route Inventory", h1_style))
    api_data = [
        [Paragraph("<b>Category</b>", body_style), Paragraph("<b>Endpoint</b>", body_style), Paragraph("<b>Method</b>", body_style), Paragraph("<b>Description</b>", body_style)],
        [Paragraph("Health", body_style), Paragraph("/health, /health/db, /health/redis", body_style), Paragraph("GET", body_style), Paragraph("Liveness, PostgreSQL, and Redis connectivity probes", body_style)],
        [Paragraph("Leads", body_style), Paragraph("/api/v1/leads/ingest", body_style), Paragraph("POST", body_style), Paragraph("Ingest & normalize lead batches with deduplication", body_style)],
        [Paragraph("Enrichment", body_style), Paragraph("/api/v1/enrichment/company/{id}", body_style), Paragraph("POST", body_style), Paragraph("Trigger company enrichment workflow", body_style)],
        [Paragraph("Oxygen", body_style), Paragraph("/api/v1/oxygen/leads/{id}/orchestrate", body_style), Paragraph("POST", body_style), Paragraph("Full decision -> guardrails -> task handoff", body_style)],
        [Paragraph("Execution", body_style), Paragraph("/api/v1/execution/tasks/{id}/execute", body_style), Paragraph("POST", body_style), Paragraph("Row-locked task execution via registered provider", body_style)],
        [Paragraph("Guardrails", body_style), Paragraph("/api/v1/guardrails/rules", body_style), Paragraph("GET", body_style), Paragraph("List all 9 active policy enforcement rules", body_style)],
        [Paragraph("RevOps", body_style), Paragraph("/api/v1/revops/metrics", body_style), Paragraph("GET", body_style), Paragraph("Aggregated GTM pipeline and conversion analytics", body_style)],
        [Paragraph("RevOps", body_style), Paragraph("/api/v1/revops/leads/{id}/timeline", body_style), Paragraph("GET", body_style), Paragraph("Chronological lead event & interaction timeline", body_style)],
        [Paragraph("Evals", body_style), Paragraph("/api/v1/evals/run", body_style), Paragraph("POST", body_style), Paragraph("Execute 10 invariant eval cases in isolation", body_style)],
    ]
    api_table = Table(api_data, colWidths=[65, 185, 45, 209])
    api_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
        ("GRID", (0, 0), (-1, -1), 0.5, c_card_border),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(api_table)
    story.append(Spacer(1, 12))

    # 6. Current Shortcomings & Limitations
    story.append(Paragraph("6. Current Shortcomings & Boundaries", h1_style))
    story.append(Paragraph(
        "<b>1. Simulated External Actions</b>: Outbound emails and calendar bookings operate strictly in simulation mode "
        "(<code>mode: 'simulation'</code>). No live emails are sent via SMTP/SendGrid, and no external calendar invites are booked.",
        body_style
    ))
    story.append(Paragraph(
        "<b>2. Deterministic Engines vs. Probabilistic LLMs</b>: Decision logic and outreach drafting currently use "
        "deterministic templates. This is an intentional architectural boundary to establish provable invariants before introducing LLM variance.",
        body_style
    ))
    story.append(Paragraph(
        "<b>3. No Continuous Background Daemon Loop</b>: Orchestration is triggered via REST endpoints or service functions. "
        "There is no Celery beat, Temporal worker, or cron daemon continuously polling and looping.",
        body_style
    ))
    story.append(Paragraph(
        "<b>4. Single-Tenant & Unauthenticated API</b>: Routes currently do not enforce JWT or API key authorization, "
        "and database records do not include an organization-level <code>tenant_id</code>.",
        body_style
    ))
    story.append(Paragraph(
        "<b>5. Headless / API-Only</b>: There is no frontend user interface; interactions occur via Swagger / REST APIs.",
        body_style
    ))
    story.append(Spacer(1, 10))

    # 7. Required Improvements for Production
    story.append(Paragraph("7. Required Improvements & Production Roadmap", h1_style))
    story.append(Paragraph(
        "<b>Priority 1: Connect Live External Providers</b><br/>"
        "Because capabilities conform to <code>ExecutionProvider</code>, swapping mocks requires zero changes to core logic. "
        "Implement <code>SendGridOutreachProvider</code> (email), <code>ApolloLiveEnrichmentProvider</code> (data), "
        "and <code>GoogleCalendarMeetingProvider</code> (OAuth2 calendar integration).",
        body_style
    ))
    story.append(Paragraph(
        "<b>Priority 2: Integrate Real LLM Decision & Drafting Agents</b><br/>"
        "Replace <code>DeterministicDecisionEngine</code> with an LLM engine utilizing OpenAI / Anthropic structured tool calling. "
        "Add prompt injection defenses, sentiment verification, and compliance checks inside <code>app/guardrails/</code>.",
        body_style
    ))
    story.append(Paragraph(
        "<b>Priority 3: Introduce Distributed Worker Execution (Celery / Temporal / ARQ)</b><br/>"
        "Deploy background workers to claim pending tasks and process event queues asynchronously. "
        "Add automated recurring cron schedules to run <code>GtmOrchestrationService.execute_cycle()</code> on newly ingested leads.",
        body_style
    ))
    story.append(Paragraph(
        "<b>Priority 4: Authentication, Authorization & Multi-Tenancy</b><br/>"
        "Implement JWT/API key authentication middleware and attach <code>tenant_id</code> foreign keys to all CRM models "
        "to ensure enterprise-grade data isolation across multiple client accounts.",
        body_style
    ))
    story.append(Paragraph(
        "<b>Priority 5: Web UI / Human-in-the-Loop RevOps Dashboard</b><br/>"
        "Build a Next.js / React interface displaying real-time metrics, lead timelines, and a review queue "
        "allowing sales reps to approve or edit AI-generated outreach drafts before sending.",
        body_style
    ))
    story.append(Spacer(1, 12))

    # 8. Test Suite Summary
    story.append(Paragraph("8. Verified Test Suite Summary", h1_style))
    test_data = [
        [Paragraph("<b>Test Suite File</b>", body_style), Paragraph("<b>Subsystem Covered</b>", body_style), Paragraph("<b>Status</b>", body_style)],
        [Paragraph("tests/test_guardrails.py", body_style), Paragraph("9 Policy Rules, Registry, Evaluator, API", body_style), Paragraph("<b>15 Passed</b>", body_style)],
        [Paragraph("tests/test_gtm_capabilities.py", body_style), Paragraph("Research, Drafting, Send Sim, Meetings, CRM", body_style), Paragraph("<b>11 Passed</b>", body_style)],
        [Paragraph("tests/test_revops.py", body_style), Paragraph("Event Processor, Cursors, Metrics, Timelines", body_style), Paragraph("<b>6 Passed</b>", body_style)],
        [Paragraph("tests/test_evals.py", body_style), Paragraph("10 Core Invariant Cases, Runner, Metrics", body_style), Paragraph("<b>7 Passed</b>", body_style)],
        [Paragraph("tests/test_end_to_end_gtm.py", body_style), Paragraph("Complete Closed Loop & Feedback Cycle", body_style), Paragraph("<b>2 Passed</b>", body_style)],
        [Paragraph("tests/test_oxygen.py", body_style), Paragraph("Oxygen Context, Decision Engine, Registry", body_style), Paragraph("<b>21 Passed</b>", body_style)],
        [Paragraph("tests/test_execution.py", body_style), Paragraph("Task State Machine, Row Locking, Providers", body_style), Paragraph("<b>20 Passed</b>", body_style)],
        [Paragraph("tests/test_enrichment.py", body_style), Paragraph("Company & Person Enrichment Workflows", body_style), Paragraph("<b>24 Passed</b>", body_style)],
        [Paragraph("tests/test_ingestion.py", body_style), Paragraph("Lead Ingestion, Normalization, Deduplication", body_style), Paragraph("<b>16 Passed (4 skipped)</b>", body_style)],
        [Paragraph("Broader System Suite", body_style), Paragraph("Connectivity, Health, Models, Normalization", body_style), Paragraph("<b>100 Passed (1 skipped)</b>", body_style)],
        [Paragraph("<b>Total Complete Suite</b>", body_style), Paragraph("<b>Full Application Regression</b>", body_style), Paragraph("<b>222 Passed, 5 skipped, 0 Failed</b>", body_style)],
    ]
    test_table = Table(test_data, colWidths=[160, 220, 124])
    test_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#ECFDF5")),
        ("GRID", (0, 0), (-1, -1), 0.5, c_card_border),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(test_table)

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Successfully generated PDF: {filename}")


if __name__ == "__main__":
    out_pdf = r"c:\Users\Ayushideep\Documents\GTM_Architecture_Handover_Guide.pdf"
    build_pdf(out_pdf)
    # Also write a copy directly in the project folder
    local_pdf = r"c:\Users\Ayushideep\Documents\GTM architecture\GTM_Architecture_Handover_Guide.pdf"
    import shutil
    shutil.copyfile(out_pdf, local_pdf)
    print(f"Copied to: {local_pdf}")
