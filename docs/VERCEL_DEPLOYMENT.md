# Deploying the Oxygen demo on Vercel

The demo runs on Vercel as:

- the **dashboard**, built by Vercel and served as static files;
- the **API** as one Python serverless function (`api/index.py`, up to 60 s per request);
- **Neon Postgres** and **Upstash Redis** from the Vercel Marketplace.

Vercel has no always-on processes, so the background worker is replaced by:

- a **tick pump**: while an admin or operator has the dashboard open, the browser asks the server to run the pipeline every 6 s;
- a **Vercel Cron** hitting `/api/v1/cron/tick` (daily on Hobby; every minute on Pro).

The first visit to a new deployment migrates the database and loads the demo dataset automatically. A **demo access code** must be entered before the demo-user picker appears.

Verified on Vercel production (Neon + Upstash, 2026-11-26): first-visit self-setup, access code (wrong code refused), demo sign-in, the pipeline driven by the tick pump, research, drafts, approvals, follow-ups, a full conversion scenario, pause/resume seen by every function instance through Redis, tenant isolation enforced on Neon (migration 0013), and the live stream ending itself before the function limit.

Python dependencies come from `requirements.txt`. Keep a `[project]`-less `pyproject.toml` out of the repository root: Vercel installs from `pyproject.toml` when one exists, and the function then fails with `No module named 'fastapi'`. Pytest settings live in `pytest.ini` for this reason.

## One-time setup (about 10 minutes)

1. **Sign in to Vercel from this folder** (opens a browser):

   ```bash
   npx vercel@latest login
   ```

2. **Create and link the project:**

   ```bash
   npx vercel@latest link
   ```

3. **Add the databases.** In the Vercel dashboard, go to the project → **Storage**:
   - Add **Neon** (Postgres). It sets `DATABASE_URL` and `DATABASE_URL_UNPOOLED`. Oxygen uses the unpooled one automatically.
   - Add **Upstash** (Redis). Copy its `rediss://…` URL into a variable named `REDIS_URL`. Redis holds the shared pause switch and provider health; without it each function instance keeps its own.

4. **Set the environment variables** (Production). Use long random values for the secrets, e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`:

   | Variable | Value |
   |---|---|
   | `GTM_PROFILE` | `demo` |
   | `ENVIRONMENT` | `production` |
   | `SECRET_KEY` | long random string (required: demo sign-in is refused without it) |
   | `DEMO_ACCESS_CODE` | the code you give to viewers |
   | `CRON_SECRET` | long random string (Vercel Cron sends it automatically) |
   | `REDIS_URL` | the Upstash `rediss://` URL |

   Do **not** set `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `SMTP_*`, `IMAP_*` or `APOLLO_API_KEY` for the demo. The demo profile ignores live providers anyway, and nothing leaves the system.

5. **Deploy:**

   ```bash
   npx vercel@latest deploy --prod
   ```

6. **Open the URL.** The first load takes a few seconds while it sets up the database. Then enter the access code and pick **Avery Collins (admin)**.

## Presenting on the hosted demo

- Follow `DEMO_PRESENTATION_GUIDE.md`. Keep the dashboard open as Avery Collins (admin) or Jordan Patel (operator), so the tick pump keeps the pipeline moving.
- **Run worker tick now** and the scenario buttons also run the pipeline on demand.
- Approvals appear about 30–45 s after **Start complete pipeline** (the pump ticks every 6 s).
- **Reset demo environment** returns everything to the starting point.

## Limits on Vercel

- **No pipeline progress without an open dashboard** between cron runs. Cron is daily on Hobby; change the schedule in `vercel.json` to `* * * * *` on Pro.
- **Live updates reconnect every ~25 s** (the function time limit); screens also refresh every 20 s.
- **Long scenario runs** (up to ~20 s) must finish within the 60 s function limit; they do in local tests.
- **Webhooks** for live inbound email are single-tenant, as elsewhere.

## Running it like Vercel, locally

```bash
OXYGEN_SERVERLESS=1 GTM_PROFILE=demo ENVIRONMENT=production SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))") \
DEMO_ACCESS_CODE=letmein CRON_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(32))") \
DATABASE_URL=postgresql://gtm_user:gtm_password@localhost:5432/<empty database> \
.venv/bin/python -m uvicorn api.index:app --port 8000
```
