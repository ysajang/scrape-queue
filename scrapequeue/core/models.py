"""API-facing schemas. These are the contract; db.tables is the storage."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from scrapequeue.core.states import JobState

FetcherKind = Literal["http", "browser"]
OutputFormat = Literal["csv", "json"]


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(description="name of a spec file in targets/, without extension")
    fetcher: FetcherKind | None = Field(
        default=None, description="overrides the spec default; browser jobs go to the browser queue"
    )
    start_url: HttpUrl | None = Field(default=None, description="overrides the spec start_url")
    max_pages: int = Field(default=5, ge=1, le=1000)
    output_format: OutputFormat = "csv"
    incremental: bool = Field(
        default=False, description="diff against the previous snapshot of the same target"
    )
    callback_url: HttpUrl | None = Field(default=None, description="signed webhook on completion")
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target")
    @classmethod
    def _safe_target_name(cls, v: str) -> str:
        # The value becomes a file path; reject anything that could escape targets/.
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("target must be alphanumeric with - or _")
        return v


class PageProgress(BaseModel):
    pages_total: int | None = None
    pages_done: int = 0
    pages_failed: int = 0
    rows: int = 0


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    state: JobState
    target: str
    fetcher: FetcherKind
    start_url: str
    max_pages: int
    output_format: OutputFormat
    incremental: bool
    progress: PageProgress
    attempts: int
    error: str | None = None
    result_key: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobAccepted(BaseModel):
    id: UUID
    state: JobState
    idempotent_replay: bool = Field(
        default=False, description="true when an Idempotency-Key matched an existing job"
    )


class ResultLink(BaseModel):
    url: str
    expires_in: int
    content_type: str
    rows: int


class FailedPage(BaseModel):
    page: int
    url: str
    attempts: int
    error: str
    last_attempt_at: datetime
