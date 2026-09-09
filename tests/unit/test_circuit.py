import pytest

from scrapequeue.queue.circuit import CircuitBreaker, CircuitOpen, CircuitState

URL = "https://api.grants.gov/v1/api/search2"


def test_stays_closed_below_threshold(redis):
    breaker = CircuitBreaker(redis)
    for _ in range(4):
        assert breaker.record_failure(URL) is CircuitState.CLOSED
    assert breaker.assert_closed(URL) is CircuitState.CLOSED


def test_opens_at_threshold_and_lets_exactly_one_probe_through(redis):
    breaker = CircuitBreaker(redis)
    for _ in range(5):
        breaker.record_failure(URL)
    assert breaker.assert_closed(URL) is CircuitState.HALF_OPEN
    with pytest.raises(CircuitOpen):
        breaker.assert_closed(URL)


def test_success_closes_the_circuit(redis):
    breaker = CircuitBreaker(redis)
    for _ in range(5):
        breaker.record_failure(URL)
    breaker.record_success(URL)
    assert breaker.state("api.grants.gov")[0] is CircuitState.CLOSED


def test_circuits_are_per_domain(redis):
    breaker = CircuitBreaker(redis)
    for _ in range(5):
        breaker.record_failure(URL)
    assert breaker.assert_closed("https://www.federalregister.gov/api") is CircuitState.CLOSED
