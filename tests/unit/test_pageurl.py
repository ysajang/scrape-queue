import pytest

from scrapequeue.core.targets import load_target
from scrapequeue.pipeline.pageurl import (
    UnsupportedPagination,
    page_body,
    page_url,
    page_value,
)


def test_query_param_pagination():
    spec = load_target("grants_browser")
    assert page_url(spec, 1).endswith("page=1")
    assert page_url(spec, 7).endswith("page=7")


def test_offset_pagination_multiplies_by_step():
    spec = load_target("grants_api")
    assert page_value(spec, 1) == 0
    assert page_value(spec, 3) == 50
    assert page_body(spec, 3)["startRecordNum"] == 50


def test_static_params_are_kept_for_get_targets():
    spec = load_target("federal_register")
    url = page_url(spec, 2)
    assert "per_page=20" in url and "page=2" in url


def test_page_numbers_start_at_one():
    with pytest.raises(ValueError):
        page_value(load_target("federal_register"), 0)


def test_non_addressable_pagination_is_rejected():
    spec = load_target("federal_register").model_copy(deep=True)
    spec.pagination.kind = "next_link"
    with pytest.raises(UnsupportedPagination):
        page_url(spec, 2)
