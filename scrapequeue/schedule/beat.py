"""Scheduled submissions.

Beat does not crawl. It submits a job through the same path the API uses, so a
scheduled run is subject to the same validation, allowlist, circuit breaker and
rate limits as a manual one, and shows up in the same tables.
"""

from __future__ import annotations

from typing import Any

from scrapequeue.core.settings import get_settings
from scrapequeue.core.states import JobState
from scrapequeue.core.targets import load_target
from scrapequeue.core.urlguard import assert_allowed
from scrapequeue.db import repo
from scrapequeue.db.session import session_scope
from scrapequeue.observability import metrics
from scrapequeue.observability.logging import get_logger
from scrapequeue.queue.celery_app import app as celery_app

log = get_logger(__name__)


@celery_app.task(name="scrapequeue.schedule.beat.submit_scheduled_job")
def submit_scheduled_job(name: str, job: dict[str, Any]) -> dict[str, Any]:
    from scrapequeue.pipeline.crawl import crawl_job

    settings = get_settings()
    spec = load_target(job["target"])
    start_url = job.get("start_url") or str(spec.start_url)
    assert_allowed(start_url, settings.allowed_target_hosts)

    with session_scope() as session:
        row = repo.create_job(
            session,
            state=JobState.PENDING,
            target=spec.name,
            fetcher=job.get("fetcher") or spec.fetcher,
            start_url=start_url,
            max_pages=int(job.get("max_pages", 5)),
            output_format=job.get("output_format", "csv"),
            incremental=bool(job.get("incremental", False)),
            params={"_schedule": name},
            api_key_id="scheduler",
            idempotency_key=None,
            callback_url=job.get("callback_url"),
        )
        job_id = str(row.id)
        queue = "browser" if row.fetcher == "browser" else "default"
        row.celery_task_id = job_id

    crawl_job.apply_async(kwargs={"job_id": job_id}, queue=queue, task_id=job_id)
    metrics.JOBS_SUBMITTED.labels(target=spec.name, fetcher=spec.fetcher).inc()
    log.info("schedule.submitted", schedule=name, job_id=job_id, target=spec.name)
    return {"schedule": name, "job_id": job_id}
