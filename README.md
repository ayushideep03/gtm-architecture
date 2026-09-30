# GTM Autonomous System

An **Autonomous AI Sales/GTM (Go-To-Market) system** built with FastAPI, PostgreSQL, Redis, and LLM providers.

This repository is the **foundation layer** — the persistent state, configuration, logging, and infrastructure that all future autonomous agents, workers, and integrations will build on.

---

## Architecture Overview

```
Lead Sources
    ↓
Data Processing & Enrichment
    ↓
CompAI CRM / Persistent State  ← YOU ARE HERE (Step 1)
    ↓
Oxygen Orchestrator
    ↓
Guardrails
    ↓
Execution Tools
    ↓
Prospect
    ↓
Events / Webhooks
    ↓
CRM Update
    ↓
Oxygen again
```

**Supporting systems:** PostgreSQL · Redis · LLM Providers · File Storage · Monitoring

---

## Project Structure

```
gtm-architecture/
├── app/
│   ├── main.py                 # FastAPI application factory + lifespan
│   ├── core/
│   │   ├── config.py           # Pydantic-Settings configuration
│   │   ├── logging.py          # Structured logging + request middleware
│   │   ├── database.py         # SQLAlchemy async engine + session factory
│   │   └── redis.py            # Redis async pool + health check
│   ├── models/
│   │   ├── __init__.py         # Re-exports all ORM models
│   │   └── base.py             # Company, Person, Lead, Interaction, Task, Event
│   ├── schemas/
│   │   └── __init__.py         # Pydantic I/O schemas (Create/Read/Update)
│   ├── api/
│   │   ├── __init__.py         # Aggregates all routers
│   │   └── routes/
│   │       └── health.py       # GET /health, /health/db, /health/redis
│   ├── services/               # Business logic (to be built in Step 2+)
│   ├── integrations/           # Provider interfaces (Apollo, LinkedIn, etc.)
│   ├── agents/                 # Oxygen orchestrator + individual agents
│   ├── events/                 # Domain event publishing + inbound webhooks
│   ├── guardrails/             # Agent behaviour constraints
│   ├── evals/                  # Quality measurement framework
│   └── workers/                # Background task workers
├── alembic/
│   ├── env.py                  # Async-aware migration environment
│   └── script.py.mako          # Migration file template
├── tests/
│   ├── conftest.py             # Fixtures + environment setup
│   ├── test_health.py          # App startup + /health tests
│   ├── test_models.py          # ORM + Pydantic schema tests
│   └── test_connectivity.py    # DB + Redis health probe tests
├── alembic.ini                 # Alembic configuration
├── docker-compose.yml          # PostgreSQL + Redis + API
├── Dockerfile                  # Multi-stage Python 3.13 image
├── requirements.txt            # Python dependencies
├── pyproject.toml              # pytest configuration
├── .env.example                # Environment variable template
└── .env                        # Local dev values (not committed)
```

---

## Local Setup

### Prerequisites

- Python 3.11+
- PostgreSQL 15+ running locally (or Docker)
- Redis 7+ running locally (or Docker)

### Option A — Docker Compose (recommended)

```bash
# 1. Copy and edit env file
cp .env.example .env

# 2. Start everything (PostgreSQL + Redis + API + migrations)
docker compose up --build

# API is now available at http://localhost:8000
```

### Option B — Local Python

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and edit env file
cp .env.example .env
# Edit .env with your local PostgreSQL and Redis URLs

# 3. Run database migrations
alembic upgrade head

# 4. Start the API
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | ✅ | — | PostgreSQL connection string |
| `REDIS_URL` | ✅ | `redis://localhost:6379/0` | Redis connection string |
| `ENVIRONMENT` | — | `development` | `development` / `staging` / `production` |
| `LOG_LEVEL` | — | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `OPENAI_API_KEY` | — | `""` | OpenAI API key (used later) |
| `ANTHROPIC_API_KEY` | — | `""` | Anthropic API key (used later) |
| `SECRET_KEY` | — | (insecure default) | JWT signing key — **change in production** |

See `.env.example` for a complete template.

---

## Database Migrations

```bash
# Apply all migrations (create tables)
alembic upgrade head

# Roll back the last migration
alembic downgrade -1

# Generate a new migration from model changes
alembic revision --autogenerate -m "describe your change"

# Show current migration state
alembic current

# Show migration history
alembic history --verbose
```

---

## Health Checks

```bash
# API liveness
curl http://localhost:8000/health

# PostgreSQL connectivity
curl http://localhost:8000/health/db

# Redis connectivity
curl http://localhost:8000/health/redis
```

Expected responses:

```json
// /health
{"status": "ok", "app": "GTM Autonomous System", "version": "0.1.0", "environment": "development"}

// /health/db  (when PostgreSQL is up)
{"status": "ok", "detail": "PostgreSQL reachable"}

// /health/redis  (when Redis is up)
{"status": "ok", "detail": "Redis reachable"}
```

---

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app

# Run only health tests
pytest tests/test_health.py -v

# Run only model tests
pytest tests/test_models.py -v
```

---

## API Documentation

When running locally, interactive API docs are available at:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/openapi.json

---

## Key Design Decisions

### 1. Domain logic is separated from external integrations
Services and agents call **integration interfaces**, not provider SDKs directly.
This means you can swap Apollo for another lead source without touching the orchestration logic.

### 2. All external capabilities are behind provider interfaces
Future providers (Apollo, BillionMail, LinkedIn, Calendly, etc.) will implement interfaces defined in `app/integrations/`.

### 3. Events are immutable (append-only)
The `events` table is a domain event log. Events are never updated, only appended.
The Oxygen orchestrator reads events to decide what to do next.

### 4. Async throughout
SQLAlchemy uses the `asyncpg` driver. Redis uses `redis.asyncio`.
This maximises throughput for I/O-bound agentic workloads.

### 5. Structured logging with request tracing
Every request gets a unique `X-Request-ID` header.
All log entries in the request scope include this ID for correlation.

---

## What's Next (Step 2+)

- Lead CRUD API (Company, Person, Lead endpoints)
- LeadService + basic lead lifecycle state machine
- Oxygen Orchestrator skeleton
- First integration interface definitions (LeadSource, EnrichmentProvider)
- Task queue worker infrastructure (Redis-backed)
- Guardrails framework
