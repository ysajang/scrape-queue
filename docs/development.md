# Development

## Local setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env      # point the hosts at localhost if you run services in compose
docker compose up -d postgres redis minio
alembic upgrade head
```

Run the pieces separately while developing:

```bash
uvicorn scrapequeue.api.app:app --reload --port 8000
celery -A scrapequeue.queue.celery_app worker -Q export,parse,default -c 4 --prefetch-multiplier 1
celery -A scrapequeue.queue.celery_app worker -Q browser -c 1 --prefetch-multiplier 1
celery -A scrapequeue.queue.celery_app beat -S redbeat.RedBeatScheduler
```

## Checks

```bash
ruff check . && ruff format --check .
mypy scrapequeue
pytest                   # unit only
pytest -m integration    # needs postgres, redis, minio
pytest -m chaos          # kills workers, ~2 minutes
```

## Adding a target

1. Write `targets/<name>.yml`. Required: `start_url`, `fetcher`,
   `fetcher_reason`, `pagination`, `extraction`. A browser target with an empty
   `fetcher_reason` fails the test suite.
2. Check `robots.txt` yourself and set `rate_per_second` conservatively.
3. Add the host to `allowed_target_hosts` in settings; the SSRF guard refuses
   anything else, in the API and again in the worker.
4. Confirm the pages are addressable by URL. If page 2 only exists after
   clicking, the loader will reject it and that is intentional (ADR 4).
5. Capture a fixture and add a selector test, following
   [the drift runbook](runbook/selector-broken.md).

## Conventions

- `db/repo.py` is the only module that writes to the job tables, so every state
  change goes through the state machine.
- Tasks take a `job_id` and load their own state. Passing objects through the
  broker would serialise a snapshot that is stale by the time it is consumed.
- New settings go in `core/settings.py` with a comment saying what failure the
  value prevents, not what the value is.
