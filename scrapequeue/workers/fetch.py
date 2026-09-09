"""Fetching one page.

Both fetchers pass through the same guards in the same order:

  robots -> SSRF re-check -> circuit breaker -> distributed rate limit -> fetch

The order matters. Checking robots after spending a rate-limit token wastes the
budget; checking the circuit after the fetch defeats its purpose; re-checking
the URL here rather than trusting the API means a DNS change between submission
and execution cannot be used to reach an internal address.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from redis import Redis

from scrapequeue.core.settings import get_settings
from scrapequeue.core.targets import TargetSpec
from scrapequeue.core.urlguard import assert_allowed
from scrapequeue.observability import metrics
from scrapequeue.observability.logging import get_logger
from scrapequeue.pipeline.pageurl import page_body, page_url
from scrapequeue.queue.circuit import CircuitBreaker, domain_of
from scrapequeue.queue.ratelimit import TokenBucket
from scrapequeue.workers import extract

log = get_logger(__name__)

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class TransientFetchError(RuntimeError):
    """Worth retrying: a timeout, a transport error, or a 5xx/429."""


class PermanentFetchError(RuntimeError):
    """Not worth retrying: 4xx other than 429, or a malformed response."""


@dataclass
class PageResult:
    page: int
    url: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    status: int | None = None
    bytes_in: int = 0
    requests_allowed: int = 0
    requests_blocked: int = 0
    duration: float = 0.0
    waited: float = 0.0


def _robots_allows(url: str, user_agent: str) -> bool:
    from scrapekit.robots import RobotsDisallowed
    from scrapekit.robots import assert_allowed as robots_assert

    try:
        robots_assert(url, user_agent)
        return True
    except RobotsDisallowed:
        return False


class Fetcher:
    """Shared guard pipeline. Subclasses implement _fetch only."""

    def __init__(self, spec: TargetSpec, redis: Redis) -> None:
        self.spec = spec
        self.settings = get_settings()
        self.bucket = TokenBucket(redis)
        self.circuit = CircuitBreaker(redis)

    def fetch_page(self, page: int, base_url: str | None = None) -> PageResult:
        url = page_url(self.spec, page, base_url)

        assert_allowed(url, self.settings.allowed_target_hosts)

        if self.spec.respect_robots and not _robots_allows(url, self.settings.user_agent):
            raise PermanentFetchError(f"robots.txt disallows {url}")

        self.circuit.assert_closed(url)

        domain = domain_of(url)
        waited = self.bucket.wait_for_slot(
            f"domain:{domain}", rate_per_second=self.spec.rate_per_second, timeout=120.0
        )
        metrics.RATE_LIMIT_WAIT.labels(domain=domain).observe(waited)

        started = time.monotonic()
        try:
            result = self._fetch(page, url)
        except Exception:
            self.circuit.record_failure(url)
            raise
        self.circuit.record_success(url)

        result.waited = waited
        result.duration = time.monotonic() - started
        metrics.PAGE_DURATION.labels(target=self.spec.name, fetcher=self.spec.fetcher).observe(
            result.duration
        )
        metrics.PAGES_FETCHED.labels(target=self.spec.name, fetcher=self.spec.fetcher).inc()
        metrics.ROWS_EXTRACTED.labels(target=self.spec.name).inc(len(result.rows))
        return result

    def _fetch(self, page: int, url: str) -> PageResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def close(self) -> None:
        pass


class HttpFetcher(Fetcher):
    def __init__(self, spec: TargetSpec, redis: Redis, timeout: float = 30.0) -> None:
        super().__init__(spec, redis)
        self._client = httpx.Client(
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    def _request(self, page: int, url: str) -> httpx.Response:
        try:
            if self.spec.method == "POST":
                return self._client.post(url, json=page_body(self.spec, page))
            return self._client.get(url)
        except httpx.TimeoutException as exc:
            raise TransientFetchError(f"timeout fetching {url}") from exc
        except httpx.TransportError as exc:
            raise TransientFetchError(f"transport error fetching {url}: {exc}") from exc

    def _fetch(self, page: int, url: str) -> PageResult:
        response = self._request(page, url)
        if response.status_code in RETRYABLE_STATUS:
            raise TransientFetchError(f"{response.status_code} from {url}")
        if response.status_code >= 400:
            raise PermanentFetchError(f"{response.status_code} from {url}")

        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            rows = extract.rows_from_json(response.json(), self.spec)
        else:
            rows = extract.rows_from_html(response.text, self.spec, url)

        return PageResult(
            page=page,
            url=url,
            rows=rows,
            status=response.status_code,
            bytes_in=len(response.content),
        )

    def close(self) -> None:
        self._client.close()


class BrowserFetcher(Fetcher):
    def __init__(self, spec: TargetSpec, redis: Redis, har_path: str | None = None) -> None:
        super().__init__(spec, redis)
        self._har_path = har_path

    def _fetch(self, page: int, url: str) -> PageResult:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        from scrapequeue.workers.browser.blocking import RequestCounts
        from scrapequeue.workers.browser.pool import job_page, report_memory

        transferred = 0
        counts = RequestCounts()

        with job_page(har_path=self._har_path, counts=counts) as browser_page:

            def _count(response: Any) -> None:
                nonlocal transferred
                try:
                    transferred += int(response.headers.get("content-length", 0))
                except (TypeError, ValueError):
                    pass

            browser_page.on("response", _count)
            try:
                response = browser_page.goto(url, wait_until="domcontentloaded")
                if self.spec.wait_for_selector:
                    browser_page.wait_for_selector(self.spec.wait_for_selector, state="attached")
                rows = extract.rows_from_page(browser_page, self.spec)
                status = response.status if response is not None else None
            except PlaywrightTimeout as exc:
                raise TransientFetchError(f"timeout rendering {url}") from exc
            except PlaywrightError as exc:
                raise TransientFetchError(f"browser error on {url}: {exc}") from exc

        report_memory(worker="browser")

        if status is not None and status in RETRYABLE_STATUS:
            raise TransientFetchError(f"{status} from {url}")
        if status is not None and status >= 400:
            raise PermanentFetchError(f"{status} from {url}")

        return PageResult(
            page=page,
            url=url,
            rows=rows,
            status=status,
            bytes_in=transferred,
            requests_allowed=counts.allowed,
            requests_blocked=counts.blocked,
        )


def build_fetcher(spec: TargetSpec, redis: Redis, **kwargs: Any) -> Fetcher:
    if spec.fetcher == "browser":
        return BrowserFetcher(spec, redis, **kwargs)
    return HttpFetcher(spec, redis, **kwargs)
