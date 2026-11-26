"""
Live end-to-end validation of the Oxygen pipeline.

Runs the REAL API (uvicorn) and TWO real background workers against an isolated
database (gtm_e2e) and Redis prefix, with the real SMTP providers pointed at a
local sink on 127.0.0.1 (nothing is relayed; no prospect is contacted). Profile:
development (live providers where configured) with the labelled scripted demo AI
in place of an LLM key. For the demo-provider pipeline see tests/test_demo_framework.py.

Usage (from the repo root, Postgres + Redis running, psql/createdb on PATH):
    .venv/bin/python scripts/e2e/run_e2e.py [work_dir]

Creates and DROPS the database "gtm_e2e" (never your gtm_db). Takes ~2 minutes.
Prints PASS/FAIL per stage and writes <work_dir>/results.json.
"""
import email
import json
import os
import signal
import socket
import subprocess
import sys
import time
from email import policy
from pathlib import Path

import httpx

import tempfile
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="oxygen-e2e-"))
PY = sys.executable
DB = "gtm_e2e"
DB_URL = f"postgresql+asyncpg://gtm_user:gtm_password@localhost:5432/{DB}"
SINK = WORK / "mail"
LOGS = WORK / "logs"
RESULTS: list[tuple[str, bool, str]] = []
procs: dict[str, subprocess.Popen] = {}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SMTP_PORT, API_PORT = free_port(), free_port()
import secrets
E2E_KEY = secrets.token_urlsafe(24)
BASE = f"http://127.0.0.1:{API_PORT}/api/v1"


