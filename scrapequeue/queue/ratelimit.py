"""Redis token buckets.

Two users of the same primitive:

  * per API key, to cap request rate at the control plane, and
  * per target domain, to cap outbound crawl rate across every worker.

The domain case is the one that matters. An in-process limiter caps one worker;
scale to three workers and the site sees three times the traffic. The bucket
state lives in Redis so the limit is a property of the deployment, not of a
process. The refill is a Lua script so check-and-consume is atomic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from redis import Redis

# KEYS[1] bucket hash, ARGV: rate, capacity, now, requested
_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local ts = tonumber(bucket[2])
if tokens == nil then
  tokens = capacity
  ts = now
end

local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * rate)

local allowed = 0
local wait = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
else
  wait = (requested - tokens) / rate
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, math.ceil(capacity / rate) + 60)
return {allowed, tostring(wait)}
"""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after: float


class TokenBucket:
    def __init__(self, redis: Redis, prefix: str = "sq:rl") -> None:
        self._redis = redis
        self._prefix = prefix
        self._script = redis.register_script(_TOKEN_BUCKET_LUA)

    def consume(
        self, name: str, *, rate_per_second: float, capacity: float | None = None, tokens: float = 1.0
    ) -> Decision:
        cap = capacity if capacity is not None else max(rate_per_second, 1.0)
        allowed, wait = self._script(
            keys=[f"{self._prefix}:{name}"],
            args=[rate_per_second, cap, time.time(), tokens],
        )
        return Decision(allowed=bool(int(allowed)), retry_after=float(wait))

    def wait_for_slot(
        self, name: str, *, rate_per_second: float, timeout: float = 60.0, tokens: float = 1.0
    ) -> float:
        """Block until a token is available. Returns seconds waited."""
        waited = 0.0
        while True:
            decision = self.consume(name, rate_per_second=rate_per_second, tokens=tokens)
            if decision.allowed:
                return waited
            delay = min(decision.retry_after, timeout - waited)
            if delay <= 0:
                raise TimeoutError(f"rate limit wait exceeded {timeout}s for {name}")
            time.sleep(delay)
            waited += delay
