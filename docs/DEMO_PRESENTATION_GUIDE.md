# Presenting Oxygen — Demo Guide

A 15–20 minute walkthrough of the complete autonomous pipeline. Everything runs locally in demo mode: fictional organisations and data, simulated providers, a scripted AI. **No email, calendar invite or API call leaves the machine**, and the UI labels every simulated item **DEMO**.

## Launch (one command)

Prerequisites, once: Python venv with `pip install -r requirements.txt`; PostgreSQL and Redis running; Node.js 18+ to build the dashboard the first time.

```bash
python start.py
```

This picks demo mode automatically when no `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` is set (force it with `python start.py --profile demo`). It:
1. Checks Postgres and Redis.
2. Applies migrations.
3. Builds the dashboard if needed.
4. Starts the API and the background worker.
5. Loads the demo dataset.
6. Opens **http://127.0.0.1:8000**.

Ctrl+C stops everything.

## Sign in

The sign-in page lists demo users by organisation. Click a name; no password is needed in demo mode.

| Organisation | User | Role | Use for |
|---|---|---|---|
| Northstar Revenue Cloud (Demo) | **Avery Collins** | admin | **Presenting.** Can do everything, including approvals and resets. |
| Northstar Revenue Cloud (Demo) | Morgan Reyes | approver | Showing a reviewer: approve or reject only |
| Northstar Revenue Cloud (Demo) | Jordan Patel | operator | Showing that operators cannot approve |
| Helix Clinical Systems (Demo) | Sam Okafor / Taylor Brooks / Riley Chen | admin / approver / operator | Showing organisation isolation |

## Before the audience arrives (2 minutes)

1. Sign in as **Avery Collins**.
2. Open **Demo Control Centre** and click **Reset demo environment** → **Delete and reload**. This gives a clean state; demo users are kept.
3. Leave it there. Start the pipeline live in front of the audience.

## The management sequence (verified end to end)

| # | Do | What the audience sees |
|---|---|---|
| 1 | Sign in as **Avery Collins (admin)** | Role, organisation and the demo-mode banner |
| 2 | **Demo Control Centre** | Presenter, demo clock (+0 h), workflow running, dataset loaded |
| 3 | **Start complete pipeline** → confirm | Discovery runs for 12 target accounts and finds 34 contacts. The worker then researches, qualifies and drafts on its own. |
| 4 | **Target Accounts** | Accounts with industry, size, location, technologies, priority, discovery status and source (`demo_apollo`) |
| 5 | **Qualification** | 34 evaluated, 30 qualified, 4 disqualified. Expand a row for the per-dimension score and reasons. Optional: **Edit criteria** → **Preview impact** → **Save & recalculate**. |
| 6 | **Research** | Signals per company (news, hiring, technology, funding, …) and intent assessments, each tagged DEMO with its source and "why it matters" |
| 7 | **AI Drafts** | Personalised drafts with an explanation, evidence and strategy. **Regenerate** gives a new version. |
| 8 | **Approval Centre** → select **Priya Shah** → **Approve** | About **25–30 s after step 3**, 30 drafts wait here. The toast says the send is simulated. |
| 9 | Wait about 5 s (or **Run worker tick now**) | The email is "sent": recorded as a simulated send and Priya moves to *contacted* |
| 10 | **Demo Control Centre → Prospect triggers**: search "Priya", choose **Priya Shah**, **Trigger reply (interested)** | Matched to Priya, classified *positive interest*, follow-up cancelled, meeting request recorded |
| 11 | **Meetings** | A *Discovery call* proposal, pending approval, with a banner linking to the Approval Centre |
| 12 | **Approval Centre** → the *book meeting* item → **Approve** | A simulated Google Calendar invitation with 3 proposed slots appears in **Meetings** |
| 13 | **Meetings** → **Prospect accepts this slot** → **Accept slot (simulated)** | Status **booked** with the time; Priya's stage is *meeting booked* |
| 14 | **RevOps** and **Dashboard** | Funnel, rates (flagged *small n* on few touches), decisions; all 15 dashboard KPIs |
| 15 | (Optional proof) | Every dashboard number is a database count. In validation the dashboard matched SQL exactly. |

Then show the other behaviours with one click each in **Demo Control Centre → Predefined scenarios** (each takes 5–20 s and shows its step log):

- **Prospect objection:** an answer drafted only from approved facts, approved and sent. Show it in **Inbox**.
- **Prospect asks a question:** a grounded answer. Contrast with **Question Oxygen cannot answer**, which is escalated to a person and not invented.
- **Prospect does not reply:** the demo clock advances past the delay; the follow-up is drafted, approved and sent. Show **Follow-ups**.
- **Prospect unsubscribes:** suppressed, consent withdrawn, follow-ups cancelled, lead closed.

## Suggested 15–20 minute running order

| Min | Section |
|---|---|
| 0–2 | Dashboard and the demo-mode banner: what is real (workflow, rules, approvals, data) and what is simulated (providers, AI) |
| 2–4 | Campaigns: ICP, offer, follow-up sequence; pause, resume and stop |
| 4–9 | Steps 2–7 above: start the pipeline live, then accounts, qualification, research, drafts |
| 9–13 | Steps 8–13: approve an email, reply, meeting proposal, approval, booked |
| 13–16 | Scenarios: objection, unanswerable question (escalation), no reply, unsubscribe |
| 16–18 | Roles and isolation: sign out, then sign in as **Jordan Patel** (operator: "can review but not approve") and **Sam Okafor** (Helix sees only its own campaign) |
| 18–20 | **Integrations** (every provider *Demo Active*, tests marked *Simulated*) and the go-live path (FUTURE_API_INTEGRATION.md) |

## Manual triggers

In **Demo Control Centre → Prospect triggers**, pick a prospect who has received an email (stage *contacted*). The trigger buttons are: interested, meeting request, objection, question, unsupported question, out-of-office and unsubscribe. Replies go through the real inbound pipeline. **Simulate follow-up (+3 days)** advances the demo clock so scheduled follow-ups fall due. The clock buttons (+1 hour … +7 days) do the same in smaller steps.

## Good to know

- **Timing:** after **Start complete pipeline**, approvals appear after about 25–30 s. Screens refresh live; the sidebar shows "Live updates on".
- **Demo clock:** advancing it moves that organisation's records into the past, so "3d ago" labels and the trend chart shift accordingly. That is expected.
- **Repeatable:** **Reset demo environment** deletes only the two demo organisations' data and reloads the dataset.
- **If something seems stuck:**
  - Click **Run worker tick now**.
  - Check the **Workflow execution log** panel or the prospect's **Workflow** tab, where each stage shows done, active or pending.
  - Approvals only move when an admin or approver approves them. That is by design.
