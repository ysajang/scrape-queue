# 3. RedBeat instead of the default Beat scheduler

**Status:** accepted

## Context

Celery's default scheduler keeps its state in a local file. Two Beat processes,
which is what a rolling deploy produces for a few seconds, both fire every due
entry.

## Decision

Use `redbeat.RedBeatScheduler` with the schedule in Redis and a lock
(`redbeat_lock_timeout`). Load the schedule from `config/schedule.yml`.

## Consequences

Two Beat instances are safe: the one without the lock waits. A schedule change
is a config edit and a restart, not a code change, and entries can be paused
with `enabled: false` while remaining documented.

Beat does not crawl. It submits a job through the same path the API uses, so a
scheduled run gets the same validation, allowlist, circuit breaker and rate
limits, and appears in the same tables as a manual one.

The cost is a dependency on Redis for the schedule. Acceptable: Redis is already
required as the broker.
