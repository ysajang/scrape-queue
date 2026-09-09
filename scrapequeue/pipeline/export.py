"""Finalising a job: dedupe, drift check, write, upload, notify."""

from __future__ import annotations

import csv
import io
import json
from typing import Any
from uuid import UUID

from scrapequeue.core.settings import get_settings
from scrapequeue.core.states import JobState
from scrapequeue.core.targets import load_target
from scrapequeue.db import repo
from scrapequeue.db.session import session_scope
from scrapequeue.observability import metrics
from scrapequeue.observability.logging import get_logger
from scrapequeue.pipeline import dedupe as dedupe_mod
from scrapequeue.pipeline import drift
from scrapequeue.queue.celery_app import app as celery_app
from scrapequeue.storage import s3
from scrapequeue.workers.extract import columns

log = get_logger(__name__)


def _load_rows(job_id: UUID, page_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in sorted(page_results, key=lambda r: r.get("page", 0)):
        key = result.get("key")
        if not key:
            continue
        bucket = get_settings().s3_bucket
        body = s3.get_client().get_object(Bucket=bucket, Key=key)["Body"].read()
        rows.extend(json.loads(body))
    return rows


def to_csv(rows: list[dict[str, Any]], header: list[str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def to_json(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")


def finalise_job(job_uuid: UUID, page_results: list[dict[str, Any]]) -> dict[str, Any]:
    with session_scope() as session:
        job = repo.get_job(session, job_uuid)
        if job is None:
            return {"job_id": str(job_uuid), "status": "missing"}
        if JobState(job.state) in (JobState.CANCELLED, JobState.DEAD_LETTER):
            return {"job_id": str(job_uuid), "status": job.state}
        spec = load_target(job.target)
        output_format = job.output_format
        incremental = job.incremental
        callback_url = job.callback_url
        pages_failed = job.pages_failed

    rows = _load_rows(job_uuid, page_results)
    deduped = dedupe_mod.dedupe(rows, spec)
    emitted = deduped.rows
    diff_counts: dict[str, int] = {}

    with session_scope() as session:
        if incremental:
            diff = dedupe_mod.diff_against_snapshot(session, spec, deduped.rows)
            emitted = diff.rows
            diff_counts = {
                "added": len(diff.added),
                "changed": len(diff.changed),
                "removed": len(diff.removed_keys),
            }
        dedupe_mod.store_snapshot(session, spec, deduped.rows, job_uuid)

        pages_done = len([r for r in page_results if not r.get("failed")])
        verdict = drift.check(session, spec.name, len(deduped.rows), max(pages_done, 0))
        if not verdict.suspect:
            drift.update_baseline(session, spec.name, len(deduped.rows), pages_done)

    header = columns(spec)
    payload = to_csv(emitted, header) if output_format == "csv" else to_json(emitted)
    key = f"jobs/{job_uuid}/result.{output_format}"
    size = s3.put_bytes(key, payload, "text/csv" if output_format == "csv" else "application/json")

    if verdict.suspect:
        state = JobState.SUSPECT
        error = f"possible selector drift: {verdict.reason}"
        metrics.DRIFT_SUSPECTS.labels(target=spec.name).inc()
    elif pages_failed:
        state = JobState.PARTIAL
        error = f"{pages_failed} page(s) failed permanently"
    else:
        state = JobState.SUCCEEDED
        error = None

    with session_scope() as session:
        job = repo.get_job(session, job_uuid)
        if job is None:
            return {"job_id": str(job_uuid), "status": "missing"}
        job.result_key = key
        job.result_bytes = size
        job.rows = len(emitted)
        repo.transition(session, job, state, error=error)
        if job.started_at and job.finished_at:
            metrics.JOB_DURATION.labels(target=spec.name, fetcher=spec.fetcher).observe(
                (job.finished_at - job.started_at).total_seconds()
            )
        metrics.JOBS_FINISHED.labels(target=spec.name, state=state).inc()

    log.info(
        "export.done",
        job_id=str(job_uuid),
        state=state,
        rows=len(emitted),
        duplicates=deduped.duplicates,
        bytes=size,
        **diff_counts,
    )

    if callback_url:
        from scrapequeue.api.routes.webhooks import deliver_callback

        deliver_callback.apply_async(
            kwargs={
                "job_id": str(job_uuid),
                "url": callback_url,
                "state": str(state),
                "rows": len(emitted),
            },
            queue="export",
        )

    return {"job_id": str(job_uuid), "state": str(state), "rows": len(emitted), "key": key}


@celery_app.task(name="scrapequeue.pipeline.export.export_job", bind=True)
def export_job(self: Any, page_results: list[dict[str, Any]], job_id: str) -> dict[str, Any]:
    return finalise_job(UUID(job_id), page_results)
