"""The crawl tasks.

Shape of a run:

    crawl_job          plans the pages and fans out
      fetch_page * N   one independent task per page  (default | browser queue)
      assemble_job     chord callback: dedupe, drift check, export  (export queue)

One task per page rather than one task per job is the decision the rest of the
design rests on. It gives per-page retries, per-page failure records, resume
after a worker dies, and horizontal scale by adding workers. The cost is that
the job's completion has to be assembled from the parts, which is what the
chord callback does.
"""

from __future__ import annotations

import json
import random
from typing import Any
from uuid import UUID

from celery import chord

from scrapequeue.queue.celery_app import app as celery_app
from celery.exceptions import SoftTimeLimitExceeded, WorkerLostError

from scrapequeue.api.deps import get_redis
from scrapequeue.core.settings import get_settings
from scrapequeue.core.states import TERMINAL_STATES, JobState, PageState
from scrapequeue.core.targets import load_target
from scrapequeue.db import repo
from scrapequeue.db.session import session_scope
from scrapequeue.observability import metrics
from scrapequeue.observability.logging import get_logger, job_id_var
from scrapequeue.queue.checkpoint import pages_to_fetch
from scrapequeue.queue.circuit import CircuitOpen
from scrapequeue.storage import s3
from scrapequeue.workers.fetch import PermanentFetchError, TransientFetchError, build_fetcher

log = get_logger(__name__)

FETCH_TASK = "scrapequeue.pipeline.crawl.fetch_page"
ASSEMBLE_TASK = "scrapequeue.pipeline.crawl.assemble_job"


def page_key(job_id: UUID | str, page: int) -> str:
    return f"jobs/{job_id}/pages/{page:05d}.json"


@celery_app.task(name="scrapequeue.pipeline.crawl.crawl_job", bind=True, max_retries=3)
def crawl_job(self: Any, job_id: str) -> dict[str, Any]:
    job_uuid = UUID(job_id)
    job_id_var.set(job_id)
    settings = get_settings()

    with session_scope() as session:
        job = repo.get_job(session, job_uuid)
        if job is None:
            log.error("crawl.job_missing", job_id=job_id)
            return {"job_id": job_id, "status": "missing"}
        state = JobState(job.state)
        if state in TERMINAL_STATES:
            # A redelivered crawl_job for a job that already finished. Returning
            # is correct: the state machine treats terminal as final, and a
            # retry is an explicit new job via POST /jobs/{id}/retry.
            log.info("crawl.already_finished", job_id=job_id, state=state)
            return {"job_id": job_id, "status": str(state)}

        # A task arriving here a second time means the previous worker died
        # holding it (acks_late + reject_on_worker_lost put it back). Past the
        # threshold this job is a poison pill and goes to an operator instead
        # of taking down another worker.
        if state is JobState.RUNNING:
            lost = repo.bump_lost_worker(session, job_uuid)
            if lost >= settings.poison_pill_threshold:
                repo.transition(session, job, JobState.DEAD_LETTER, error="poison pill")
                repo.park_dead_letter(
                    session, job_uuid, "poison_pill", f"redelivered {lost} times", {}
                )
                metrics.DEAD_LETTERS.labels(reason="poison_pill").inc()
                log.error("crawl.poison_pill", job_id=job_id, redeliveries=lost)
                return {"job_id": job_id, "status": "dead_letter"}
        else:
            repo.transition(session, job, JobState.RUNNING)

        job.attempts += 1
        job.pages_total = job.max_pages
        spec_name, max_pages, base_url = job.target, job.max_pages, job.start_url
        pending = pages_to_fetch(session, job_uuid, max_pages)

    if not pending:
        log.info("crawl.nothing_to_do", job_id=job_id)
        assemble_job.apply_async(args=[[], job_id], queue="export")
        return {"job_id": job_id, "pages": 0, "resumed": True}

    log.info(
        "crawl.planned",
        job_id=job_id,
        target=spec_name,
        pending=len(pending),
        already_done=max_pages - len(pending),
    )

    spec = load_target(spec_name)
    queue = "browser" if spec.fetcher == "browser" else "default"
    header = [
        fetch_page.signature(kwargs={"job_id": job_id, "page": page, "base_url": base_url}, queue=queue)
        for page in pending
    ]
    chord(header)(assemble_job.signature(args=[job_id], queue="export"))
    return {"job_id": job_id, "pages": len(pending)}


