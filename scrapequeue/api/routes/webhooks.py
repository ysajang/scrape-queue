"""Signed completion callbacks.

The callback is a task on the export queue, not an inline HTTP call at the end
of the job: a caller whose endpoint is slow or down must not hold a worker or
turn a finished job into a failed one. Deliveries are signed with HMAC so the
receiver can tell a real callback from anyone who learned the URL.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx
from scrapequeue.queue.celery_app import app as celery_app
from fastapi import APIRouter

from scrapequeue.core.settings import get_settings
from scrapequeue.observability import metrics
from scrapequeue.observability.logging import get_logger

router = APIRouter(tags=["webhooks"])
log = get_logger(__name__)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256)
    return mac.hexdigest()


@celery_app.task(
    name="scrapequeue.api.routes.webhooks.deliver_callback",
    bind=True,
    autoretry_for=(httpx.TransportError, httpx.HTTPStatusError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def deliver_callback(self: Any, job_id: str, url: str, state: str, rows: int) -> dict[str, Any]:
    settings = get_settings()
    body = json.dumps({"job_id": job_id, "state": state, "rows": rows}).encode("utf-8")
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Scrapequeue-Timestamp": timestamp,
        "X-Scrapequeue-Signature": sign(settings.webhook_secret.get_secret_value(), timestamp, body),
    }
    try:
        response = httpx.post(url, content=body, headers=headers, timeout=10.0)
        response.raise_for_status()
    except Exception:
        metrics.WEBHOOK_ATTEMPTS.labels(outcome="error").inc()
        raise
    metrics.WEBHOOK_ATTEMPTS.labels(outcome="ok").inc()
    log.info("webhook.delivered", job_id=job_id, status=response.status_code)
    return {"job_id": job_id, "status": response.status_code}