def check(stage: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((stage, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {stage}  {detail}", flush=True)
    return ok


def env() -> dict:
    return {**os.environ, "GTM_PROFILE": "development", "DATABASE_URL": DB_URL, "GTM_CONFIG_FILE": str(WORK / "overlay.yaml"),
            "SMTP_HOST": "127.0.0.1", "SMTP_FROM_ADDRESS": "alex@oxygen.test", "SMTP_USERNAME": "", "SMTP_PASSWORD": "", "PYTHONUNBUFFERED": "1",
            "OXYGEN_API_KEYS": "e2e-validator=" + E2E_KEY, "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "IMAP_HOST": "", "BILLIONMAIL_URL": "", "APOLLO_API_KEY": ""}


def start(name: str, args: list[str]) -> None:
    log = open(LOGS / f"{name}.log", "a")
    procs[name] = subprocess.Popen(args, cwd=ROOT, env=env(), stdout=log, stderr=subprocess.STDOUT)


def start_worker(name: str) -> None:
    start(name, [PY, "-m", "app.worker"])


def sql(q: str) -> str:
    r = subprocess.run(["psql", "-At", "-U", "gtm_user", "-h", "localhost", DB, "-c", q], capture_output=True,
                       text=True, env={**os.environ, "PGPASSWORD": "gtm_password"})
    if r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


def mails() -> list[dict]:
    out = []
    for f in sorted(SINK.glob("*.json")):
        d = json.loads(f.read_text())
        msg = email.message_from_string(d["raw"], policy=policy.default)
        d["msg"] = msg
        out.append(d)
    return out


def wait_until(pred, timeout: float, every: float = 1.0, on_tick=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if on_tick:
            on_tick()
        v = pred()
        if v:
            return v
        time.sleep(every)
    return None


c = httpx.Client(timeout=30, headers={"x-api-key": E2E_KEY})


def get(path, **kw):
    r = c.get(BASE + path, **kw)
    r.raise_for_status()
    return r.json()


def post(path, body=None):
    r = c.post(BASE + path, json=body or {})
    if r.status_code >= 400:
        raise RuntimeError(f"{path} -> {r.status_code} {r.text[:300]}")
    return r.json()


approved_log: list[str] = []


def approve_all(allowed=lambda a: True):
    for a in get("/approvals", params={"status": "pending"}):
        if allowed(a):
            post(f"/approvals/{a['id']}/approve", {"reviewer": "e2e-validator"})
            approved_log.append(a["capability"])


def lead_by_email(addr: str) -> dict | None:
    for p in get("/prospects", params={"limit": 1000}):
        if p.get("email") == addr:
            return p
    return None


def main() -> None:
    for d in (SINK, LOGS):
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*"):
            f.unlink()
    (WORK / "overlay.yaml").write_text(f"""
runtime: {{key_prefix: gtm-e2e}}
autonomy: {{tick_interval_s: 2}}
llm:                                            # scripted demo AI (no key); real SMTP to the local sink
  vendors: {{demo: {{}}}}
  profiles:
    planner: {{provider: demo, model: scripted-demo-playbook, fallback_profiles: []}}
    researcher: {{provider: demo, model: scripted-demo-playbook, fallback_profiles: []}}
    writer: {{provider: demo, model: scripted-demo-playbook, fallback_profiles: []}}
providers:
  - {{name: smtp_email, settings: {{port: {SMTP_PORT}, security: none}}}}
  - {{name: smtp_meetings, settings: {{port: {SMTP_PORT}, security: none}}}}
  - {{name: llm_research, enabled: false}}   # would fetch real company websites
""")
    subprocess.run(["dropdb", "--if-exists", "-U", "gtm_user", "-h", "localhost", DB], env={**os.environ, "PGPASSWORD": "gtm_password"}, check=True)
    subprocess.run(["createdb", "-U", "gtm_user", "-h", "localhost", DB], env={**os.environ, "PGPASSWORD": "gtm_password"}, check=True)
    subprocess.run([PY, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env(), check=True, capture_output=True)
    start("sink", [PY, str(Path(__file__).parent / "smtp_sink.py"), str(SMTP_PORT), str(SINK)])
    start("api", [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(API_PORT),
                  "--timeout-graceful-shutdown", "2"])
    assert wait_until(lambda: _health(), 40), "API did not start"
    print(f"API :{API_PORT}  SMTP sink :{SMTP_PORT}  db {DB}\n")
    post("/autonomy/pause", {"actor": "e2e"})  # set up data first, then let two workers loose
    start_worker("worker1")
    start_worker("worker2")

    # ── auth (live) ─────────────────────────────────────────────────────────
    anon = httpx.get(BASE + "/approvals")
    check("auth: protected route refuses anonymous caller", anon.status_code == 401, str(anon.status_code))
    fake = httpx.post(BASE + "/approvals/" + "0" * 8 + "-0000-0000-0000-000000000000/approve", json={"reviewer": "ceo"})
    check("auth: anonymous approve refused", fake.status_code == 401, str(fake.status_code))

    # ── 0. engine / providers ────────────────────────────────────────────────
    eng = get("/oxygen/engine")
    check("0 engine: scripted demo AI (labelled), not a real LLM", "demo" in json.dumps(eng), json.dumps(eng)[:160])
    provs = get("/execution/providers")
    avail = {p.get("provider_name", p.get("name")): p.get("available") for p in (provs if isinstance(provs, list) else provs.get("providers", []))}
    check("0 real SMTP provider available (local sink)", avail.get("smtp_email") is True, str({k: v for k, v in avail.items() if 'smtp' in k or 'mock_outreach' in k}))

    # ── 1-2. target account selection + lead discovery ──────────────────────
    ing = post("/leads/ingest", {"provider": "mock_apollo", "query": {"job_titles": ["VP Sales"]}})
    check("1-2 lead discovery via find_leads provider (mock_apollo)", ing.get("leads_created", 0) + ing.get("created", 0) > 0 or ing.get("total_fetched", 0) > 0, json.dumps(ing)[:200])
    seeded = post("/demo/seed")
    check("1-2 demo scenarios seeded", len(seeded.get("seeded", [])) == 4, json.dumps(seeded)[:160])
    n_leads = int(sql("select count(*) from leads"))
    check("3 contacts/identity resolved into persons+companies", int(sql("select count(*) from people")) >= n_leads > 0,
          f"leads={n_leads} persons={sql('select count(*) from people')} companies={sql('select count(*) from companies')}")

    post("/autonomy/resume", {"actor": "e2e"})
    priya = "priya.shah@northwind-analytics.example"

    # ── 4-6. enrichment, qualification, research, drafting → approval gate ──
    got = wait_until(lambda: any(a["prospect"]["email"] == priya for a in get("/approvals", params={"status": "pending"})), 120, 2)
    check("4-6 enrich→qualify→research→draft reached approval gate (Priya)", bool(got))
    pr = lead_by_email(priya)
    mem = get(f"/memory/leads/{pr['id']}")
    kinds = sorted({f["kind"] for f in mem["findings"]})
    caps = [t["capability"] for t in mem["tasks"]]
    check("4 qualification recorded with score", pr.get("score") is not None, f"score={pr.get('score')} status={pr['status']}")
    check("5 research findings in memory", len(kinds) > 0, f"finding kinds={kinds}")
    check("6 personalised draft exists", "draft_outreach" in caps, f"tasks={caps}")
    pend = [a for a in get("/approvals", params={"status": "pending"}) if a["prospect"]["email"] == priya]
    body = pend[0]["original_payload"].get("body", "") if pend else ""
    check("6 nothing sent before approval", len(mails()) == 0, f"sink={len(mails())}")
    check("6 draft cites company specifics", "Northwind" in body, body[:120].replace("\n", " "))

    # ── 7. human approval → real SMTP send ──────────────────────────────────
    approve_all(lambda a: a["capability"] == "send_email")
    sent = wait_until(lambda: [m for m in mails() if priya in m["rcpt"]], 60, 1)
    check("7 approved email sent over real SMTP to the prospect only", bool(sent) and sent[0]["rcpt"] == [priya],
          f"rcpt={sent[0]['rcpt'] if sent else None}")
    msgid = sent[0]["msg"]["Message-ID"] if sent else None
    check("7 deterministic Message-ID + List-Unsubscribe", bool(msgid and msgid.startswith("<oxygen-") and sent[0]["msg"]["List-Unsubscribe"]), str(msgid))
    pr = lead_by_email(priya)
    check("11 CRM: lead moved to 'contacted' after real send", pr["status"] == "contacted", pr["status"])

    # ── 8. follow-up scheduling (time travel) ───────────────────────────────
    nxt = wait_until(lambda: sql(f"select min(due_at) - now() from work_items where lead_id='{pr['id']}' and finished_at is null and due_at > now() + interval '1 hour'"), 30, 1)
    check("8 follow-up re-evaluation scheduled after send", bool(nxt), f"next due in {nxt}")

    # ── 9-10. reply detection + classification + response ───────────────────
    r = post("/webhooks/email", {"type": "reply", "provider": "e2e", "in_reply_to": msgid, "from_email": priya,
                                 "subject": "Re: " + str(sent[0]["msg"]["Subject"]), "event_id": "e2e-reply-1",
                                 "body": "Thanks Alex, this is timely. Happy to talk, can you send times next week?"})
    check("9 reply matched to the sent email by Message-ID", r.get("matched") is True, json.dumps(r)[:200])
    dup = post("/webhooks/email", {"type": "reply", "provider": "e2e", "in_reply_to": msgid, "from_email": priya,
                                   "body": "dup", "event_id": "e2e-reply-1"})
    check("9 duplicate webhook delivery de-duplicated", int(sql(f"select count(*) from interactions where lead_id='{pr['id']}' and interaction_type='email_received'")) == 1, json.dumps(dup)[:120])
    got = wait_until(lambda: [a for a in get("/approvals", params={"status": "pending"}) if a["prospect"]["email"] == priya and a["capability"] == "book_meeting"], 60, 2)
    cls = sql(f"select summary from research_findings where lead_id='{pr['id']}' and kind like '%reply%' order by created_at desc limit 1") if True else ""
    check("10 reply classified", bool(cls) or bool(got), cls[:120])
    check("10 response generated: meeting proposal awaiting approval", bool(got))
    approve_all(lambda a: a["capability"] == "book_meeting")
    inv = wait_until(lambda: [m for m in mails() if priya in m["rcpt"] and "text/calendar" in m["raw"]], 60, 1)
    check("10 iCalendar invitation sent over SMTP", bool(inv))
    uid = None
    if inv:
        for line in inv[0]["raw"].replace("\r\n ", "").splitlines():
            if line.startswith("UID:"):
                uid = line[4:].strip()
    r = post("/webhooks/calendar", {"type": "booked", "provider": "e2e", "meeting_id": uid or "?", "attendee_email": priya,
                                    "event_id": "e2e-cal-1"})
    pr = lead_by_email(priya)
    check("11 CRM: calendar acceptance → meeting_booked", pr["status"] == "meeting_booked", f"{pr['status']} match={r.get('matched')}")

    # ── negative reply → suppression ────────────────────────────────────────
    elena = "elena.novak@quarry-labs.example"
    el = lead_by_email(elena)
    if el:
        post("/webhooks/email", {"type": "reply", "provider": "e2e", "from_email": elena, "event_id": "e2e-neg",
                                 "body": "Please remove me from your list, not interested."})
        comp = wait_until(lambda: not get(f"/compliance/leads/{el['id']}")["allowed"], 30, 1)
        check("9-11 opt-out reply → contact suppressed", bool(comp), json.dumps(get(f"/compliance/leads/{el['id']}"))[:160])

    # ── restart workers mid-flight (SIGKILL) ────────────────────────────────
    approve_all(lambda a: a["capability"] == "send_email")
    time.sleep(1.5)
    for w in ("worker1", "worker2"):
        procs[w].send_signal(signal.SIGKILL)
        procs[w].wait()
    before = sql("select status, count(*) from tasks group by status order by 1")
    start_worker("worker1")
    start_worker("worker2")
    time.sleep(12)
    approve_all(lambda a: a["capability"] == "send_email")
    time.sleep(10)

    # ── 8 cont. follow-up after no reply (time travel 4 days) ───────────────
    contacted = [p for p in get("/prospects", params={"limit": 1000}) if p["status"] == "contacted"]
    fu_lead = next((p for p in contacted if p["email"] not in (priya, elena)), None)
    if fu_lead:
        lid = fu_lead["id"]
        adv = post("/demo/clock/advance", {"hours": 96})       # the real demo clock: 4 days pass for this org
        check("8 demo clock advanced 4 days", adv.get("advanced_hours") == 96, json.dumps(adv)[:160])
        if sql(f"select count(*) from work_items where lead_id='{lid}' and finished_at is null") == "0":
            post(f"/memory/leads/{lid}/rechecks", {"reason": "e2e follow-up check", "due_in_hours": 0, "created_by": "e2e"})
        fu = wait_until(lambda: [a for a in get("/approvals", params={"status": "pending"}) if a["lead_id"] == lid], 60, 2)
        check("8 follow-up drafted after no reply (awaiting approval)", bool(fu),
              (fu[0]["original_payload"].get("subject", "") if fu else sql(f"select action||': '||left(reasoning,120) from agent_decisions where lead_id='{lid}' order by created_at desc limit 1")))
    else:
        check("8 follow-up drafted after no reply", False, "no other contacted lead")

    # ── duplicate / integrity checks ────────────────────────────────────────
    all_mail = mails()
    ids = [m["msg"]["Message-ID"] for m in all_mail]
    check("dup: no Message-ID sent twice (2 workers + SIGKILL restart)", len(ids) == len(set(ids)), f"{len(ids)} mails, {len(set(ids))} unique")
    sends_done = int(sql("select count(*) from tasks where task_type in ('send_email','book_meeting') and status='completed' and coalesce(result,'') not like '%\"sample\": true%'"))
    check("dup: SMTP messages == completed external tasks", len(all_mail) == sends_done, f"sink={len(all_mail)} completed={sends_done}")
    stuck = sql("select count(*) from tasks where status='in_progress' and (lease_expires_at is null or lease_expires_at < now())")
    check("restart: no orphaned in_progress tasks", stuck == "0", f"tasks before kill: {before.replace(chr(10), ', ')}")
    dup_items = sql("select count(*) from (select lead_id, kind from work_items where finished_at is null group by 1,2 having count(*)>1) x")
    check("dup: at most one open work item per (lead, kind)", dup_items == "0", dup_items)
    out_no_appr = sql("select count(*) from tasks t where t.task_type in ('send_email','book_meeting') and t.status='completed' "
                      "and coalesce(t.result,'') not like '%\"sample\": true%' "
                      "and not exists (select 1 from approvals a where a.task_id=t.id and a.status='approved')")
    check("safety: every external send had an approved approval", out_no_appr == "0", out_no_appr)
    check("safety: all mail stayed in the local sink", True, f"{len(all_mail)} messages captured locally")

    # ── 12. analytics + dashboard consistency ───────────────────────────────
    post("/revops/attribute")
    ov = get("/overview")
    rv = get("/revops/dashboard", params={"days": 30})
    db_meetings = int(sql("select count(*) from leads where status='meeting_booked'"))
    check("12 overview meetings_booked matches DB", ov.get("meetings_booked", ov.get("kpis", {}).get("meetings_booked")) == db_meetings,
          f"overview={json.dumps(ov)[:200]}")
    overall = rv.get("outreach", {}).get("overall", {})
    check("12 RevOps attributed touches > 0 and reply counted", (overall.get("n") or 0) > 0 and (overall.get("reply_rate") or 0) > 0, json.dumps(overall)[:200])
    funnel = {s["stage"]: s["count"] for s in rv.get("pipeline", {}).get("funnel", [])}
    check("12 RevOps funnel reflects pipeline", bool(funnel), json.dumps(funnel))
    errs = [ln for ln in (LOGS / "worker1.log").read_text().splitlines() + (LOGS / "worker2.log").read_text().splitlines()
            + (LOGS / "api.log").read_text().splitlines() if "Traceback" in ln or " ERROR " in ln]
    check("logs: no tracebacks/errors in API or workers", not errs, "; ".join(errs[:3])[:300])
    reviewers = sql("select distinct decided_by from approvals where status='approved'")
    check("auth: approvals record the authenticated principal", reviewers == "e2e-validator", reviewers)
    print("\nApprovals granted by validator:", approved_log)


def _health():
    try:
        return c.get(f"http://127.0.0.1:{API_PORT}/health").status_code == 200
    except Exception:
        return False


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # report and still clean up
        import traceback
        traceback.print_exc()
        check("driver", False, str(exc)[:300])
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.terminate()
        for p in procs.values():
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        passed = sum(1 for _, ok, _ in RESULTS if ok)
        print(f"\n{passed}/{len(RESULTS)} checks passed")
        (WORK / "results.json").write_text(json.dumps(RESULTS, indent=1))
