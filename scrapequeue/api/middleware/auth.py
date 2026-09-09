"""API key authentication with scopes.

Keys are compared with a constant-time function and identified in logs and in
the database by a short hash, never by the key itself.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from scrapequeue.core.settings import Scope, Settings, get_settings

_ORDER: dict[Scope, int] = {"read": 1, "write": 2, "admin": 3}


@dataclass(frozen=True)
class Principal:
    key_id: str
    scope: Scope

    def allows(self, required: Scope) -> bool:
        return _ORDER[self.scope] >= _ORDER[required]


def key_id(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]


def _authenticate(presented: str, settings: Settings) -> Principal:
    for candidate, scope in settings.api_key_scopes().items():
        if hmac.compare_digest(presented, candidate):
            return Principal(key_id=key_id(candidate), scope=scope)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")


def current_principal(
    x_api_key: str = Header(default="", alias="X-API-Key"),
) -> Principal:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-API-Key header")
    return _authenticate(x_api_key, get_settings())


def require_scope(required: Scope) -> Callable[..., Principal]:
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.allows(required):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"scope {required} required")
        return principal

    return dependency
