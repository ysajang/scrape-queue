# Architecture

## Why one task per page

A job could be one Celery task that loops over pages. That design fails in four
ways that only appear under load:

1. a failure on page 40 loses pages 1 to 39, or forces the whole job to rerun;
2. a retry re-fetches everything, which is exactly the behaviour a site's
   operator would consider abusive;
3. the job cannot use more than one worker, so scaling does nothing;
4. a worker that dies takes an arbitrary amount of completed work with it.

One task per page fixes all four, at the cost of needing an assembly step. That
step is the chord callback `assemble_job`, and it is the only place that sees
the whole result set.

The requirement this places on a target is that its pages are addressable by
URL. Click-driven pagination cannot satisfy it: page 7 only exists after six
clicks in one browser session, so it cannot be retried or resumed
independently. `pipeline.pageurl` rejects such targets rather than supporting
them badly.

## Components

| Component | Queue | Concurrency | Notes |
| --- | --- | --- | --- |
| `api` | n/a | uvicorn workers | validates, persists, enqueues; never fetches |
| `worker-default` | `export`, `parse`, `default` | 4 | HTTP fetches, assembly, exports |
| `worker-browser` | `browser` | 1 | one Chromium per process, one context per job |
| `beat` | n/a | 1 leader | RedBeat schedule in Redis, lock prevents double-fire |

Browser work is isolated on its own queue and its own deployment because it has
a completely different resource profile: seconds per page instead of
milliseconds, hundreds of megabytes of RSS, and a crash mode (a hung renderer)
that must not take HTTP fetches down with it.

## Storage

| Store | Holds | Why |
| --- | --- | --- |
| PostgreSQL | jobs, pages, failures, dead letters, snapshots, drift baselines | the checkpoint must outlive a Redis flush |
| Redis | broker, rate-limit buckets, circuit state, RedBeat schedule | shared, fast, expiring state |
| S3 / MinIO | per-page row snapshots and final results | keeps large payloads out of the database and the API process |

Per-page rows are written to object storage rather than the database because
they are intermediate: assembly reads them once, and the job's own result is
the artefact worth keeping.

## Job states

```
PENDING ──> RUNNING ──> SUCCEEDED
              │  │
              │  ├────> PARTIAL      some pages failed permanently
              │  ├────> SUSPECT      finished, but the drift detector rejects the row counts
              │  ├────> FAILED
              │  ├────> CANCELLED
              │  └────> DEAD_LETTER  poison pill, parked for an operator
              └────> PAUSED ──> RUNNING     circuit open on the target domain
```

Terminal is final. A redelivered task for a finished job can only be a no-op,
and re-running is an explicit new job via `POST /jobs/{id}/retry`. Illegal
transitions raise rather than being written, because a job that goes
`SUCCEEDED -> RUNNING` means something slipped past the checkpoint logic and
silence would hide it.

`SUSPECT` exists because "finished with zero rows" and "finished correctly" look
identical from the outside. An empty CSV delivered as success is worse than an
error.

## Failure handling

| Failure | Detected by | Response |
| --- | --- | --- |
| Timeout, 5xx, 429 | `TransientFetchError` | retry with exponential backoff and jitter, up to 5 times |
| 404, robots refusal | `PermanentFetchError` | recorded in `failed_pages`, never retried |
| Domain down or blocking | circuit breaker | domain opens; one probe per cooling window |
| Worker killed | `acks_late` + `reject_on_worker_lost` | message redelivered once visible again |
| Repeated worker death | `lost_worker_count` | dead-letter queue past the threshold |
| Markup changed | drift baseline | job finishes `SUSPECT`, output kept and flagged |
| Caller retries a submission | `Idempotency-Key` | same job returned; 409 if the body differs |

## Known limits

- Job-level counters (`rows`, `pages_done`) are accumulated on the job row with
  SQL expressions. Correct under concurrency, but every page task writes to the
  same row; a job with thousands of pages would want the counts derived at read
  time instead.
- Recovery from a hard worker kill takes about 100 seconds on the Redis
  transport. See [ADR 0007](adr/0007-redis-visibility-timeout.md).
- Chord assembly holds the full result set in memory. Fine for the page counts
  this service accepts (1000 max), not for a million-row export.
