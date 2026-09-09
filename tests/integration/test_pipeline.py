"""The three failure shapes the pipeline exists to handle: a timeout, a refusal,
and a partial failure. Each is forced deterministically rather than by hoping a
live site misbehaves during the test run."""

from __future__ import annotations

import pytest

from scrapequeue.core.states import JobState, PageState
from scrapequeue.db import repo
from scrapequeue.db.session import session_scope
from scrapequeue.db.tables import FailedPage
from scrapequeue.workers.fetch import PageResult, PermanentFetchError, TransientFetchError

pytestmark = pytest.mark.integration


class FakeFetcher:
    """Stands in for a real fetcher; each page number gets a scripted outcome."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def fetch_page(self, page, base_url=None):
        self.calls.append(page)
        outcome = self.script[page]
        if isinstance(outcome, Exception):
            raise outcome
        return PageResult(page=page, url=f"https://example.test/{page}", rows=outcome, status=200)

    def close(self):
        pass


def install(monkeypatch, script):
    fetcher = FakeFetcher(script)
    monkeypatch.setattr(
        "scrapequeue.pipeline.crawl.build_fetcher", lambda spec, redis, **kw: fetcher
    )
    return fetcher


def rows(n, offset=0):
    return [{"document_number": f"d{offset + i}", "title": f"t{i}"} for i in range(n)]


def test_timeout_is_retried_then_recorded(monkeypatch, db, bucket, eager_celery, make_job):
    """A timeout is transient: the first attempt asks for a retry, and only the
    attempt that exhausts the budget records a permanent page failure."""
    from celery.exceptions import Retry

    from scrapequeue.pipeline.crawl import fetch_page

    job_id = make_job()
    install(monkeypatch, {1: TransientFetchError("timeout fetching page 1")})

    with pytest.raises(Retry):
        fetch_page.apply(kwargs={"job_id": str(job_id), "page": 1}, throw=True).get()

    result = fetch_page.apply(
        kwargs={"job_id": str(job_id), "page": 1}, retries=fetch_page.max_retries
    ).get()

    assert result["failed"] is True
    with session_scope() as session:
        failure = session.query(FailedPage).filter_by(job_id=job_id).one()
    assert failure.error_type == "TransientFetchError"
    assert failure.attempts >= 1


def test_refusal_is_not_retried(monkeypatch, db, bucket, eager_celery, make_job):
    """robots.txt and 4xx are configuration problems. Retrying them wastes the
    target's bandwidth and never succeeds, so exactly one attempt is made."""
    from scrapequeue.pipeline.crawl import fetch_page

    job_id = make_job()
    fetcher = install(monkeypatch, {1: PermanentFetchError("robots.txt disallows /x")})

    result = fetch_page.apply(kwargs={"job_id": str(job_id), "page": 1}).get()

    assert result["failed"] is True
    assert fetcher.calls == [1]
    with session_scope() as session:
        failure = session.query(FailedPage).filter_by(job_id=job_id).one()
    assert failure.error_type == "PermanentFetchError"


def test_partial_failure_still_produces_output(monkeypatch, db, bucket, eager_celery, make_job):
    from scrapequeue.pipeline.crawl import assemble_job, fetch_page

    job_id = make_job(max_pages=2)
    with session_scope() as session:
        repo.transition(session, repo.get_job(session, job_id), JobState.RUNNING)
    install(monkeypatch, {1: rows(3), 2: PermanentFetchError("404 from page 2")})

    results = [
        fetch_page.apply(kwargs={"job_id": str(job_id), "page": page}).get() for page in (1, 2)
    ]
    outcome = assemble_job.apply(args=[results, str(job_id)]).get()

    assert outcome["rows"] == 3
    with session_scope() as session:
        job = repo.get_job(session, job_id)
    assert job.state == JobState.PARTIAL
    assert job.result_key
    assert job.pages_failed == 1


def test_redelivered_page_does_not_double_count(monkeypatch, db, bucket, eager_celery, make_job):
    from scrapequeue.pipeline.crawl import fetch_page

    job_id = make_job(max_pages=1)
    with session_scope() as session:
        repo.transition(session, repo.get_job(session, job_id), JobState.RUNNING)
    install(monkeypatch, {1: rows(5)})

    first = fetch_page.apply(kwargs={"job_id": str(job_id), "page": 1}).get()
    second = fetch_page.apply(kwargs={"job_id": str(job_id), "page": 1}).get()

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    with session_scope() as session:
        job = repo.get_job(session, job_id)
    assert (job.pages_done, job.rows) == (1, 5)


def test_resume_only_fetches_missing_pages(monkeypatch, db, bucket, eager_celery, make_job):
    from scrapequeue.queue.checkpoint import pages_to_fetch

    job_id = make_job(max_pages=4)
    with session_scope() as session:
        repo.transition(session, repo.get_job(session, job_id), JobState.RUNNING)
        repo.record_page(session, job_id, 1, "u1", state=PageState.PARSED, rows=2)
        repo.record_page(session, job_id, 3, "u3", state=PageState.PARSED, rows=2)

    with session_scope() as session:
        assert pages_to_fetch(session, job_id, 4) == [2, 4]


def test_poison_pill_is_parked(monkeypatch, db, bucket, eager_celery, make_job):
    from scrapequeue.pipeline.crawl import crawl_job

    job_id = make_job(max_pages=1)
    with session_scope() as session:
        job = repo.get_job(session, job_id)
        repo.transition(session, job, JobState.RUNNING)
        job.lost_worker_count = 2  # two workers already died holding it

    outcome = crawl_job.apply(kwargs={"job_id": str(job_id)}).get()

    assert outcome["status"] == "dead_letter"
    with session_scope() as session:
        assert repo.get_job(session, job_id).state == JobState.DEAD_LETTER


def test_finished_job_ignores_a_redelivered_crawl(db, bucket, eager_celery, make_job):
    from scrapequeue.pipeline.crawl import crawl_job

    job_id = make_job()
    with session_scope() as session:
        job = repo.get_job(session, job_id)
        repo.transition(session, job, JobState.RUNNING)
        repo.transition(session, job, JobState.SUCCEEDED)

    outcome = crawl_job.apply(kwargs={"job_id": str(job_id)}).get()
    assert outcome["status"] == JobState.SUCCEEDED
