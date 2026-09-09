"""One browser per worker process, one context per job.

Launching Chromium costs a second or more, so a browser that lives for the
process pays that once instead of once per page. Contexts are cheap and are what
provide isolation, so each job gets its own and closes it in a finally block:
a leaked context is a leaked set of pages, and the memory shows up as a worker
that grows all day.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from scrapequeue.core.settings import get_settings
from scrapequeue.observability import metrics
from scrapequeue.observability.logging import get_logger
from scrapequeue.workers.browser.blocking import RequestCounts, make_route_filter

log = get_logger(__name__)

_lock = threading.Lock()
_playwright = None
_browser: Browser | None = None


def _launch() -> Browser:
    global _playwright, _browser
    if _browser is not None and _browser.is_connected():
        return _browser
    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.launch(
        args=[
            # The container already isolates the process; Chromium's own sandbox
            # needs privileges the read-only container does not grant.
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-background-networking",
        ]
    )
    log.info("browser.launched", pid=os.getpid())
    return _browser


def get_browser() -> Browser:
    with _lock:
        return _launch()


@contextmanager
def job_context(
    *, har_path: str | None = None, counts: RequestCounts | None = None
) -> Iterator[BrowserContext]:
    settings = get_settings()
    browser = get_browser()
    context = browser.new_context(
        user_agent=settings.user_agent,
        viewport={"width": 1280, "height": 900},
        java_script_enabled=True,
        ignore_https_errors=settings.browser_ignore_https_errors,
        record_har_path=har_path,
        record_har_content="omit" if har_path else None,
    )
    context.set_default_timeout(30_000)
    context.route("**/*", make_route_filter(counts if counts is not None else RequestCounts()))
    try:
        yield context
    finally:
        context.close()


@contextmanager
def job_page(*, har_path: str | None = None, counts: RequestCounts | None = None) -> Iterator[Page]:
    with job_context(har_path=har_path, counts=counts) as context:
        page = context.new_page()
        try:
            yield page
        finally:
            page.close()


def report_memory(worker: str) -> None:
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    metrics.BROWSER_RSS.labels(worker=worker).set(kb * 1024)
                    return
    except OSError:
        pass


def shutdown_pool() -> None:
    """Called from the Celery worker_shutdown signal. A browser left running
    after the worker exits becomes an orphan process holding memory."""
    global _playwright, _browser
    with _lock:
        try:
            if _browser is not None:
                _browser.close()
        finally:
            _browser = None
            if _playwright is not None:
                _playwright.stop()
                _playwright = None
    log.info("browser.stopped", pid=os.getpid())
