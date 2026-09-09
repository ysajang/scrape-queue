from scrapequeue.core.idempotency import fingerprint


def test_fingerprint_ignores_key_order():
    assert fingerprint({"target": "x", "max_pages": 2}) == fingerprint(
        {"max_pages": 2, "target": "x"}
    )


def test_fingerprint_detects_a_changed_body():
    assert fingerprint({"max_pages": 2}) != fingerprint({"max_pages": 3})
