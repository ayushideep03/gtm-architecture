"""
Runtime signals used by provider selection: health, rate limits, reliability.

Health (failure cooldowns) and rate limits are shared across all workers and
API processes through Redis (``runtime.backend: redis``). If Redis is
unreachable the trackers fall back to per-process state (configurable), so an
outage degrades limits to per-process instead of halting execution.
Reliability statistics are read from the persisted event log, so they survive
restarts and reflect real outcomes.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.gtm_config import RateLimitConfig
from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Interfaces ───────────────────────────────────────────────────────────────

class HealthTracker(Protocol):
    async def record_success(self, name: str) -> None: ...
    async def record_failure(self, name: str, error_code: Optional[str], threshold: int, cooldown_s: float) -> None: ...
    async def cooling_down(self, name: str) -> bool: ...
    async def state(self, name: str) -> dict: ...


class RateLimiter(Protocol):
    async def has_capacity(self, name: str, cfg: RateLimitConfig) -> bool: ...
    async def try_acquire(self, name: str, cfg: RateLimitConfig) -> bool: ...
    async def block(self, name: str, seconds: float) -> None: ...


# ── In-memory (single process; tests and fallback) ───────────────────────────

@dataclass
class HealthState:
    consecutive_failures: int = 0
    last_failure_at: Optional[float] = None
    last_error_code: Optional[str] = None
    cooldown_until: float = 0.0


class InMemoryProviderHealth:
    """Consecutive infrastructure failures; a provider cools down after a threshold."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._state: dict[str, HealthState] = defaultdict(HealthState)

    async def record_success(self, name: str) -> None:
        self._state[name] = HealthState()

    async def record_failure(self, name: str, error_code: Optional[str], threshold: int, cooldown_s: float) -> None:
        st = self._state[name]
        st.consecutive_failures += 1
        st.last_failure_at = self._clock()
        st.last_error_code = error_code
        if st.consecutive_failures >= threshold:
            st.cooldown_until = self._clock() + cooldown_s
            st.consecutive_failures = 0
            logger.warning("Provider %r cooling down for %.0fs (last error %s)", name, cooldown_s, error_code)

    async def cooling_down(self, name: str) -> bool:
        return self._state[name].cooldown_until > self._clock()

    async def state(self, name: str) -> dict:
        st = self._state[name]
        return {"consecutive_failures": st.consecutive_failures,
                "cooldown_remaining_s": max(0.0, st.cooldown_until - self._clock()),
                "last_error_code": st.last_error_code}


class InMemoryRateLimiter:
    """Sliding-window limiter per provider, plus explicit blocks (e.g. vendor Retry-After)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}

    def _trim(self, name: str, window: float) -> deque[float]:
        q = self._calls[name]
        cutoff = self._clock() - window
        while q and q[0] <= cutoff:
            q.popleft()
        return q

    async def has_capacity(self, name: str, cfg: RateLimitConfig) -> bool:
        if self._blocked_until.get(name, 0.0) > self._clock():
            return False
        if cfg.requests is None:
            return True
        return len(self._trim(name, cfg.per_seconds)) < cfg.requests

    async def try_acquire(self, name: str, cfg: RateLimitConfig) -> bool:
        if not await self.has_capacity(name, cfg):
            return False
        if cfg.requests is not None:
            self._calls[name].append(self._clock())
        return True

    async def block(self, name: str, seconds: float) -> None:
        self._blocked_until[name] = max(self._blocked_until.get(name, 0.0), self._clock() + seconds)


# Backwards-compatible names
ProviderHealth = InMemoryProviderHealth


# ── Redis (shared across workers and API processes) ──────────────────────────

_SLIDING_WINDOW = """
local key = KEYS[1]
local blocked = KEYS[2]
local window_ms = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local acquire = ARGV[3]
if redis.call('EXISTS', blocked) == 1 then return 0 end
if limit < 0 then return 1 end
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', key, 0, now - window_ms)
if redis.call('ZCARD', key) >= limit then return 0 end
if acquire == '1' then
  redis.call('ZADD', key, now, now .. '-' .. t[2] .. '-' .. math.random(1000000))
  redis.call('PEXPIRE', key, window_ms)
