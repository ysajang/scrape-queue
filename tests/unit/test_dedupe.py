from scrapequeue.core.targets import load_target
from scrapequeue.pipeline.dedupe import dedupe, row_hash


def test_dedupe_uses_the_declared_key():
    spec = load_target("federal_register")
    rows = [
        {"document_number": "a", "title": "one"},
        {"document_number": "a", "title": "one again"},
        {"document_number": "b", "title": "two"},
    ]
    result = dedupe(rows, spec)
    assert [r["document_number"] for r in result.rows] == ["a", "b"]
    assert result.duplicates == 1


def test_without_a_key_every_row_is_kept():
    spec = load_target("federal_register").model_copy(deep=True)
    spec.extraction.row_key = []
    rows = [{"document_number": "a"}, {"document_number": "a"}]
    assert len(dedupe(rows, spec).rows) == 2


def test_row_hash_ignores_key_order():
    assert row_hash({"a": 1, "b": 2}) == row_hash({"b": 2, "a": 1})


def test_row_hash_changes_with_content():
    assert row_hash({"a": 1}) != row_hash({"a": 2})
