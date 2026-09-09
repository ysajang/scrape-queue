"""Shared fixtures.

Unit tests must not need a database, a broker or a network: they run on every
push and a suite that needs infrastructure gets skipped, which is the same as
not having it. Anything that needs real services is marked integration.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://scrape:scrape@localhost:5432/scrape")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REDBEAT_REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("S3_ENDPOINT", "http://127.0.0.1:9000")
os.environ.setdefault("S3_BUCKET", "scrape-results")
os.environ.setdefault("S3_ACCESS_KEY", "minio")
os.environ.setdefault("S3_SECRET_KEY", "minio12345")
os.environ.setdefault("API_KEYS", "test-read:read,test-write:write,test-admin:admin")
os.environ.setdefault("WEBHOOK_SECRET", "test-secret")


@pytest.fixture
def redis():
    import fakeredis

    return fakeredis.FakeStrictRedis(decode_responses=True)


@pytest.fixture
def grants_html() -> str:
    return (FIXTURES / "html" / "grants_browser_page1.html").read_text(encoding="utf-8")


@pytest.fixture
def federal_register_json() -> dict:
    import json

    return json.loads((FIXTURES / "json" / "federal_register_page1.json").read_text("utf-8"))
