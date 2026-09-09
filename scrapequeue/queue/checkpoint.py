"""Resume planning.

A job that dies at page 40 of 50 must come back and fetch ten pages, not fifty.
The checkpoint is the job_pages table rather than a Redis key: Redis is the
broker and may be flushed or expire keys, and a checkpoint that disappears
silently turns a resume into a full re-crawl of a site we promised to be gentle
with.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from scrapequeue.db import repo


def pages_to_fetch(session: Session, job_id: UUID, max_pages: int) -> list[int]:
    done = repo.completed_pages(session, job_id)
    return [page for page in range(1, max_pages + 1) if page not in done]


def resume_summary(session: Session, job_id: UUID, max_pages: int) -> tuple[int, int]:
    remaining = pages_to_fetch(session, job_id, max_pages)
    return len(remaining), max_pages - len(remaining)
