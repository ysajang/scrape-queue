"""Request filtering for browser pages.

Images, fonts, media and analytics are downloaded by a browser and thrown away
by a scraper. Blocking them cuts transfer per page and, more importantly, cuts
the number of third-party hosts contacted. The README reports the measured
difference.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from playwright.sync_api import Route

BLOCKED_TYPES = frozenset({"image", "media", "font"})
BLOCKED_HOSTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.net",
    "hotjar.com",
    "segment.io",
    "dap.digitalgov.gov",
)


@dataclass
class RequestCounts:
    allowed: int = 0
    blocked: int = 0
    blocked_types: dict[str, int] = field(default_factory=dict)


def make_route_filter(counts: RequestCounts):  # noqa: ANN201 - playwright handler
    def handler(route: Route) -> None:
        request = route.request
        blocked_as = None
        if request.resource_type in BLOCKED_TYPES:
            blocked_as = request.resource_type
        elif any(host in request.url for host in BLOCKED_HOSTS):
            blocked_as = "tracker"

        if blocked_as is not None:
            counts.blocked += 1
            counts.blocked_types[blocked_as] = counts.blocked_types.get(blocked_as, 0) + 1
            route.abort()
            return

        counts.allowed += 1
        route.continue_()

    return handler
