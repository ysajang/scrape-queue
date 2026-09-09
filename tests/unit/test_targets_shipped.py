"""Every shipped target must load and be internally consistent.

A malformed spec would otherwise surface as a worker crash at run time rather
than a failing build.
"""

import pytest

from scrapequeue.core.settings import get_settings
from scrapequeue.core.targets import list_targets, load_target
from scrapequeue.pipeline.pageurl import page_url


@pytest.mark.parametrize("name", list_targets())
def test_target_loads_and_paginates(name):
    spec = load_target(name)
    assert spec.name == name
    assert spec.extraction.fields
    assert page_url(spec, 2)


@pytest.mark.parametrize("name", list_targets())
def test_target_host_is_on_the_allowlist(name):
    from urllib.parse import urlparse

    host = urlparse(str(load_target(name).start_url)).hostname
    assert host in get_settings().allowed_target_hosts


@pytest.mark.parametrize("name", list_targets())
def test_browser_targets_document_why(name):
    spec = load_target(name)
    if spec.fetcher == "browser":
        assert spec.fetcher_reason.strip(), "a browser target must justify the cost"


@pytest.mark.parametrize("name", list_targets())
def test_row_key_refers_to_declared_columns(name):
    spec = load_target(name)
    known = set(spec.extraction.fields) | set(spec.extraction.attributes)
    assert set(spec.extraction.row_key) <= known