end
return 1
"""


class _RedisBacked:
    def __init__(self, prefix: str, fallback, allow_fallback: bool, client_factory=None) -> None:
        self._prefix = prefix
        self._fallback = fallback
        self._allow_fallback = allow_fallback
        self._client_factory = client_factory
        self._warned = False

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory()
        from app.core.redis import get_loop_redis
        return get_loop_redis()

    def _key(self, name: str, suffix: str) -> str:
        return f"{self._prefix}:provider:{name}:{suffix}"

    def _degrade(self, exc: Exception) -> None:
        if not self._allow_fallback:
            raise exc
        if not self._warned:
            logger.warning("Redis runtime state unavailable (%s); falling back to per-process state", exc)
            self._warned = True


class RedisProviderHealth(_RedisBacked):
    def __init__(self, prefix: str, allow_fallback: bool = True, client_factory=None) -> None:
        super().__init__(prefix, InMemoryProviderHealth(), allow_fallback, client_factory)

    async def record_success(self, name: str) -> None:
        try:
            await self._client().delete(self._key(name, "failures"), self._key(name, "last_error"))
        except RedisError as exc:
            self._degrade(exc)
            await self._fallback.record_success(name)

    async def record_failure(self, name: str, error_code: Optional[str], threshold: int, cooldown_s: float) -> None:
        try:
            client = self._client()
            failures_key = self._key(name, "failures")
            async with client.pipeline(transaction=True) as pipe:
                pipe.incr(failures_key)
                pipe.expire(failures_key, max(int(cooldown_s * 10), 60))
                pipe.set(self._key(name, "last_error"), error_code or "unknown", ex=max(int(cooldown_s * 10), 60))
                count = (await pipe.execute())[0]
            if int(count) >= threshold:
                async with client.pipeline(transaction=True) as pipe:
                    pipe.set(self._key(name, "cooldown"), error_code or "1", px=max(int(cooldown_s * 1000), 1))
                    pipe.delete(failures_key)
                    await pipe.execute()
                logger.warning("Provider %r cooling down for %.0fs across all workers (last error %s)",
                               name, cooldown_s, error_code)
        except RedisError as exc:
            self._degrade(exc)
            await self._fallback.record_failure(name, error_code, threshold, cooldown_s)

    async def cooling_down(self, name: str) -> bool:
        try:
            return bool(await self._client().exists(self._key(name, "cooldown")))
        except RedisError as exc:
            self._degrade(exc)
            return await self._fallback.cooling_down(name)

    async def state(self, name: str) -> dict:
        try:
            client = self._client()
            failures = await client.get(self._key(name, "failures"))
            pttl = await client.pttl(self._key(name, "cooldown"))
            last = await client.get(self._key(name, "last_error"))
            return {"consecutive_failures": int(failures or 0),
                    "cooldown_remaining_s": max(0.0, (pttl or 0) / 1000.0),
                    "last_error_code": last}
        except RedisError as exc:
            self._degrade(exc)
            return await self._fallback.state(name)


class RedisRateLimiter(_RedisBacked):
    def __init__(self, prefix: str, allow_fallback: bool = True, client_factory=None) -> None:
        super().__init__(prefix, InMemoryRateLimiter(), allow_fallback, client_factory)

    async def _check(self, name: str, cfg: RateLimitConfig, acquire: bool) -> bool:
        limit = -1 if cfg.requests is None else int(cfg.requests)
        try:
            ok = await self._client().eval(
                _SLIDING_WINDOW, 2, self._key(name, "rl"), self._key(name, "blocked"),
                int(cfg.per_seconds * 1000), limit, "1" if acquire else "0",
            )
            return bool(int(ok))
        except RedisError as exc:
            self._degrade(exc)
            return await (self._fallback.try_acquire(name, cfg) if acquire else self._fallback.has_capacity(name, cfg))

    async def has_capacity(self, name: str, cfg: RateLimitConfig) -> bool:
        return await self._check(name, cfg, acquire=False)

    async def try_acquire(self, name: str, cfg: RateLimitConfig) -> bool:
        return await self._check(name, cfg, acquire=True)

    async def block(self, name: str, seconds: float) -> None:
        try:
            await self._client().set(self._key(name, "blocked"), "1", px=max(int(seconds * 1000), 1))
        except RedisError as exc:
            self._degrade(exc)
            await self._fallback.block(name, seconds)


def build_runtime_state(cfg) -> tuple[HealthTracker, RateLimiter]:
    """Create health + rate-limit state for the configured backend."""
    if cfg.runtime.backend == "redis":
        prefix = cfg.runtime.key_prefix
        fallback = cfg.runtime.redis_fallback_to_memory
        return RedisProviderHealth(prefix, fallback), RedisRateLimiter(prefix, fallback)
    return InMemoryProviderHealth(), InMemoryRateLimiter()


# ── Reliability statistics ───────────────────────────────────────────────────

@dataclass
class ProviderStat:
    successes: int = 0
    failures: int = 0
    avg_latency_s: Optional[float] = None

    @property
    def total(self) -> int:
        return self.successes + self.failures


@dataclass
class _CacheEntry:
    expires_at: float
    data: dict[str, ProviderStat] = field(default_factory=dict)


class EventProviderStats:
    """
    Per-provider success/failure counts and latency for a capability, computed
    from task_completed / task_failed events in the configured window.
    """

    _SQL = text(
        """
        SELECT payload::jsonb ->> 'provider'                           AS provider,
               event_type                                              AS event_type,
               count(*)                                                AS n,
               avg(NULLIF(payload::jsonb ->> 'duration_ms', '')::float) AS avg_ms
        FROM events
        WHERE event_type IN ('task_completed', 'task_failed')
          AND occurred_at >= now() - make_interval(days => :days)
          AND (payload::jsonb ->> 'capability' = :capability
               OR payload::jsonb ->> 'task_type' = ANY(:names))
        GROUP BY 1, 2
        """
    )

    def __init__(self, ttl_s: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_s
        self._clock = clock
        self._cache: dict[tuple[str, int], _CacheEntry] = {}

    def invalidate(self) -> None:
        self._cache.clear()

    async def get(
        self, session: Optional[AsyncSession], capability: str, names: list[str], window_days: int
    ) -> dict[str, ProviderStat]:
        key = (capability, window_days)
        cached = self._cache.get(key)
        if cached and cached.expires_at > self._clock():
            return cached.data
        if session is None:
            return {}
        data: dict[str, ProviderStat] = {}
        try:
            async with session.begin_nested():  # isolate failures from the caller's transaction
                rows = (await session.execute(
                    self._SQL, {"days": window_days, "capability": capability, "names": names}
                )).all()
        except Exception as exc:  # noqa: BLE001 - stats are advisory; never block execution
            logger.warning("Provider stats unavailable for %r: %s", capability, exc)
            return {}
        for provider, event_type, n, avg_ms in rows:
            if not provider:
                continue
            stat = data.setdefault(provider, ProviderStat())
            if event_type == "task_completed":
                stat.successes += int(n)
                if avg_ms is not None:
                    stat.avg_latency_s = float(avg_ms) / 1000.0
            else:
                stat.failures += int(n)
        self._cache[key] = _CacheEntry(expires_at=self._clock() + self._ttl, data=data)
        return data


class StaticProviderStats:
    """Fixed stats (tests, simulations)."""

    def __init__(self, data: Optional[dict[str, dict[str, ProviderStat]]] = None) -> None:
        self._data = data or {}

    async def get(self, session, capability, names, window_days) -> dict[str, ProviderStat]:
        return self._data.get(capability, {})
