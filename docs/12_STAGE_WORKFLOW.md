# The 12-Stage Pipeline

Each stage is a real step in the workflow engine. It writes database records and events, and it hands over to the next stage through events and work items, not through the UI. The prospect drawer's **Workflow** tab (`GET /api/v1/workflow/leads/{id}`) shows each prospect's position.

| # | Stage | What happens | Triggered by | Records | Screen |
|---|---|---|---|---|---|
| 1 | Target account selection | Companies are added manually or by CSV, prioritised and selected for a campaign with an ICP. | User | `companies`, `campaign_accounts`, `campaigns.icp` | Campaigns, Target Accounts |
| 2 | Lead discovery | **Start pipeline** activates the campaign and runs `find_leads` for each selected account (demo: `demo_apollo`). Contacts are ingested with identity resolution and deduplication. | User (Start), API | `people`, `leads` (campaign), `source_records`, `account_discovered` | Target Accounts |
| 3 | Contact identification | Name, title, department, seniority, email, verification status and source. Each `lead_ingested` event wakes Oxygen. | Discovery | `people.verification_status` | Contacts |
| 4 | ICP qualification | `icp_rules` scores industry, size, location, technology and persona against the campaign ICP: score, fit, reasons for and against, hard disqualifiers. Editing the ICP gives a preview and a recalculation. | Oxygen (`qualify_lead`) | `leads.score/status`, qualification finding | Qualification |
| 5 | Research and intent | `research_prospect` (demo: simulated, labelled signals with source and "why it matters"), then `analyze_intent`. Every AI-written finding stores which model wrote it. Scripted demo AI output is stored as `demo: true` with source "… (scripted demo AI)", and every view tags it DEMO. | Oxygen | research findings | Research |
| 6 | Personalised outreach | `draft_outreach` writes subject and body from prospect, company, ICP, signals and campaign offer, with a personalisation explanation, evidence and strategy. Users can regenerate, edit, save and submit. | Oxygen / user | `drafts` (versioned), task result | AI Drafts |
| 7 | Approval | Oxygen proposes `send_email`, which creates an approval (hash-locked payload). Approve (optionally edited, shown as a diff) queues the send; reject returns it for redrafting. Compliance is re-checked at send time. | Oxygen → human | `approvals`, `drafts.status` | Approval Centre |
| 8 | Follow-ups | After each send, the next sequence step is scheduled (`delay_days`). Due steps wake Oxygen, which drafts the follow-up for approval. A reply cancels follow-ups; out-of-office postpones them; unique per step. | Send hook, worker | `follow_ups` | Follow-ups |
| 9 | Reply detection and classification | Inbound mail (demo: simulated inbox) is matched to the prospect by `In-Reply-To` or sender and classified (interested, meeting request, question, objection, out of office, not now, not interested, unsubscribe). Opt-outs suppress deterministically. A reply cancels scheduled follow-ups and **withdraws any outreach email still waiting for approval** (recorded as `system (prospect replied)`); answers to the prospect stay. | Inbox | interactions, `reply_analysis`, suppressions | Inbox |
| 10 | Response intelligence | Questions and objections get `draft_reply`, using only the approved knowledge base. Every question must be covered, or it is **escalated** rather than answered. The answer goes through approval before it is sent. | Oxygen | reply `drafts`, escalations | Inbox |
| 11 | Meeting management | Interest or a meeting request leads to a meeting proposal, then approval, then a calendar invitation with slots. Accept, decline or reschedule arrives through the calendar pipeline: booked, declined or a new proposal. | Oxygen / prospect | `meetings` (+history) | Meetings |
| 12 | RevOps | Outcomes are attributed to touches; KPIs, conversion rates, funnel, time series and per-campaign performance are computed from records. | Worker / API | `outcome_attributions` | Dashboard, RevOps |

## Decision rules (what Oxygen proposes next)

Oxygen's planner (an LLM, or the scripted demo playbook) sees the prospect's full memory: campaign, findings, interactions, tasks, follow-ups, meetings, drafts and compliance. It proposes one action, which guardrails then check. The demo playbook, in order:

1. If the campaign is stopped, stop. If it is paused, wait. If the prospect is suppressed, stop. If a meeting is booked, escalate to the account executive. If disqualified, stop.
2. If the prospect declined the meeting, escalate. If they asked to reschedule, propose new slots.
3. On a reply:
   - **interested or meeting request:** propose a meeting (or wait if an invite is out).
   - **question or objection:** draft a reply. If it's grounded, propose sending it; otherwise escalate.
   - **out of office:** wait.
   - **not now:** wait 30 days.
   - **not interested or unsubscribe:** stop.
4. Enrich, research, analyse intent and qualify, as needed.
5. Draft the first email, then propose sending it.
6. Follow-ups: if a step is due, draft it, then propose sending it. If a step is scheduled, wait until it's due. When the sequence is complete, stop.

## Safety on every stage

- External actions always need approval.
- Compliance (suppression, lawful basis, frequency caps, minimum gap) is checked when proposing and again when sending.
- A reply waives the cooldown and minimum gap so the prospect can be answered. Count limits still apply.
- In demo mode, only demo providers can execute external actions.
