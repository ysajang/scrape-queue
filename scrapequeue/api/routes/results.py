from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from scrapequeue.api.deps import get_db
from scrapequeue.api.middleware.auth import Principal, require_scope
from scrapequeue.core.models import ResultLink
from scrapequeue.core.settings import get_settings
from scrapequeue.core.states import JobState
from scrapequeue.db import repo
from scrapequeue.storage import s3

router = APIRouter(prefix="/jobs", tags=["results"])

_CONTENT_TYPES = {"csv": "text/csv", "json": "application/json"}


@router.get("/{job_id}/result", response_model=ResultLink)
def get_result(
    job_id: UUID,
    principal: Annotated[Principal, Depends(require_scope("read"))],
    db: Annotated[Session, Depends(get_db)],
) -> ResultLink:
    job = repo.get_job(db, job_id)
    if job is None or (principal.scope != "admin" and job.api_key_id != principal.key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    if job.result_key is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"no result yet; job is {JobState(job.state)}"
        )
    ttl = get_settings().s3_presign_ttl_seconds
    return ResultLink(
        url=s3.presign(job.result_key, ttl),
        expires_in=ttl,
        content_type=_CONTENT_TYPES[job.output_format],
        rows=job.rows,
    )