@celery_app.task(
    name=FETCH_TASK,
    bind=True,
    autoretry_for=(TransientFetchError, CircuitOpen),
    retry_backoff=True,
    retry_backoff_max=1800,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def fetch_page(self: Any, job_id: str, page: int, base_url: str | None = None) -> dict[str, Any]:
    """Fetch one page and store its rows.

    Retries are driven by the exception type, not by the status code alone: a
    404 or a robots refusal is a configuration problem and retrying it only
    wastes the target's bandwidth, so PermanentFetchError is recorded and not
    retried.
    """
    job_uuid = UUID(job_id)
    job_id_var.set(job_id)

    with session_scope() as session:
        job = repo.get_job(session, job_uuid)
        if job is None or JobState(job.state) is JobState.CANCELLED:
            return {"page": page, "rows": 0, "skipped": True}
        spec_name = job.target

    spec = load_target(spec_name)
    fetcher = build_fetcher(spec, get_redis())
    try:
        result = fetcher.fetch_page(page, base_url)
    except PermanentFetchError as exc:
        _record_failure(job_uuid, spec_name, page, base_url or str(spec.start_url), exc, self)
        return {"page": page, "rows": 0, "failed": True}
    except SoftTimeLimitExceeded as exc:
        # The soft limit fires inside the task so the browser context can be
        # closed properly; the hard limit would kill the process mid-render.
        _record_failure(job_uuid, spec_name, page, base_url or str(spec.start_url), exc, self)
        raise
    except (TransientFetchError, CircuitOpen) as exc:
        if self.request.retries >= self.max_retries:
            _record_failure(job_uuid, spec_name, page, base_url or str(spec.start_url), exc, self)
            return {"page": page, "rows": 0, "failed": True}
        raise
    except WorkerLostError:
        raise
    finally:
        fetcher.close()

    key = page_key(job_id, page)
    s3.put_bytes(key, json.dumps(result.rows, ensure_ascii=False).encode("utf-8"), "application/json")

    with session_scope() as session:
        inserted = repo.record_page(
            session,
            job_uuid,
            page,
            result.url,
            state=PageState.PARSED,
            rows=len(result.rows),
        )

    log.info(
        "crawl.page_done",
        job_id=job_id,
        page=page,
        rows=len(result.rows),
        duplicate_delivery=not inserted,
        waited=round(result.waited, 2),
        duration=round(result.duration, 2),
    )
    return {"page": page, "rows": len(result.rows), "key": key, "duplicate": not inserted}


def _record_failure(
    job_uuid: UUID, target: str, page: int, url: str, exc: BaseException, task: Any
) -> None:
    with session_scope() as session:
        repo.record_failed_page(
            session,
            job_uuid,
            page,
            url,
            attempts=int(getattr(task.request, "retries", 0)) + 1,
            error_type=type(exc).__name__,
            error=str(exc),
        )
    metrics.PAGE_FAILURES.labels(target=target, error_type=type(exc).__name__).inc()
    log.warning("crawl.page_failed", job_id=str(job_uuid), page=page, error=str(exc))


@celery_app.task(name=ASSEMBLE_TASK, bind=True, max_retries=3, acks_late=True)
def assemble_job(self: Any, page_results: list[dict[str, Any]], job_id: str) -> dict[str, Any]:
    from scrapequeue.pipeline.export import finalise_job

    job_id_var.set(job_id)
    return finalise_job(UUID(job_id), page_results)
