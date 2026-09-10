# 1. Postgres for job state, Redis for transport only

**Status:** accepted

## Context

Celery ships a Redis result backend. Using it for job state would have removed
Postgres from the stack entirely.

## Decision

Job state, page checkpoints, failures and snapshots live in Postgres. Redis is
the broker, the rate-limit store and the circuit state, all of which are
allowed to be lost.

## Consequences

The checkpoint survives a Redis flush, an eviction policy, and a broker restart.
Losing it would not lose data but would turn every resume into a full re-crawl
of a site we promised to be gentle with, which is a worse failure than it looks.

`failed_pages` and `dead_letters` are queryable with SQL, which is what makes
the operator endpoints (`/failures`, retry) and the runbooks possible.

The cost is a second stateful service in the compose file and a migration step
in the deploy. Accepted: an operator needs to answer "what happened to job X
last Tuesday" and a result backend with a TTL cannot.
