# 2. Ship an API target and a browser target for the same data

**Status:** accepted

## Context

`grants_browser` scrapes rendered pages from simpler.grants.gov. The same data
is available from `api.grants.gov/v1/api/search2`. A scraping portfolio has an
incentive to hide that and show off the browser.

## Decision

Ship both, name the API in the browser target's `api_alternative` field, and say
in the README that the API is the better choice when one exists.

## Consequences

The browser path has to justify itself on evidence rather than preference. It
does: a plain GET of `/search` returns the Next.js shell with zero result rows,
verified and captured as a test fixture.

Every target must fill in `fetcher_reason`, and a unit test fails the build if a
browser target leaves it empty. This makes "why is this using a browser" a
question the repository answers rather than one a reviewer has to ask.

Measured difference: the API path returns 20 rows in about 50 ms; the browser
path returns 25 rows in about 1.3 seconds and needs a Chromium process.
