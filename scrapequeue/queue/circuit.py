"""Per-domain circuit breaker.

Retrying into a site that is down, rate-limiting us, or blocking us is worse
than useless: it burns worker capacity and makes the block harder to lift. After
N consecutive failures the domain opens, and jobs for it are parked rather than
retried. After a cooling period one probe is allowed through (half-open); it
either closes the circuit or reopens it.

State lives in Redis so every worker sees the same circuit.
"""

from __future__ import annotations

from enum import IntEnum
from typing import cast
from urllib.parse import urlparse

from redis import Redis

from scrapequeue.core.settings import get_settings


class CircuitState(IntEnum):
    CLOSED = 0
    HALF_OPEN = 1
    OPEN = 2


class CircuitOpen(RuntimeError):
    def __init__(self, domain: str, ttl: int) -> None:
        super().__init__(f"circuit open for {domain}, retry in {ttl}s")
        self.domain = domain
        self.ttl = ttl


def domain_of(url: str) -> str:
    return (urlparse(url).hostname or "unknown").lower()


class CircuitBreaker:
    def __init__(self, redis: Redis, prefix: str = "sq:cb") -> None:
        self._redis = redis
        self._prefix = prefix
        settings = get_settings()
        self._threshold = settings.circuit_failure_threshold
        self._open_seconds = settings.circuit_open_seconds

    def _keys(self, domain: str) -> tuple[str, str, str]:
        return (
            f"{self._prefix}:{domain}:fails",
            f"{self._prefix}:{domain}:open",
            f"{self._prefix}:{domain}:probe",
        )

    def state(self, domain: str) -> tuple[CircuitState, int]:
        _, open_key, probe_key = self._keys(domain)
        ttl = int(cast(int, self._redis.ttl(open_key)))
        if ttl < 0:
            return CircuitState.CLOSED, 0
        # One probe per open window: the first caller to claim the probe key
        # gets through, everyone else keeps seeing OPEN.
        if self._redis.set(probe_key, "1", nx=True, ex=max(ttl, 1)):
            return CircuitState.HALF_OPEN, ttl
        return CircuitState.OPEN, ttl

    def assert_closed(self, url: str) -> CircuitState:
        domain = domain_of(url)
        state, ttl = self.state(domain)
        if state is CircuitState.OPEN:
            raise CircuitOpen(domain, ttl)
        return state

    def record_success(self, url: str) -> None:
        domain = domain_of(url)
        fails_key, open_key, probe_key = self._keys(domain)
        self._redis.delete(fails_key, open_key, probe_key)

    def record_failure(self, url: str) -> CircuitState:
        domain = domain_of(url)
        fails_key, open_key, _ = self._keys(domain)
        fails = int(cast(int, self._redis.incr(fails_key)))
        self._redis.expire(fails_key, self._open_seconds)
        if fails >= self._threshold:
            self._redis.set(open_key, "1", ex=self._open_seconds)
            return CircuitState.OPEN
        return CircuitState.CLOSED
