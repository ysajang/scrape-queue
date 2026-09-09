from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.orm import Session

from scrapequeue.api.deps import get_db, get_redis
from scrapequeue.queue.celery_app import app as celery_app

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependencies checked on purpose, so a
    database blip does not cause an orchestrator to restart healthy pods."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Readiness: every dependency needed to accept a job."""
    checks: dict[str, Any] = {}
    ok = True

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc.__class__.__name__}"
        ok = False

    try:
        get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc.__class__.__name__}"
        ok = False

    try:
        pong = celery_app.control.ping(timeout=0.5)
        workers = sorted(name for reply in pong or [] for name in reply)
        checks["workers"] = workers
        if not workers:
            checks["workers_note"] = "no worker responded to ping"
            ok = False
    except Exception as exc:
        checks["workers"] = f"error: {exc.__class__.__name__}"
        ok = False

    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "checks": checks}


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
