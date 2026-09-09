from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends
from redis import Redis
from sqlalchemy.orm import Session

from scrapequeue.core.settings import Settings, get_settings
from scrapequeue.db.session import get_session_factory
from scrapequeue.queue.ratelimit import TokenBucket


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


@lru_cache
def get_bucket() -> TokenBucket:
    return TokenBucket(get_redis())


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def settings_dep() -> Settings:
    return get_settings()


SettingsDep = Depends(settings_dep)
