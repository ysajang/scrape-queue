"""Dead-letter queue.

A parked job is not retried. It sits on its own queue with its reason recorded,
because the failures that reach here are the ones a human has to look at: a
poison pill that kills workers, or a job whose retries are exhausted.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from scrapequeue.queue.celery_app import app as celery_app

from scrapequeue.core.states import JobState
from scrapequeue.db import repo
from scrapequeue.db.session import session_scope
from scrapequeue.observability import metrics
from scrapequeue.observability.logging import get_logger

log = get_logger(__name__)


@celery_app.task(name="scrapequeue.queue.deadletter.park", acks_late=True)
def park(job_id: str, reason: str, detail: str, payload: dict[str, Any] | None = None) -> None:
    job_uuid = UUID(job_id)
    with session_scope() as session:
        job = repo.get_job(session, job_uuid)
        if job is None:
            return
        repo.park_dead_letter(session, job_uuid, reason, detail, payload or {})
        if JobState(job.state) not in (JobState.DEAD_LETTER,):
            repo.transition(session, job, JobState.DEAD_LETTER, error=detail)
    metrics.DEAD_LETTERS.labels(reason=reason).inc()
    log.error("job.dead_letter", job_id=job_id, reason=reason, detail=detail)
