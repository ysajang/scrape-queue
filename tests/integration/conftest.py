"""Integration fixtures: a real database, a real broker, real object storage.

Every table is truncated between tests rather than recreated: schema creation is
Alembic's job and running it per test would hide a broken migration.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def engine():
    from scrapequeue.db.session import get_engine

    engine = get_engine()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return engine


@pytest.fixture
def db(engine):
    from scrapequeue.db.session import session_scope

    with session_scope() as session:
        session.execute(
            text(
                "TRUNCATE jobs, job_pages, failed_pages, dead_letters, "
                "target_snapshots, drift_baselines RESTART IDENTITY CASCADE"
            )
        )
    with session_scope() as session:
        yield session


@pytest.fixture
def bucket():
    from scrapequeue.storage import s3

    s3.ensure_bucket()
    return s3


@pytest.fixture
def eager_celery():
    from scrapequeue.queue.celery_app import app

    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
    yield app
    app.conf.task_always_eager = False
    app.conf.task_eager_propagates = False


@pytest.fixture
def make_job():
    from scrapequeue.core.states import JobState
    from scrapequeue.db import repo
    from scrapequeue.db.session import session_scope

    def factory(**overrides):
        fields = {
            "state": JobState.PENDING,
            "target": "federal_register",
            "fetcher": "http",
            "start_url": "https://www.federalregister.gov/api/v1/documents.json",
            "max_pages": 2,
            "output_format": "csv",
            "incremental": False,
            "params": {},
            "api_key_id": "test",
            "idempotency_key": None,
            "callback_url": None,
        }
        fields.update(overrides)
        with session_scope() as session:
            job = repo.create_job(session, **fields)
            return uuid.UUID(str(job.id))

    return factory
