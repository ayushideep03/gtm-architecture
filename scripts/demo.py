"""
GTM framework live demo.

Walks one lead through the full loop against the running API:
    ingest -> Oxygen decides -> Guardrails authorize -> Task -> Execute -> Events
and then shows the safety checks, the lead timeline, RevOps metrics and evals.

Usage (API must be running on localhost:8000):
    python scripts/demo.py               # pauses between sections (for presenting)
    python scripts/demo.py --no-pause    # runs straight through
    python scripts/demo.py --reset       # wipe the local DB first (clean, repeatable demo)
    python scripts/demo.py --base-url http://host:8000

Works on macOS, Linux and Windows.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

# Enable ANSI colours in the Windows console.
if os.name == "nt":
    os.system("")

BOLD, DIM, GREEN, RED, YELLOW, CYAN, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m"
)

PAUSE = True


def header(n: int, title: str, subtitle: str = "") -> None:
    if PAUSE and n > 1:
        input(f"\n{DIM}Press Enter for the next step...{RESET}")
    print(f"\n{BOLD}{CYAN}━━ Step {n}: {title} {'━' * max(3, 54 - len(title))}{RESET}")
    if subtitle:
        print(f"{DIM}{subtitle}{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}✔{RESET} {msg}")


def bad(msg: str) -> None:
    print(f"  {RED}✘{RESET} {msg}")


def info(msg: str) -> None:
    print(f"    {msg}")


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.client = httpx.Client(timeout=30)

    def get(self, path: str, **params: Any) -> Any:
        r = self.client.get(self.base + path, params=params or None)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: Any = None) -> Any:
        r = self.client.post(self.base + path, json=body)
        r.raise_for_status()
        return r.json()


def pick_lead(api: Api) -> tuple[str, dict]:
    """Choose a lead that hasn't been qualified yet, so the demo can be re-run."""
    events = api.get("/api/v1/revops/events", event_type="lead_ingested", limit=200)["events"]
    seen: set[str] = set()
    for ev in events:
        lead_id = ev["lead_id"]
        if lead_id in seen:
            continue
        seen.add(lead_id)
        timeline = api.get(f"/api/v1/revops/leads/{lead_id}/timeline")
        if timeline["lead_status"] != "qualified":
            return lead_id, ev["payload"]
    raise SystemExit(
        f"{YELLOW}Every ingested lead is already qualified. Reset the demo database "
        f"(see README, 'Demo') and run again.{RESET}"
    )


def describe_decision(d: dict) -> str:
    cap = f" via {d['capability']}" if d.get("capability") and d["capability"] != d["action"] else ""
    return f"{BOLD}{d['action']}{RESET}{cap}  {DIM}(reason: {d['reason']}){RESET}"


