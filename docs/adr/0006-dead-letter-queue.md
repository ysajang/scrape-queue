# 6. A dead-letter queue for poison pills

**Status:** accepted

## Context

`acks_late` plus `reject_on_worker_lost` guarantees that a task whose worker
died is redelivered. If the task is what killed the worker, that guarantee turns
into a loop that kills every worker in turn.

## Decision

Count redeliveries per job in `jobs.lost_worker_count`. Past
`poison_pill_threshold`, transition the job to `DEAD_LETTER`, record the reason
in `dead_letters`, and stop. A dedicated `dead_letter` queue exists for parked
messages, drained only by an operator.

## Consequences

The failure mode that takes down a fleet is bounded to three workers. The cost
is that a job killed by an unrelated infrastructure problem three times also
gets parked; `POST /jobs/{id}/retry` is the way back, and it creates a new job
so the original stays as the record of what happened.

Counting in the database rather than on the message means the count survives the
redelivery, which is the only place it can live.
