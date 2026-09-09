"""Storage schema.

Design notes worth reading before changing anything here:

* jobs.idempotency_key is a partial unique index, not a plain unique column:
  most jobs have no key and NULLs would not collide anyway, but the partial
  index documents the intent and keeps the index small.
* job_pages has a (job_id, page) unique constraint. This is what makes a
  redelivered crawl task harmless: the second write of the same page conflicts
  instead of appending a duplicate set of rows.
* failed_pages is a separate table rather than a state flag so that operators
  can query and re-drive failures without scanning the whole page table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from scrapequeue.core.states import JobState, PageState


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    state: Mapped[str] = mapped_column(String(16), default=JobState.PENDING, index=True)

    # --- request ---
    target: Mapped[str] = mapped_column(String(64), index=True)
    fetcher: Mapped[str] = mapped_column(String(16))
    start_url: Mapped[str] = mapped_column(Text)
    max_pages: Mapped[int] = mapped_column(Integer)
    output_format: Mapped[str] = mapped_column(String(8))
    incremental: Mapped[bool] = mapped_column(default=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # --- caller identity ---
    api_key_id: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), default=None)
    callback_url: Mapped[str | None] = mapped_column(Text, default=None)

    # --- execution ---
    celery_task_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Incremented when a worker dies holding this job. Past the configured
    # threshold the job is a poison pill and goes to the dead-letter queue
    # instead of being retried forever.
    lost_worker_count: Mapped[int] = mapped_column(Integer, default=0)
    pages_total: Mapped[int | None] = mapped_column(Integer, default=None)
    pages_done: Mapped[int] = mapped_column(Integer, default=0)
    pages_failed: Mapped[int] = mapped_column(Integer, default=0)
    rows: Mapped[int] = mapped_column(Integer, default=0)

    # --- output ---
    result_key: Mapped[str | None] = mapped_column(Text, default=None)
    result_bytes: Mapped[int | None] = mapped_column(Integer, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    # --- timestamps ---
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    pages: Mapped[list[JobPage]] = relationship(back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (
        Index(
            "uq_jobs_idempotency_key",
            "api_key_id",
            "idempotency_key",
            unique=True,
            postgresql_where=idempotency_key.isnot(None),
        ),
        Index("ix_jobs_state_created", "state", "created_at"),
        CheckConstraint("max_pages > 0", name="ck_jobs_max_pages_positive"),
    )


class JobPage(Base):
    """One row per page attempt outcome. The checkpoint lives here, not in Redis,
    so a resumed job knows exactly which pages already produced rows."""

    __tablename__ = "job_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default=PageState.PENDING)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    # Hash of the extracted rows; incremental runs compare against the previous
    # snapshot of the same target to emit added/changed/removed instead of all.
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    job: Mapped[Job] = relationship(back_populates="pages")

    __table_args__ = (
        UniqueConstraint("job_id", "page", name="uq_job_pages_job_page"),
        Index("ix_job_pages_hash", "content_hash"),
    )


class FailedPage(Base):
    """Permanent per-page failures, kept after retries are exhausted."""

    __tablename__ = "failed_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer)
    error_type: Mapped[str] = mapped_column(String(64), index=True)
    error: Mapped[str] = mapped_column(Text)
    traceback: Mapped[str | None] = mapped_column(Text, default=None)
    last_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("job_id", "page", name="uq_failed_pages_job_page"),)


class DeadLetter(Base):
    """Jobs parked for a human: poison pills and exhausted job-level retries."""

    __tablename__ = "dead_letters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(String(32), index=True)
    detail: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    parked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class TargetSnapshot(Base):
    """Latest content hashes per target, used by incremental runs."""

    __tablename__ = "target_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target: Mapped[str] = mapped_column(String(64), index=True)
    row_key: Mapped[str] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, default=None
    )
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("target", "row_key", name="uq_target_snapshots_row"),)


class DriftBaseline(Base):
    """Rows-per-page baseline per target. A run far below it is SUSPECT, which
    is the difference between 'the site changed its markup' and 'we shipped an
    empty CSV and called it success'."""

    __tablename__ = "drift_baselines"

    target: Mapped[str] = mapped_column(String(64), primary_key=True)
    rows_per_page: Mapped[int] = mapped_column(Integer)
    samples: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
