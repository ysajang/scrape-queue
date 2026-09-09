"""Backpressure.

Accepting work the workers cannot drain converts a capacity problem into a
latency problem that is invisible until the queue is hours deep. Above the
configured depth, submission returns 429 with Retry-After and the caller keeps
its own backlog, where it belongs.
"""

from __future__ import annotations

from typing import cast

from fastapi import HTTPException, status
from redis import Redis

from scrapequeue.core.settings import get_settings


def queue_depth(redis: Redis, queue: str) -> int:
    """Redis transport stores each queue as a list keyed by the queue name."""
    return int(cast(int, redis.llen(queue)))


def assert_capacity(redis: Redis, queue: str) -> int:
    limit = get_settings().queue_depth_limit
    depth = queue_depth(redis, queue)
    if depth >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"queue {queue} is at capacity ({depth}/{limit})",
            headers={"Retry-After": "30"},
        )
    return depth
