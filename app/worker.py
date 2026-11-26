"""
Oxygen background worker.

    python -m app.worker            # run continuously (tick every autonomy.tick_interval_s)
    python -m app.worker --once     # run a single tick and exit

Several workers can run at once: work items and tasks are claimed with
FOR UPDATE SKIP LOCKED and leases, and the pause switch, provider health and
rate limits are shared through Redis.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket

from app.core.gtm_config import get_gtm_config
from app.core.logging import configure_logging, get_logger
from app.services.autonomy import tick

logger = get_logger("app.worker")


async def run(once: bool) -> None:
    name = f"{socket.gethostname()}:{os.getpid()}"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    logger.info("Oxygen worker %s starting", name)
    while not stop.is_set():
        try:
            report = await tick(worker=name)
            if report["paused"]:
                logger.info("Autonomy paused; idle")
            elif report["cycles"] or report["tasks_executed"] or report["reactions"]:
                logger.info("Tick: %d reactions, %d cycles, %d tasks, %d recovered, %d capped",
                            report["reactions"], len(report["cycles"]), report["tasks_executed"],
                            report["recovered"], report["capped"])
        except Exception:  # noqa: BLE001 - keep the worker alive; each tick is independent
            logger.exception("Worker tick failed")
        if once:
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=get_gtm_config().autonomy.tick_interval_s)
        except asyncio.TimeoutError:
            pass
    logger.info("Oxygen worker %s stopped", name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Oxygen autonomous worker")
    parser.add_argument("--once", action="store_true", help="run a single tick and exit")
    args = parser.parse_args()
    configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
    asyncio.run(run(args.once))


if __name__ == "__main__":
    main()
