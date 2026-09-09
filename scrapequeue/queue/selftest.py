"""A task that exists only to prove the delivery guarantees.

The chaos test kills a worker in the middle of this task and expects another
worker to run it to completion. That behaviour comes from task_acks_late plus
task_reject_on_worker_lost, and it is the kind of claim that is worth an
executable proof rather than a sentence in a README. The runbook uses the same
task to verify a deployment after a change to the broker settings.
"""

from __future__ import annotations

import os
import time

from redis import Redis

from scrapequeue.core.settings import get_settings
from scrapequeue.queue.celery_app import app as celery_app

STARTED_KEY = "sq:selftest:started"
FINISHED_KEY = "sq:selftest:finished"
ATTEMPTS_KEY = "sq:selftest:attempts"


def _redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


@celery_app.task(name="scrapequeue.queue.selftest.slow_marker", acks_late=True)
def slow_marker(token: str, seconds: float = 10.0) -> dict[str, object]:
    redis = _redis()
    attempts = redis.incr(f"{ATTEMPTS_KEY}:{token}")
    redis.set(f"{STARTED_KEY}:{token}", os.getpid(), ex=600)
    time.sleep(seconds)
    redis.set(f"{FINISHED_KEY}:{token}", os.getpid(), ex=600)
    return {"token": token, "attempts": attempts, "pid": os.getpid()}
