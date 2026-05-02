from __future__ import annotations

from fastdl_upload_bot.rate_limit import RateLimiter


def test_rate_limiter_allows_requests_within_limit() -> None:
    limiter = RateLimiter(max_requests=2, window_seconds=60)

    assert limiter.check(user_id=1, bucket="upload:map").allowed
    assert limiter.check(user_id=1, bucket="upload:map").allowed


def test_rate_limiter_rejects_requests_over_limit() -> None:
    limiter = RateLimiter(max_requests=1, window_seconds=60)

    assert limiter.check(user_id=1, bucket="upload:map").allowed
    rejected = limiter.check(user_id=1, bucket="upload:map")

    assert not rejected.allowed
    assert rejected.retry_after_seconds > 0


def test_rate_limiter_uses_separate_user_and_bucket_limits() -> None:
    limiter = RateLimiter(max_requests=1, window_seconds=60)

    assert limiter.check(user_id=1, bucket="upload:map").allowed
    assert limiter.check(user_id=2, bucket="upload:map").allowed
    assert limiter.check(user_id=1, bucket="validate:map").allowed


def test_rate_limiter_can_be_disabled() -> None:
    limiter = RateLimiter(max_requests=0, window_seconds=60)

    assert limiter.check(user_id=1, bucket="upload:map").allowed
    assert limiter.check(user_id=1, bucket="upload:map").allowed
