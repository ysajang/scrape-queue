import pytest

from scrapequeue.core.urlguard import BlockedURL, assert_allowed

ALLOWED = ["simpler.grants.gov", "api.grants.gov"]


def test_allows_configured_host():
    assert assert_allowed("https://simpler.grants.gov/search", ALLOWED)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1:8000/jobs",  # loopback
        "https://evil.example.com/x",  # not on the allowlist
        "file:///etc/passwd",  # non-http scheme
        "https://simpler.grants.gov:2375/x",  # unexpected port
    ],
)
def test_blocks_ssrf_shapes(url):
    with pytest.raises(BlockedURL):
        assert_allowed(url, ALLOWED, check_dns=False)


def test_dns_check_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr("scrapequeue.core.urlguard._resolve", lambda host: ["10.0.0.5"])
    with pytest.raises(BlockedURL):
        assert_allowed("https://simpler.grants.gov/x", ALLOWED)
