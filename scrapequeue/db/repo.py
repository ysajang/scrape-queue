"""Every write to the job tables goes through here.

Two rules the rest of the codebase relies on:
  1. State changes are validated against the state machine, so an illegal
     transition raises instead of being persisted.
  2. Page results are written with ON CONFLICT DO NOTHING on (job_id, page),
     which makes a redelivered task a no-op rather than a duplicate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from scrapequeue.core.states import JobState, PageState, TERMINAL_STATES, assert_transition
from scrapequeue.db.tables import DeadLetter, FailedPage, Job, JobPage


def _now() -> datetime:
    return datetime.now(UTC)


def get_job(session: Session, job_id: UUID) -> Job | None:
    return session.get(Job, job_id)


def find_by_idempotency_key(session: Session, api_key_id: str, key: str) -> Job | None:
    stmt = select(Job).where(Job.api_key_id == api_key_id, Job.idempotency_key == key)
    return session.execute(stmt).scalar_one_or_none()


def create_job(session: Session, **fields: object) -> Job:
    job = Job(**fields)  # type: ignore[arg-type]
    session.add(job)
    session.flush()
    return job


def transition(session: Session, job: Job, target: JobState, *, error: str | None = None) -> Job:
    current = JobState(job.state)
    assert_transition(current, target)
    job.state = target
    if target is JobState.RUNNING and job.started_at is None:
        job.started_at = _now()
    if target in TERMINAL_STATES:
        job.finished_at = _now()
    if error is not None:
        job.error = error[:4000]
    session.flush()
    return job


def record_page(
    session: Session,
    job_id: UUID,
    page: int,
    url: str,
    *,
    state: PageState,
    rows: int = 0,
    content_hash: str | None = None,
) -> bool:
    """Idempotent page write. Returns True if this call inserted the row,
    False if the page was already recorded (redelivery)."""
    stmt = (
        pg_insert(JobPage)
        .values(
            job_id=job_id,
            page=page,
            url=url,
            state=state,
            rows=rows,
            content_hash=content_hash,
            attempts=1,
            fetched_at=_now(),
        )
        .on_conflict_do_nothing(constraint="uq_job_pages_job_page")
        .returning(JobPage.id)
    )
    inserted = session.execute(stmt).scalar_one_or_none()
    if inserted is None:
        return False
    session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(pages_done=Job.pages_done + 1, rows=Job.rows + rows)
    )
    return True


def record_failed_page(
    session: Session,
    job_id: UUID,
    page: int,
    url: str,
    *,
    attempts: int,
    error_type: str,
    error: str,
    traceback: str | None = None,
) -> None:
    stmt = (
        pg_insert(FailedPage)
        .values(
            job_id=job_id,
            page=page,
            url=url,
            attempts=attempts,
            error_type=error_type[:64],
            error=error[:4000],
            traceback=traceback,
            last_attempt_at=_now(),
        )
        .on_conflict_do_update(
            constraint="uq_failed_pages_job_page",
            set_={"attempts": attempts, "error": error[:4000], "last_attempt_at": _now()},
        )
    )
    session.execute(stmt)
    session.execute(
        update(Job).where(Job.id == job_id).values(pages_failed=Job.pages_failed + 1)
    )


def completed_pages(session: Session, job_id: UUID) -> set[int]:
    """Pages that already produced rows. A resumed job skips these instead of
    restarting from page 1."""
    stmt = select(JobPage.page).where(
        JobPage.job_id == job_id, JobPage.state.in_([PageState.FETCHED, PageState.PARSED])
    )
    return set(session.execute(stmt).scalars().all())


def park_dead_letter(
    session: Session, job_id: UUID, reason: str, detail: str, payload: dict[str, object]
) -> None:
    session.add(
        DeadLetter(job_id=job_id, reason=reason[:32], detail=detail[:4000], payload=payload)
    )


def bump_lost_worker(session: Session, job_id: UUID) -> int:
    """Called when a task is redelivered because its worker died. Returns the
    new count so the caller can compare it against poison_pill_threshold."""
    stmt = (
        update(Job)
        .where(Job.id == job_id)
        .values(lost_worker_count=Job.lost_worker_count + 1)
        .returning(Job.lost_worker_count)
    )
    return int(session.execute(stmt).scalar_one())


def queue_depth_by_state(session: Session) -> dict[str, int]:
    stmt = select(Job.state, func.count()).group_by(Job.state)
    return {state: count for state, count in session.execute(stmt).all()}
