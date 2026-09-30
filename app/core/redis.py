"""
Redis client management.

Provides:
- A module-level Redis connection pool (created once on startup)
- A FastAPI dependency: get_redis()
- A health-check probe: check_redis_health()

Later modules (workers, rate-limiters, caches) import get_redis() or
use the pool directly.
"""

from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.exceptions import ConnectionError, RedisError

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Module-level pool — created lazily on first access via get_redis_pool()
_redis_pool: Redis | None = None


def get_redis_pool() -> Redis:
    """
    Return the shared Redis connection pool (creates it on first call).

    This is a connection pool, not a single connection. Each await on
    a Redis command borrows a connection and returns it automatically.
    """
    global _redis_pool  # noqa: PLW0603
    if _redis_pool is None:
        settings = get_settings()
        _redis_pool = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
        logger.info("Redis connection pool created: %s", settings.REDIS_URL)
    return _redis_pool


async def close_redis_pool() -> None:
    """Close the Redis connection pool gracefully (call on app shutdown)."""
    global _redis_pool  # noqa: PLW0603
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None
        logger.info("Redis connection pool closed")


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def get_redis() -> Any:  # noqa: ANN401
    """
    Yield a Redis client for use in route handlers.

    Usage::

        @router.get("/example")
        async def example(redis: Redis = Depends(get_redis)):
            await redis.set("key", "value")
    """
    return get_redis_pool()


# ── Health check ─────────────────────────────────────────────────────────────

async def check_redis_health() -> dict:
    """
    Ping Redis to verify connectivity.

    Returns a status dict compatible with the /health/redis endpoint.
    """
    try:
        client = get_redis_pool()
        pong = await client.ping()
        if pong:
            return {"status": "ok", "detail": "Redis reachable"}
        return {"status": "error", "detail": "Redis ping returned False"}
    except ConnectionError as exc:
        logger.error("Redis health check failed (connection): %s", exc)
        return {"status": "error", "detail": str(exc)}
    except RedisError as exc:
        logger.error("Redis health check failed: %s", exc)
        return {"status": "error", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected Redis error: %s", exc)
        return {"status": "error", "detail": str(exc)}
