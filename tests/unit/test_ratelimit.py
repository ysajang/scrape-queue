import time

from scrapequeue.queue.ratelimit import TokenBucket


def test_bucket_allows_up_to_capacity_then_refuses(redis):
    bucket = TokenBucket(redis)
    decisions = [bucket.consume("d", rate_per_second=2.0, capacity=2.0) for _ in range(4)]
    assert [d.allowed for d in decisions] == [True, True, False, False]


def test_refusal_reports_a_usable_retry_after(redis):
    bucket = TokenBucket(redis)
    for _ in range(2):
        bucket.consume("d", rate_per_second=2.0, capacity=2.0)
    decision = bucket.consume("d", rate_per_second=2.0, capacity=2.0)
    assert 0 < decision.retry_after <= 0.5


def test_bucket_is_shared_across_clients(redis):
    """The point of putting the bucket in Redis: three workers share one budget
    instead of getting one each."""
    worker_a, worker_b = TokenBucket(redis), TokenBucket(redis)
    assert worker_a.consume("d", rate_per_second=1.0, capacity=1.0).allowed
    assert not worker_b.consume("d", rate_per_second=1.0, capacity=1.0).allowed


def test_tokens_refill_over_time(redis):
    bucket = TokenBucket(redis)
    bucket.consume("d", rate_per_second=50.0, capacity=1.0)
    time.sleep(0.1)
    assert bucket.consume("d", rate_per_second=50.0, capacity=1.0).allowed
