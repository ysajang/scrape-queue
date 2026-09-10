# Runbook: a target domain is failing or blocking us

## Symptoms

- `sq_circuit_state` at 2 (open) for a domain
- `sq_page_failures_total` rising with `error_type="TransientFetchError"`
- Jobs for one target stuck while others proceed normally

## What the circuit is telling you

Five consecutive failures opened the domain. Every subsequent task for it is
refused immediately except one probe per cooling window. This is deliberate:
retrying into a site that is down, rate-limiting us, or blocking us burns worker
capacity and makes the block harder to lift.

## Triage

1. **Find the domain and how long it stays open.**
   ```bash
   docker compose exec redis redis-cli --scan --pattern 'sq:cb:*'
   docker compose exec redis redis-cli ttl sq:cb:simpler.grants.gov:open
   ```

2. **Read the actual errors** rather than guessing from the state:
   ```sql
   SELECT error_type, count(*), max(last_attempt_at), min(error)
   FROM failed_pages GROUP BY error_type ORDER BY 2 DESC LIMIT 5;
   ```

3. **Classify:**
   - `429` or `403` -> we are being rate limited or blocked. Lower
     `rate_per_second` in the target spec. Do not work around the block.
   - `5xx` or timeouts -> the site is having trouble. Wait; the probe will close
     the circuit on its own once it recovers.
   - `robots.txt disallows` -> the site changed its rules. Stop scraping that
     path. This is not a bug to route around.

## Recovery

The circuit closes itself on the first successful probe. To clear it manually
after fixing the cause:

```bash
docker compose exec redis redis-cli del sq:cb:<domain>:open sq:cb:<domain>:fails sq:cb:<domain>:probe
```

Then re-drive the affected jobs:

```bash
curl -s -X POST localhost:8000/jobs/<id>/retry -H 'X-API-Key: <write key>'
```

## Do not

Do not raise the rate limit to "get the backlog through" while a domain is
returning 429. That is the request that turns a temporary throttle into a
permanent block.
