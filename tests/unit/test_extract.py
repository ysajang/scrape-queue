"""Selector regression.

The fixtures are real captures of the two live targets. If a site changes its
markup, this suite fails in CI without a browser and without touching the site,
which is the cheap half of the drift problem; the other half is caught at run
time by pipeline.drift.
"""

from scrapequeue.core.targets import load_target
from scrapequeue.workers.extract import columns, row_identity, rows_from_html, rows_from_json


def test_html_selectors_still_match(grants_html):
    spec = load_target("grants_browser")
    rows = rows_from_html(grants_html, spec, "https://simpler.grants.gov/search?page=1")
    assert len(rows) == 25
    first = rows[0]
    assert set(first) == set(columns(spec))
    assert first["title"]
    assert first["detail_url"].startswith("https://simpler.grants.gov/opportunity/")


def test_html_cells_exclude_responsive_headers(grants_html):
    """The table repeats each column label inside the cell for narrow screens.
    Selecting the whole cell would produce 'TitleCooperative Agreement...'."""
    spec = load_target("grants_browser")
    rows = rows_from_html(grants_html, spec, "https://simpler.grants.gov/search")
    assert not any(row["title"].startswith("Title") for row in rows if row["title"])


def test_json_paths_still_match(federal_register_json):
    spec = load_target("federal_register")
    rows = rows_from_json(federal_register_json, spec)
    assert len(rows) == 20
    assert rows[0]["document_number"]


def test_row_identity_uses_declared_key():
    spec = load_target("federal_register")
    assert row_identity({"document_number": "2026-1", "title": "x"}, spec) == "2026-1"
