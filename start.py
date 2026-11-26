#!/usr/bin/env python3
"""
Oxygen — one-command start (macOS, Linux, Windows).

    python start.py                 # checks, migrations, dashboard build, API + worker, opens the browser
    python start.py --profile development   # real providers/LLMs where keys exist, mocks for the rest
    python start.py --no-browser --port 8080
    python start.py --no-worker     # API + dashboard only (run ticks from the Control Centre)

Profile choice when --profile is not given: $GTM_PROFILE if set; otherwise
"development" when OPENAI_API_KEY or ANTHROPIC_API_KEY is set, else "demo"
(scripted demo AI and demo providers, clearly labelled in the dashboard; no API
keys needed and nothing leaves the machine). The demo profile also loads the
demo dataset: two fictional organisations with admin/operator/approver users,
a campaign each and their target accounts. Sign in by picking a demo user.

Run it with the project's virtualenv Python (e.g. .venv/bin/python start.py on
macOS/Linux or .venv\\Scripts\\python start.py on Windows).
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
IS_WIN = os.name == "nt"


def say(msg: str) -> None:
    print(f"  {msg}", flush=True)


def fail(msg: str, hint: str = "") -> None:
    print(f"\n  ✘ {msg}")
    if hint:
        print(f"    {hint}")
    sys.exit(1)


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        example = ROOT / ".env.example"
        if example.exists():
            shutil.copy(example, env)
            say("Created .env from .env.example (edit it to add API keys).")
    try:
        from dotenv import load_dotenv
        load_dotenv(env, override=False)
    except ImportError:
        fail("Python dependencies are missing.", "Run: pip install -r requirements.txt (inside the virtualenv)")


def reachable(url: str, default_port: int) -> bool:
    u = urlparse(url.replace("+asyncpg", ""))
    try:
        with socket.create_connection((u.hostname or "localhost", u.port or default_port), timeout=2):
            return True
    except OSError:
        return False


def check_services() -> None:
    db = os.environ.get("DATABASE_URL", "postgresql://gtm_user:gtm_password@localhost:5432/gtm_db")
    redis = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    if not reachable(db, 5432):
        fail("PostgreSQL is not reachable.",
             "macOS: brew services start postgresql@16 · Windows: start the PostgreSQL service · or: docker compose up -d db")
    say("✔ PostgreSQL reachable")
    if not reachable(redis, 6379):
        fail("Redis is not reachable.",
             "macOS: brew services start redis · Windows: Memurai or WSL Redis · or: docker compose up -d redis")
    say("✔ Redis reachable")


def migrate() -> None:
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        fail("Database migration failed.", (r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout) else "")
    say("✔ Database schema up to date")


def build_dashboard(force: bool) -> None:
    dist = FRONTEND / "dist" / "index.html"
    newest_src = max((p.stat().st_mtime for p in (FRONTEND / "src").rglob("*") if p.is_file()), default=0)
    if dist.exists() and not force and dist.stat().st_mtime >= newest_src:
        say("✔ Dashboard already built")
        return
    npm = shutil.which("npm.cmd" if IS_WIN else "npm")
    if npm is None:
        if dist.exists():
            say("! npm not found; using the existing dashboard build")
            return
        fail("The dashboard needs building but npm (Node.js 18+) was not found.", "Install Node.js from https://nodejs.org")
    say("… building the dashboard (first run takes a minute)")
    if not (FRONTEND / "node_modules").exists():
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=FRONTEND, check=True)
    subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True, stdout=subprocess.DEVNULL)
    say("✔ Dashboard built")


def choose_profile(arg: str | None) -> str:
    if arg:
        return arg
    if os.environ.get("GTM_PROFILE"):
        return os.environ["GTM_PROFILE"]
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
        return "development"
    return "demo"


def wait_http(url: str, seconds: float = 40) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return True
        except OSError:
            time.sleep(0.5)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Start Oxygen (API, worker and dashboard).")
    ap.add_argument("--profile", help="config profile: demo | development | production | <custom>")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-worker", action="store_true", help="don't start the background worker")
    ap.add_argument("--rebuild", action="store_true", help="force a dashboard rebuild")
    ap.add_argument("--no-seed", action="store_true", help="don't load the demo dataset (demo profile)")
    args = ap.parse_args()

    print("\n  Oxygen — Autonomous AI Sales OS\n")
    load_env()
    profile = choose_profile(args.profile)
    os.environ["GTM_PROFILE"] = profile
    say(f"Profile: {profile}" + ("  (scripted demo AI — add an LLM key for real AI)" if profile == "demo" else ""))
    check_services()
    migrate()
    build_dashboard(args.rebuild)

    env = {**os.environ, "GTM_PROFILE": profile, "PYTHONUNBUFFERED": "1"}
    procs: list[subprocess.Popen] = []
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            fail(f"Port {args.port} is already in use (is Oxygen already running?).", "Stop it or use --port <other>.")
    # Live-update (SSE) connections stay open; cap graceful shutdown so Ctrl+C always exits.
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                            "--port", str(args.port), "--timeout-graceful-shutdown", "3"], cwd=ROOT, env=env)
    procs.append(api)
    base = f"http://127.0.0.1:{args.port}"
    if not wait_http(f"{base}/health"):
        api.terminate()
        fail("The API did not start.", "See the output above.")
    say(f"✔ API running on {base} (docs: {base}/docs)")

    if profile == "demo" and not args.no_seed:
        # Load the demo dataset in-process (idempotent): two fictional organisations, users per role,
        # campaigns and target accounts. Nothing here contacts any external service.
        seed = subprocess.run([sys.executable, "-c", "import asyncio; from app.pipeline import demo_control; "
                               "r = asyncio.run(demo_control.load()); print(sum(x['accounts'] for x in r['loaded']))"],
                              cwd=ROOT, env=env, capture_output=True, text=True)
        if seed.returncode == 0:
            say(f"✔ Demo dataset loaded (2 organisations, {seed.stdout.strip().splitlines()[-1]} target accounts, "
                "users per role) — sign in from the dashboard")
        else:
            say("! Could not load the demo dataset: " + (seed.stderr.strip().splitlines() or ["unknown error"])[-1])

    if not args.no_worker:
        procs.append(subprocess.Popen([sys.executable, "-m", "app.worker"], cwd=ROOT, env=env))
        say("✔ Background worker running (pause it any time from the Control Centre)")

    def _stop(signum, frame):  # Ctrl+C, SIGTERM, console close: always stop the children
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    if hasattr(signal, "SIGBREAK"):  # Windows Ctrl+Break
        signal.signal(signal.SIGBREAK, _stop)

    say(f"\n  Dashboard: {base}/\n  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        webbrowser.open(f"{base}/")
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        say("A process exited; shutting down.")
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        print("  Stopped.\n")


if __name__ == "__main__":
    main()
