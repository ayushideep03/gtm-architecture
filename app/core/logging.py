"""
Structured application logging.

Provides:
- JSON-structured logs in production
- Human-readable logs in development
- Request-scoped context (request_id, method, path, status, duration)
- A get_logger() helper for agents, workers, and integrations to import
"""

import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

# ── Context variables ─────────────────────────────────────────────────────────
# These are set per-request by the logging middleware and available throughout
# the call-stack without passing them explicitly.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
request_path_var: ContextVar[str] = ContextVar("request_path", default="")


# ── Custom formatter ──────────────────────────────────────────────────────────

class StructuredFormatter(logging.Formatter):
    """
    Emit JSON-ish structured log records suitable for log aggregators.

    In development mode we fall back to a more readable format.
    """

    _DEV_FMT = (
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )

    def __init__(self, development: bool = True) -> None:
        super().__init__()
        self.development = development
        if development:
            self._fallback = logging.Formatter(self._DEV_FMT, datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        if self.development:
            return self._fallback.format(record)

        import json

        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(""),
            "path": request_path_var.get(""),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str = "INFO", development: bool = True) -> None:
    """
    Bootstrap root logger. Call once at application start.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        development: Use human-readable format when True, JSON when False.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter(development=development))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger.

    Usage (in any module, agent, worker, or integration)::

        from app.core.logging import get_logger
        logger = get_logger(__name__)
        logger.info("doing something", extra={"lead_id": lead_id})
    """
    return logging.getLogger(name)


# ── FastAPI middleware ────────────────────────────────────────────────────────

async def logging_middleware(request: Any, call_next: Any) -> Any:  # noqa: ANN401
    """
    ASGI middleware that:
    - Assigns a unique request_id
    - Sets context vars so all downstream log calls include request metadata
    - Logs method, path, status, and duration after the response
    """
    from starlette.requests import Request  # local import to avoid circular deps

    req: Request = request
    rid = str(uuid.uuid4())
    token_id = request_id_var.set(rid)
    token_path = request_path_var.set(req.url.path)

    logger = get_logger("gtm.request")
    start = time.perf_counter()

    response = await call_next(request)

    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    logger.info(
        "%s %s -> %s (%.2f ms)",
        req.method,
        req.url.path,
        response.status_code,
        duration_ms,
        extra={
            "request_id": rid,
            "method": req.method,
            "path": req.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
        },
    )

    response.headers["X-Request-ID"] = rid
    request_id_var.reset(token_id)
    request_path_var.reset(token_path)
    return response
