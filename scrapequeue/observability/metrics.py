"""Prometheus metrics.

The set is chosen so the Grafana dashboard can answer the three questions an
operator actually asks: is the queue draining, which domain is failing, and are
the browser workers leaking memory.
"""

from __future__ import annotations

import threading

from prometheus_client import Counter, Gauge, Histogram, start_http_server

JOBS_SUBMITTED = Counter(
    "sq_jobs_submitted_total", "Jobs accepted by the API", ["target", "fetcher"]
)
JOBS_FINISHED = Counter(
    "sq_jobs_finished_total", "Jobs reaching a terminal state", ["target", "state"]
)
JOB_DURATION = Histogram(
    "sq_job_duration_seconds",
    "Wall time from first task start to terminal state",
    ["target", "fetcher"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800),
)
PAGES_FETCHED = Counter("sq_pages_fetched_total", "Pages fetched", ["target", "fetcher"])
PAGE_FAILURES = Counter("sq_page_failures_total", "Page failures", ["target", "error_type"])
PAGE_DURATION = Histogram(
    "sq_page_duration_seconds",
    "Per-page fetch time",
    ["target", "fetcher"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
ROWS_EXTRACTED = Counter("sq_rows_extracted_total", "Rows written", ["target"])
QUEUE_DEPTH = Gauge("sq_queue_depth", "Messages waiting in a queue", ["queue"])
CIRCUIT_STATE = Gauge("sq_circuit_state", "0 closed, 1 half-open, 2 open", ["domain"])
RATE_LIMIT_WAIT = Histogram(
    "sq_rate_limit_wait_seconds",
    "Time a task spent waiting on the domain token bucket",
    ["domain"],
    buckets=(0.01, 0.1, 0.5, 1, 2, 5, 10),
)
DEAD_LETTERS = Counter("sq_dead_letters_total", "Jobs parked for an operator", ["reason"])
DRIFT_SUSPECTS = Counter(
    "sq_drift_suspects_total", "Runs rejected by the drift detector", ["target"]
)
BROWSER_RSS = Gauge("sq_browser_rss_bytes", "Resident memory of the browser process", ["worker"])
WEBHOOK_ATTEMPTS = Counter("sq_webhook_attempts_total", "Callback deliveries", ["outcome"])

_started = threading.Event()


def start_metrics_server(port: int) -> None:
    """Idempotent: worker_ready can fire more than once in a process lifetime."""
    if _started.is_set():
        return
    try:
        start_http_server(port)
        _started.set()
    except OSError:
        # Another process in the same container already bound the port.
        pass
