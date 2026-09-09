"""Turning a page number into a request.

Every page of a run must be addressable on its own. That is what lets a page be
an independent Celery task: retryable in isolation, resumable after a worker
dies, and safe to redeliver. Click-driven pagination cannot offer this, because
page 7 only exists after six clicks in one browser session, so a target whose
pages are not URL-addressable is out of scope by design rather than by omission.
See docs/adr/0004-page-level-checkpoint.md.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from scrapequeue.core.targets import TargetSpec


class UnsupportedPagination(ValueError):
    pass


def page_value(spec: TargetSpec, page: int) -> int:
    """Map a 1-based page number onto the value the site expects."""
    if page < 1:
        raise ValueError("page numbers start at 1")
    pag = spec.pagination
    return pag.start + (page - 1) * pag.step


def page_url(spec: TargetSpec, page: int, base_url: str | None = None) -> str:
    url = base_url or str(spec.start_url)
    pag = spec.pagination
    if pag.kind not in ("query_param", "offset"):
        raise UnsupportedPagination(
            f"{spec.name}: pagination kind {pag.kind!r} is not URL-addressable"
        )
    if not pag.param:
        raise UnsupportedPagination(f"{spec.name}: pagination.param is required")

    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if spec.method == "GET":
        # Static query parameters live in the spec (per_page, sort order); the
        # page parameter is layered on top so a caller-supplied base URL can
        # still override them.
        query = {**{k: str(v) for k, v in spec.params.items()}, **query}
    query[pag.param] = str(page_value(spec, page))
    return urlunparse(parsed._replace(query=urlencode(query)))


def page_body(spec: TargetSpec, page: int) -> dict[str, Any]:
    """Request body for POST-based JSON targets."""
    body = dict(spec.params)
    if spec.pagination.param:
        body[spec.pagination.param] = page_value(spec, page)
    return body
