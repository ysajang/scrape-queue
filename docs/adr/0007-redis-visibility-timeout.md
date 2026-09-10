# 7. Redis transport: recovery latency is minutes

**Status:** accepted

## Context

The delivery guarantee (`acks_late`, `reject_on_worker_lost`) says a task whose
worker was killed will run again. The chaos test measures how long that takes,
and the answer was not what the settings suggested.

## Measurement

With `visibility_timeout` set to 15 seconds, a task whose worker was SIGKILLed
mid-execution was picked up by a fresh worker **100 seconds** later. Reducing
the timeout further does not help.

The reason is in kombu's Redis transport. Redis has no broker-side
acknowledgement, so unacked messages sit in a hash with a timestamp. A live
worker sweeps for expired entries via `QoS.restore_visible`, which the event
loop calls on a 10 second timer but which only acts on every tenth call. The
effective sweep interval is therefore about 100 seconds, independent of
`visibility_timeout`.

## Decision

Keep the Redis transport and document the latency instead of pretending it away.
`visibility_timeout` is set above the slowest expected task (a value below task
duration redelivers work that is still running), and the API's
`POST /jobs/{id}/retry` is the supported path when a job must recover now.

## Consequences

The README states recovery in minutes rather than seconds, and the chaos test
asserts the guarantee with a timeout that reflects the real interval. If this
latency ever becomes unacceptable, the fix is a broker with real
acknowledgements (RabbitMQ or SQS), not more tuning: both would make redelivery
immediate on connection loss. That change would touch only the transport
settings, since nothing in the pipeline depends on Redis-specific broker
behaviour.
