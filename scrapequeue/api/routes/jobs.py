from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from scrapequeue.api.deps import get_db, get_redis
from scrapequeue.api.middleware.auth import Principal, require_scope
from scrapequeue.api.middleware.backpressure import assert_capacity
from scrapequeue.api.middleware.ratelimit import enforce_api_rate_limit
from scrapequeue.core import idempotency
from scrapequeue.core.models import FailedPage as FailedPageOut
from scrapequeue.core.models import JobAccepted, JobCreate, JobRead, PageProgress
from scrapequeue.core.settings import get_settings
from scrapequeue.core.states import TERMINAL_STATES, JobState
from scrapequeue.core.targets import UnknownTarget, load_target
from scrapequeue.core.urlguard import BlockedURL, assert_allowed
from scrapequeue.db import repo
from scrapequeue.db.tables import FailedPage, Job
from scrapequeue.observability import metrics
from scrapequeue.observability.logging import get_logger
from scrapequeue.queue.celery_app import app as celery_app

router = APIRouter(prefix="/jobs", tags=["jobs"])
log = get_logger(__name__)

CRAWL_TASK = "scrapequeue.pipeline.crawl.crawl_job"


def _to_read(job: Job) -> JobRead:
    return JobRead(
        id=job.id,
        state=JobState(job.state),
        target=job.target,
        fetcher=job.fetcher,  # type: ignore[arg-type]
        start_url=job.start_url,
        max_pages=job.max_pages,
        output_format=job.output_format,  # type: ignore[arg-type]
        incremental=job.incremental,
        progress=PageProgress(
            pages_total=job.pages_total,
            pages_done=job.pages_done,
            pages_failed=job.pages_failed,
            rows=job.rows,
        ),
        attempts=job.attempts,
        error=job.error,
        result_key=job.result_key,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
def submit_job(
    payload: JobCreate,
    response: Response,
    principal: Annotated[Principal, Depends(enforce_api_rate_limit)],
    _: Annotated[Principal, Depends(require_scope("write"))],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    settings = get_settings()

    try:
        spec = load_target(payload.target)
    except UnknownTarget:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown target: {payload.target}") from None

    fetcher = payload.fetcher or spec.fetcher
    start_url = str(payload.start_url or spec.start_url)

    try:
        assert_allowed(start_url, settings.allowed_target_hosts)
    except BlockedURL as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None

    queue = "browser" if fetcher == "browser" else "default"
    depth = assert_capacity(get_redis(), queue)

    if idempotency_key:
        existing = repo.find_by_idempotency_key(db, principal.key_id, idempotency_key)
        if existing is not None:
            stored = existing.params.get("_fingerprint")
            if stored and stored != idempotency.fingerprint(payload.model_dump(mode="json")):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Idempotency-Key {idempotency_key} was used with a different body",
                )
            response.headers["Location"] = f"/jobs/{existing.id}"
            return JobAccepted(
                id=existing.id, state=JobState(existing.state), idempotent_replay=True
            )

    params = dict(payload.params)
    if idempotency_key:
        params["_fingerprint"] = idempotency.fingerprint(payload.model_dump(mode="json"))

    job = repo.create_job(
        db,
        state=JobState.PENDING,
        target=payload.target,
        fetcher=fetcher,
        start_url=start_url,
        max_pages=payload.max_pages,
        output_format=payload.output_format,
        incremental=payload.incremental,
        params=params,
        api_key_id=principal.key_id,
        idempotency_key=idempotency_key,
        callback_url=str(payload.callback_url) if payload.callback_url else None,
    )

    async_result = celery_app.send_task(
        CRAWL_TASK, kwargs={"job_id": str(job.id)}, queue=queue, task_id=str(job.id)
    )
    job.celery_task_id = async_result.id
    db.flush()

    metrics.JOBS_SUBMITTED.labels(target=payload.target, fetcher=fetcher).inc()
    log.info("job.submitted", job_id=str(job.id), target=payload.target, queue=queue, depth=depth)

    response.headers["Location"] = f"/jobs/{job.id}"
    return JobAccepted(id=job.id, state=JobState.PENDING)


@router.get("/{job_id}", response_model=JobRead)
def get_job(
    job_id: UUID,
    principal: Annotated[Principal, Depends(require_scope("read"))],
    db: Annotated[Session, Depends(get_db)],
) -> JobRead:
    job = repo.get_job(db, job_id)
    if job is None or (principal.scope != "admin" and job.api_key_id != principal.key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return _to_read(job)


@router.delete("/{job_id}", response_model=JobRead)
def cancel_job(
    job_id: UUID,
    principal: Annotated[Principal, Depends(require_scope("write"))],
    db: Annotated[Session, Depends(get_db)],
) -> JobRead:
    job = repo.get_job(db, job_id)
    if job is None or (principal.scope != "admin" and job.api_key_id != principal.key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    if JobState(job.state) in TERMINAL_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"job already {job.state}")

    # terminate=True reaches a task that is mid-fetch; the worker's soft time
    # limit handler closes the browser context before the process gives up.
    celery_app.control.revoke(job.celery_task_id or str(job.id), terminate=True, signal="SIGTERM")
    repo.transition(db, job, JobState.CANCELLED, error="cancelled by caller")
    log.info("job.cancelled", job_id=str(job_id))
    return _to_read(job)


@router.get("/{job_id}/failures", response_model=list[FailedPageOut])
def list_failures(
    job_id: UUID,
    principal: Annotated[Principal, Depends(require_scope("read"))],
    db: Annotated[Session, Depends(get_db)],
) -> list[FailedPageOut]:
    job = repo.get_job(db, job_id)
    if job is None or (principal.scope != "admin" and job.api_key_id != principal.key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    rows = db.execute(
        select(FailedPage).where(FailedPage.job_id == job_id).order_by(FailedPage.page)
    ).scalars()
    return [
        FailedPageOut(
            page=r.page, url=r.url, attempts=r.attempts, error=r.error, last_attempt_at=r.last_attempt_at
        )
        for r in rows
    ]


@router.post("/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
def retry_job(
    job_id: UUID,
    principal: Annotated[Principal, Depends(require_scope("write"))],
    db: Annotated[Session, Depends(get_db)],
) -> JobAccepted:
    """Re-drive a finished job as a new job.

    A new row rather than a revived one: the original stays as the record of
    what happened, and the state machine keeps its rule that terminal is final.
    """
    old = repo.get_job(db, job_id)
    if old is None or (principal.scope != "admin" and old.api_key_id != principal.key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    if JobState(old.state) not in TERMINAL_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"job is still {old.state}")

    new = repo.create_job(
        db,
        state=JobState.PENDING,
        target=old.target,
        fetcher=old.fetcher,
        start_url=old.start_url,
        max_pages=old.max_pages,
        output_format=old.output_format,
        incremental=old.incremental,
        params={**old.params, "_retry_of": str(old.id)},
        api_key_id=old.api_key_id,
        idempotency_key=None,
        callback_url=old.callback_url,
    )
    queue = "browser" if old.fetcher == "browser" else "default"
    celery_app.send_task(CRAWL_TASK, kwargs={"job_id": str(new.id)}, queue=queue, task_id=str(new.id))
    new.celery_task_id = str(new.id)
    db.flush()
    log.info("job.retried", job_id=str(new.id), retry_of=str(old.id))
    return JobAccepted(id=new.id, state=JobState.PENDING)
