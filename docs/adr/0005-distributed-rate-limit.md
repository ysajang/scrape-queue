# 5. Rate limit per domain in Redis, not per process

**Status:** accepted

## Context

The obvious limiter is a token bucket in the worker process. Celery also offers
`rate_limit` on a task, which is likewise per worker.

## Decision

A Lua token bucket in Redis, keyed by domain. `check-and-consume` is atomic in
one script; workers block until a token is available rather than failing.

## Consequences

The limit is a property of the deployment, not of a process. Running
`--scale worker=3` does not triple the load on the target. A unit test asserts
exactly this: two bucket clients sharing one budget, where the second is refused.

The visible effect is that adding workers does not make a single-domain job
faster. Measured: 12 pages of `federal_register` takes 6.1 seconds with one
worker and 6.1 seconds with three. Workers scale across domains and jobs, not
against one site's budget. The README states this rather than implying linear
scaling.

The Lua script is the part to be careful with: a non-atomic read-modify-write
would let concurrent workers each see enough tokens and all proceed.
