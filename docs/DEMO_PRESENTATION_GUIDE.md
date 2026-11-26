# Presenting Oxygen — Demo Guide

A 15–20 minute walkthrough of the complete autonomous pipeline. Everything runs locally in demo mode: fictional organisations and data, demo providers, scripted AI, **no email or API call leaves the machine**.

## Before the meeting (5 minutes)

```bash
python start.py
```

`start.py` checks Postgres and Redis, applies migrations, builds the dashboard, starts the API and the background worker, and loads the demo dataset. The browser opens at http://127.0.0.1:8000.

1. Sign in by choosing a demo user. **Northstar Revenue Cloud (Demo)** is the main story. Sign in as **Avery Collins (admin)** to present everything, including approvals.
2. Open **Demo Control Centre**:
   - Click **Reset demo environment** to start from a clean state.
   - Click **Start complete pipeline**. Discovery runs for 12 target accounts and the worker starts researching, qualifying and drafting. Within about 30 seconds, drafts are waiting in the **Approval Centre**.

Tips:
- **Pause workflow** freezes the background engine while you talk; **Resume workflow** continues.
- **Run worker tick now** moves things forward on demand.

## Storyline

| Minute | Show | Say |
|---|---|---|
| 0–2 | **Dashboard** | Every number comes from database records and updates live. The demo banner says the AI is scripted and providers are simulated. |
| 2–4 | **Campaigns** → *Q1 Mid-market SaaS Outbound* | A campaign holds the ICP, the offer and the follow-up sequence. Pause, resume and stop act on the running workflow. |
| 4–5 | **Target Accounts**, **Contacts** | Stage 1–3: accounts with priorities, discovered contacts with verification status. All are labelled demo data. |
| 5–7 | **Qualification** | Stage 4: open a lead to see the per-dimension explanation. Edit the ICP (e.g. add *Logistics*) → **Preview impact** → **Save & recalculate**. |
| 7–8 | **Research** | Stage 5: signals with source and "why it matters". Each one is tagged DEMO, never presented as fact. |
| 8–10 | **AI Drafts** → **Approval Centre** | Stage 6–7: the explanation and evidence behind a draft. **Regenerate**, edit, then approve. The diff shows exactly what the human changed. Nothing is sent without approval. |
| 10–13 | **Demo Control Centre → Scenarios** | Run **Successful conversion**: email sent → interested reply → meeting proposed and approved → simulated invite → accepted → booked. Show it in **Meetings** and on the **Dashboard**. |
| 13–15 | Scenarios **Prospect objection** and **Question Oxygen cannot answer** | Stage 10: grounded answers from the approved knowledge base; an unanswerable question is escalated, not invented. Show **Inbox**. |
| 15–16 | Scenario **Prospect does not reply** | Stage 8: the demo clock advances 3 days and the follow-up is drafted, approved and sent. Show **Follow-ups**. |
| 16–17 | Scenario **Prospect unsubscribes** | Immediate suppression; follow-ups cancelled; lead closed. |
| 17–18 | Sign out → sign in as **Jordan Patel (operator)**, then **Sam Okafor (Helix admin)** | Roles: the operator cannot approve. Isolation: Helix sees only its own hospital accounts and campaign. |
| 18–20 | **Integrations** | Each provider shows *Demo Active*; connection tests are simulated. Going live means adding keys (see FUTURE_API_INTEGRATION.md). |

## Manual triggers (any prospect)

In **Demo Control Centre → Prospect triggers**:

1. Pick a prospect who has already received an email (stage *contacted*).
2. Click one of the reply triggers: interested, meeting request, question, objection, out of office or unsubscribe.

The reply goes through the real inbound pipeline: matching, classification, follow-up cancellation and next action. Results show immediately in the **Inbox**. **Simulate follow-up** advances the clock by 3 days so scheduled follow-ups fall due.

## Running the demo again

Click **Reset demo environment** at any time. It deletes only the two demo organisations' data and reloads the dataset. Each scenario is repeatable after a reset.

## If something looks stuck

- Click **Run worker tick now**. The background worker ticks every 4 seconds.
- Check the **Workflow execution logs** panel or the prospect's **Workflow** tab: each stage shows done, active or pending.
- Approvals only proceed when a person (admin or approver) approves them. That is by design.