def run(api: Api) -> None:
    v1 = "/api/v1"

    # 1 ─ Health
    header(1, "System health", "API, PostgreSQL and Redis are all reachable.")
    for path, name in (("/health", "API"), ("/health/db", "PostgreSQL"), ("/health/redis", "Redis")):
        status = api.get(path)["status"]
        (ok if status == "ok" else bad)(f"{name}: {status}")

    # 2 ─ Ingestion
    header(2, "Lead ingestion", "Pull leads from a source, normalise them, de-duplicate by domain/email.")
    res = api.post(f"{v1}/leads/ingest", {"provider": "mock_apollo"})
    ok(f"Received {res['records_received']} records from {res['provider']}")
    info(f"Companies: {res['companies_created']} new, {res['companies_existing']} already in CRM")
    info(f"People:    {res['people_created']} new, {res['people_existing']} already in CRM")
    info(f"Leads created: {res['leads_created']}")
    if res["records_received"] and not res["companies_created"]:
        info(f"{DIM}(Nothing new: de-duplication stopped duplicate records.){RESET}")

    lead_id, payload = pick_lead(api)
    print(f"\n  Demo lead: {BOLD}{lead_id}{RESET}  {DIM}(source: {payload.get('source')}){RESET}")

    # 3 ─ Autonomous loop
    header(3, "Autonomous loop",
           "Oxygen decides -> Guardrails authorize -> Task created -> Executor runs it -> repeat.")
    first_task_shown = False
    last_task_id = None
    for cycle in range(1, 7):
        out = api.post(f"{v1}/oxygen/leads/{lead_id}/orchestrate")
        d = out["decision"]
        print(f"\n  {BOLD}Cycle {cycle}{RESET}  Oxygen decided: {describe_decision(d)}")

        if out["status"] == "blocked":
            bad(f"Guardrails blocked it: {out.get('error')}")
            break
        if d["action"] in ("no_action", "wait") or not out["task_created"]:
            ok("Nothing left to do for this lead: the loop stops by itself.")
            break

        ok(f"Guardrails approved; task created {DIM}{out['task_id']}{RESET}")

        if not first_task_shown:
            # Safety demo: asking again while the task is pending must NOT duplicate it.
            again = api.post(f"{v1}/oxygen/leads/{lead_id}/orchestrate")
            ok(f"Asked Oxygen again before execution -> {BOLD}{again['decision']['action']}{RESET}, "
               f"task_created={again['task_created']}  {DIM}(no duplicate work){RESET}")
            first_task_shown = True

        ex = api.post(f"{v1}/execution/tasks/{out['task_id']}/execute")
        (ok if ex["success"] else bad)(f"Executed -> {ex['task_status']}  ({ex['event_type']})")
        result = ex.get("result") or {}
        if "fields_updated" in result:
            info(f"Fields enriched: {', '.join(result['fields_updated'])}")
        elif "qualified" in result:
            info(f"Qualified: {result['qualified']}  score: {result.get('score')}  ({result.get('reason')})")
        last_task_id = out["task_id"]

    # 4 ─ Safety
    header(4, "Safety checks", "Guardrails fail closed; executing a task twice is a no-op.")
    gr = api.post(f"{v1}/guardrails/evaluate", {
        "action": "send_wire_transfer",
        "capability": "payments",
        "target_id": lead_id,
        "target_type": "lead",
        "has_company": True,
        "has_person": True,
    })
    if not gr["allowed"]:
        ok(f"Unregistered action 'send_wire_transfer' BLOCKED by {', '.join(gr['rule_ids'])}")
        info(gr["reason"])
    else:
        bad("Guardrails unexpectedly allowed an unknown action")
    if last_task_id:
        rerun = api.post(f"{v1}/execution/tasks/{last_task_id}/execute")
        if rerun.get("already_executed"):
            ok("Re-running a completed task -> already_executed=True (no double execution)")
    rules = api.get(f"{v1}/guardrails/rules")
    info(f"{rules['count']} active guardrail rules: "
         + ", ".join(r["rule_id"] for r in rules["rules"]))

    # 5 ─ Timeline
    header(5, "Lead timeline", "Every step is recorded as an append-only event (full audit trail).")
    tl = api.get(f"{v1}/revops/leads/{lead_id}/timeline")
    ok(f"Lead status: {BOLD}{tl['lead_status']}{RESET}   score: {tl.get('lead_score')}")
    for item in tl["timeline"]:
        ts = item["occurred_at"][11:19]
        info(f"{DIM}{ts}{RESET}  {item['title']}")

    # 6 ─ Metrics
    header(6, "RevOps metrics", "Live numbers computed from the event stream.")
    m = api.get(f"{v1}/revops/metrics")
    for key in ("leads_ingested", "companies_enriched", "people_enriched", "oxygen_decisions",
                "guardrail_blocks", "tasks_created", "tasks_completed", "tasks_failed"):
        info(f"{key.replace('_', ' '):<22} {m[key]}")
    info(f"{'execution success rate':<22} {m['execution_success_rate']:.0%}")

    # 7 ─ Evals
    header(7, "Evaluation suite", "Built-in checks that the decision logic behaves as designed.")
    names = {c["case_id"]: c["name"] for c in api.get(f"{v1}/evals/cases")["cases"]}
    ev = api.post(f"{v1}/evals/run")
    (ok if ev["failed"] == 0 else bad)(f"{ev['passed']}/{ev['total']} eval cases passed")
    for r in ev["results"]:
        mark = f"{GREEN}✔{RESET}" if r["passed"] else f"{RED}✘{RESET}"
        info(f"{mark} {r['case_id']}  {names.get(r['case_id'], '')}")

    print(f"\n{BOLD}{GREEN}Demo complete.{RESET} API docs: {api.base}/docs\n")


def reset_database() -> None:
    """Drop and re-create every table via Alembic. Local demo database only."""
    root = Path(__file__).resolve().parent.parent
    print(f"{YELLOW}Resetting the database (all GTM data will be deleted)...{RESET}")
    for args in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=root, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok("Fresh database ready")


def main() -> None:
    global PAUSE
    parser = argparse.ArgumentParser(description="Run the GTM framework demo.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--no-pause", action="store_true", help="don't wait for Enter between steps")
    parser.add_argument("--reset", action="store_true",
                        help="wipe and re-create the local database before the demo")
    args = parser.parse_args()
    PAUSE = not args.no_pause

    if args.reset:
        reset_database()

    api = Api(args.base_url)
    try:
        api.get("/health")
    except httpx.HTTPError:
        sys.exit(
            f"{RED}Can't reach the API at {args.base_url}.{RESET}\n"
            "Start it first:  uvicorn app.main:app --reload"
        )
    try:
        run(api)
    except httpx.HTTPStatusError as exc:
        sys.exit(f"{RED}API error {exc.response.status_code} on {exc.request.url}:{RESET}\n{exc.response.text}")
    except KeyboardInterrupt:
        print("\nDemo stopped.")


if __name__ == "__main__":
    main()
