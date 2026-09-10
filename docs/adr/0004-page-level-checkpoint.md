# 4. Checkpoint per page, in the database

**Status:** accepted

## Context

A job that fails at page 40 of 50 has to decide what the next attempt does.
Options considered: restart from page 1, keep progress in Redis, keep progress
in the database.

## Decision

`job_pages` holds one row per completed page with a unique constraint on
`(job_id, page)`. Resume computes the set difference against the requested
range. Redis was rejected for this: an evicted or flushed key silently turns a
resume into a full re-crawl.

## Consequences

The unique constraint does double duty. It is the checkpoint, and it makes a
redelivered page task a no-op: the insert conflicts, the task returns
`duplicate: true`, and the job counters are not touched. Both behaviours are
covered by integration tests.

It also forces pages to be addressable by URL, which rules out click-driven
pagination. That is a real limit on which sites this service accepts, and it is
enforced in the loader rather than discovered in production.
