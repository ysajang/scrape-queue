# Runbook: the queue is backing up

## Symptoms

- `sq_queue_depth` climbing on the Grafana dashboard
- `POST /jobs` returning 429 with `Retry-After` (backpressure is working)
- Jobs sitting in `PENDING` far longer than usual

## Triage, in order

1. **Are workers alive?**
   ```bash
   curl -s localhost:8000/readyz | jq .checks.workers
   ```
   An empty list means no worker answered the ping. Check the container or pod
   status before anything else.

2. **Which queue is deep?** `browser` and `default` fail for different reasons.
   ```bash
   docker compose exec redis redis-cli llen browser
   docker compose exec redis redis-cli llen default
   ```

3. **Is a domain circuit open?** A parked domain means tasks are retrying into
   a wall.
   ```bash
   docker compose exec redis redis-cli --scan --pattern 'sq:cb:*:open'
   ```
   If so, follow [domain-blocked](domain-blocked.md) instead; adding workers
   will not help.

4. **Is this the rate limit rather than capacity?** Check
   `sq_rate_limit_wait_seconds` on the dashboard. High wait times mean the jobs
   are queued behind one domain's budget, and **adding workers changes nothing**
   (see ADR 5). Either raise `rate_per_second` in that target's spec, which is a
   decision about how hard to hit someone else's site, or accept the throughput.

## Fixes

- Capacity, HTTP work: `docker compose up -d --scale worker-default=3`
- Capacity, browser work: scale `worker-browser`; each replica needs roughly
  1 GB of RAM and `shm_size: 1gb`
- Kubernetes: KEDA already scales on queue depth
  (`deploy/helm/scrape-queue/templates/keda-scaledobject.yaml`). Check the
  `ScaledObject` status and the configured `maxReplicaCount` before scaling by
  hand.
- Shed load deliberately: lower `QUEUE_DEPTH_LIMIT` so submissions are refused
  earlier rather than accepted into an hours-deep backlog.

## Do not

Do not raise `worker_prefetch_multiplier` to "drain faster". It moves messages
into worker memory where a crash loses them, and it is the reason a single dying
worker can take several jobs down with it.
