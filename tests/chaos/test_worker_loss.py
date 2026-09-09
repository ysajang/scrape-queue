"""Executable proof of the delivery guarantee, and of its cost.

A worker is killed with SIGKILL mid-task, which is what an OOM kill or a lost
node looks like. With task_acks_late and task_reject_on_worker_lost the message
is not gone, and a fresh worker runs it to completion.

The cost is specific to the Redis transport and worth stating plainly. Redis
has no broker-side acknowledgement, so a message held by a process that died
without restoring it stays invisible until two things happen: visibility_timeout
expires, and a live worker runs its restore sweep. Kombu runs that sweep on a
10 second timer but acts on every tenth call, so the sweep interval is about 100
seconds regardless of how short the timeout is. Measured recovery here was 100
seconds with a 15 second timeout.

The operational consequence: recovery after a hard kill is measured in minutes,
not seconds, and shrinking visibility_timeout alone does not change that. If a
job must recover faster, the API's retry endpoint is the intended path.
See docs/adr/0007-redis-visibility-timeout.md.

Marked chaos: it starts and kills processes, so it is not in the default suite.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid

import pytest
from redis import Redis

from scrapequeue.core.settings import get_settings
from scrapequeue.queue.selftest import FINISHED_KEY, STARTED_KEY, slow_marker

pytestmark = pytest.mark.chaos

WORKER_ARGS = [
    "celery",
    "-A",
    "scrapequeue.queue.celery_app",
    "worker",
    "-Q",
    "default",
    "-c",
    "1",
    "--prefetch-multiplier",
    "1",
    "--loglevel",
    "warning",
]


VISIBILITY_TIMEOUT = 15
TASK_SECONDS = 8


def start_worker(name: str) -> subprocess.Popen:
    env = {**os.environ, "VISIBILITY_TIMEOUT_SECONDS": str(VISIBILITY_TIMEOUT)}
    return subprocess.Popen(
        [sys.executable, "-m", *WORKER_ARGS, "-n", f"{name}@%h"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def kill_group(process: subprocess.Popen) -> None:
    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    process.wait(timeout=30)


def wait_for(redis: Redis, key: str, timeout: float) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = redis.get(key)
        if value:
            return str(value)
        time.sleep(0.5)
    return None


def test_task_survives_worker_kill():
    redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    token = uuid.uuid4().hex
    started, finished = f"{STARTED_KEY}:{token}", f"{FINISHED_KEY}:{token}"

    victim = start_worker("victim")
    try:
        slow_marker.apply_async(kwargs={"token": token, "seconds": TASK_SECONDS}, queue="default")
        first_pid = wait_for(redis, started, timeout=60)
        assert first_pid, "the first worker never picked the task up"

        kill_group(victim)
        assert redis.get(finished) is None, "the task should not have finished before the kill"
    finally:
        if victim.poll() is None:
            kill_group(victim)

    survivor = start_worker("survivor")
    try:
        # Sweep interval (~100s) dominates; allow generous headroom.
        second_pid = wait_for(redis, finished, timeout=240)
        assert second_pid, "the task was lost when its worker died"
        assert second_pid != first_pid, "a different worker should have completed it"
        assert int(redis.get(f"sq:selftest:attempts:{token}") or 0) >= 2
    finally:
        kill_group(survivor)
