"""Celery application.

The settings block is the part of this repository that matters most: each line
below closes a specific failure mode that appears only under load or on
worker death. See docs/adr/0006-dead-letter-queue.md and docs/architecture.md.
"""

from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init, worker_ready, worker_shutdown

from scrapequeue.core.settings import get_settings
from scrapequeue.queue.routing import QUEUES, TASK_ROUTES

settings = get_settings()

app = Celery("scrapequeue")

app.conf.update(
    # --- transport ---
    broker_url=settings.redis_url,
    result_backend=settings.redis_url,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        # Must exceed task_time_limit: Redis re-queues any unacked message
        # older than this, so a smaller value duplicates long-running jobs.
        "visibility_timeout": settings.visibility_timeout_seconds,
        "queue_order_strategy": "priority",
    },
    result_expires=7 * 24 * 3600,
    # --- delivery guarantees ---
    # Ack after the task finishes, not when it is received. Combined with
    # reject_on_worker_lost, a worker killed mid-task (OOM, SIGKILL, node loss)
    # leaves the message in the queue for another worker instead of losing it.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # One message in flight per process. Prefetching is what makes a dying
    # worker take several jobs down with it.
    worker_prefetch_multiplier=1,
    # --- time limits ---
    task_soft_time_limit=settings.task_soft_time_limit_seconds,
    task_time_limit=settings.task_time_limit_seconds,
    # --- routing ---
    task_queues=QUEUES,
    task_routes=TASK_ROUTES,
    task_default_queue="default",
    task_default_exchange="scrapequeue",
    task_default_routing_key="default",
    task_create_missing_queues=False,
    # --- state visibility ---
    task_track_started=True,
    task_send_sent_event=True,
    worker_send_task_events=True,
    # --- serialisation ---
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # --- beat ---
    beat_scheduler="redbeat.RedBeatScheduler",
    redbeat_redis_url=settings.redbeat_redis_url,
    # Lock TTL; a second Beat instance waits for it instead of double-firing.
    redbeat_lock_timeout=60,
    # --- retries (policy lives on the task; these are the shared defaults) ---
    task_default_retry_delay=settings.retry_backoff_base_seconds,
    task_annotations={"*": {"max_retries": settings.max_job_retries}},
)

app.autodiscover_tasks(
    [
        "scrapequeue.pipeline.crawl",
        "scrapequeue.pipeline.parse",
        "scrapequeue.pipeline.dedupe",
        "scrapequeue.pipeline.export",
        "scrapequeue.queue.deadletter",
        "scrapequeue.api.routes.webhooks",
    ],
    force=True,
)


@worker_process_init.connect
def _init_process(**_: object) -> None:
    # Per-process setup: logging, tracing, metrics server, browser pool warmup.
    from scrapequeue.observability.logging import configure_logging
    from scrapequeue.observability.tracing import configure_tracing

    configure_logging(settings.log_level)
    configure_tracing(settings, service_name="scrapequeue-worker")


@worker_ready.connect
def _on_ready(**_: object) -> None:
    from scrapequeue.observability.metrics import start_metrics_server

    start_metrics_server(settings.metrics_port)


@worker_shutdown.connect
def _on_shutdown(**_: object) -> None:
    from scrapequeue.workers.browser.pool import shutdown_pool

    shutdown_pool()
