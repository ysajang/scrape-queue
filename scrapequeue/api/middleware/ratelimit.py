from __future__ import annotations

from fastapi import Depends, HTTPException, Response, status

from scrapequeue.api.deps import get_bucket
from scrapequeue.api.middleware.auth import Principal, current_principal
from scrapequeue.core.settings import get_settings


def enforce_api_rate_limit(
    response: Response,
    principal: Principal = Depends(current_principal),
) -> Principal:
    settings = get_settings()
    rate = settings.api_rate_limit_per_minute / 60.0
    decision = get_bucket().consume(
        f"api:{principal.key_id}",
        rate_per_second=rate,
        capacity=float(settings.api_rate_limit_per_minute),
    )
    if not decision.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "API rate limit exceeded",
            headers={"Retry-After": str(max(1, int(decision.retry_after) + 1))},
        )
    response.headers["X-RateLimit-Limit"] = str(settings.api_rate_limit_per_minute)
    return principal
