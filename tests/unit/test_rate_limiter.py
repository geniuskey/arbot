"""Unit tests for the rate limiter module."""

import asyncio
import time

import pytest

from arbot.connectors.rate_limiter import RateLimiter, RateLimiterFactory, RateLimitPolicy


# ---------------------------------------------------------------------------
# RateLimiter - COUNT policy tests
# ---------------------------------------------------------------------------


class TestCountPolicy:
    """Tests for count-based rate limiting (e.g. Upbit, Bybit)."""

    def test_try_acquire_within_limit(self) -> None:
        limiter = RateLimiter(policy=RateLimitPolicy.COUNT, limit=5, window_seconds=1.0)
        for _ in range(5):
            assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

    def test_available_tracks_usage(self) -> None:
        limiter = RateLimiter(policy=RateLimitPolicy.COUNT, limit=10, window_seconds=1.0)
        assert limiter.available == 10
        limiter.try_acquire(3)
        assert limiter.available == 7

    def test_wait_time_zero_when_available(self) -> None:
        limiter = RateLimiter(policy=RateLimitPolicy.COUNT, limit=5, window_seconds=1.0)
        assert limiter.wait_time == 0.0

    def test_wait_time_positive_when_exhausted(self) -> None:
        limiter = RateLimiter(policy=RateLimitPolicy.COUNT, limit=1, window_seconds=1.0)
        limiter.try_acquire()
        assert limiter.wait_time > 0.0

    def test_reset_restores_capacity(self) -> None:
        limiter = RateLimiter(policy=RateLimitPolicy.COUNT, limit=5, window_seconds=1.0)
        for _ in range(5):
            limiter.try_acquire()
        assert limiter.available == 0
        limiter.reset()
        assert limiter.available == 5

    @pytest.mark.asyncio
    async def test_acquire_blocks_until_available(self) -> None:
        limiter = RateLimiter(policy=RateLimitPolicy.COUNT, limit=1, window_seconds=0.1)
        limiter.try_acquire()

        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.08  # Should have waited ~0.1s

    def test_weighted_acquire(self) -> None:
        limiter = RateLimiter(policy=RateLimitPolicy.COUNT, limit=10, window_seconds=1.0)
        assert limiter.try_acquire(5) is True
        assert limiter.available == 5
        assert limiter.try_acquire(6) is False
        assert limiter.try_acquire(5) is True
        assert limiter.available == 0


# ---------------------------------------------------------------------------
# RateLimiter - WEIGHT policy tests
# ---------------------------------------------------------------------------


class TestWeightPolicy:
    """Tests for weight-based rate limiting (e.g. Binance)."""

    def test_weight_consumption(self) -> None:
        limiter = RateLimiter(policy=RateLimitPolicy.WEIGHT, limit=1200, window_seconds=60.0)
        assert limiter.try_acquire(100) is True
        assert limiter.available == 1100
        assert limiter.try_acquire(1101) is False
        assert limiter.try_acquire(1100) is True
        assert limiter.available == 0

    def test_weight_recovery_over_time(self) -> None:
        limiter = RateLimiter(policy=RateLimitPolicy.WEIGHT, limit=10, window_seconds=0.05)
        for _ in range(10):
            limiter.try_acquire()
        assert limiter.available == 0

        # Wait for window to expire
        time.sleep(0.06)
        assert limiter.available == 10


# ---------------------------------------------------------------------------
# RateLimiter - TOKEN_BUCKET policy tests
# ---------------------------------------------------------------------------


class TestTokenBucketPolicy:
    """Tests for token bucket rate limiting (e.g. Kraken)."""

    def test_initial_capacity(self) -> None:
        limiter = RateLimiter(
            policy=RateLimitPolicy.TOKEN_BUCKET, capacity=15, refill_rate=0.33
        )
        assert limiter.available == 15

    def test_consume_tokens(self) -> None:
        limiter = RateLimiter(
            policy=RateLimitPolicy.TOKEN_BUCKET, capacity=15, refill_rate=0.33
        )
        assert limiter.try_acquire(10) is True
        assert limiter.available == 5
        assert limiter.try_acquire(6) is False

    def test_token_refill(self) -> None:
        limiter = RateLimiter(
            policy=RateLimitPolicy.TOKEN_BUCKET, capacity=15, refill_rate=100.0
        )
        limiter.try_acquire(15)
        assert limiter.available == 0

        # With refill_rate=100/s, waiting 0.05s should refill ~5 tokens
        time.sleep(0.05)
        avail = limiter.available
        assert avail >= 3  # Allow some timing slack

    def test_tokens_do_not_exceed_capacity(self) -> None:
        limiter = RateLimiter(
            policy=RateLimitPolicy.TOKEN_BUCKET, capacity=10, refill_rate=100.0
        )
        time.sleep(0.05)
        assert limiter.available <= 10

    def test_reset_refills_to_capacity(self) -> None:
        limiter = RateLimiter(
            policy=RateLimitPolicy.TOKEN_BUCKET, capacity=15, refill_rate=0.33
        )
        limiter.try_acquire(15)
        assert limiter.available == 0
        limiter.reset()
        assert limiter.available == 15

    def test_wait_time_token_bucket(self) -> None:
        limiter = RateLimiter(
            policy=RateLimitPolicy.TOKEN_BUCKET, capacity=1, refill_rate=1.0
        )
        limiter.try_acquire()
        wt = limiter.wait_time
        assert wt > 0.0
        assert wt <= 1.1  # Should need ~1s to refill 1 token

    @pytest.mark.asyncio
    async def test_acquire_waits_for_refill(self) -> None:
        limiter = RateLimiter(
            policy=RateLimitPolicy.TOKEN_BUCKET, capacity=1, refill_rate=20.0
        )
        limiter.try_acquire()

        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.03  # Should wait ~0.05s for 1 token at 20/s


# ---------------------------------------------------------------------------
# RateLimiter - PER_ENDPOINT policy tests
# ---------------------------------------------------------------------------


class TestPerEndpointPolicy:
    """Tests for per-endpoint rate limiting (e.g. OKX)."""

    def test_per_endpoint_basic(self) -> None:
        limiter = RateLimiter(
            policy=RateLimitPolicy.PER_ENDPOINT, limit=20, window_seconds=2.0
        )
        for _ in range(20):
            assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False
        assert limiter.available == 0


# ---------------------------------------------------------------------------
# RateLimiterFactory tests
# ---------------------------------------------------------------------------


class TestRateLimiterFactory:
    """Tests for the rate limiter factory."""

    def test_create_binance(self) -> None:
        limiter = RateLimiterFactory.create("binance")
        assert limiter._policy == RateLimitPolicy.WEIGHT
        assert limiter._limit == 1200

    def test_create_bybit(self) -> None:
        limiter = RateLimiterFactory.create("bybit")
        assert limiter._policy == RateLimitPolicy.COUNT
        assert limiter._limit == 600

    def test_create_okx(self) -> None:
        limiter = RateLimiterFactory.create("okx")
        assert limiter._policy == RateLimitPolicy.PER_ENDPOINT
        assert limiter._limit == 20

    def test_create_kraken(self) -> None:
        limiter = RateLimiterFactory.create("kraken")
        assert limiter._policy == RateLimitPolicy.TOKEN_BUCKET
        assert limiter._capacity == 15

    def test_create_upbit(self) -> None:
        limiter = RateLimiterFactory.create("upbit")
        assert limiter._policy == RateLimitPolicy.COUNT
        assert limiter._limit == 10

    def test_create_case_insensitive(self) -> None:
        limiter = RateLimiterFactory.create("Binance")
        assert limiter._policy == RateLimitPolicy.WEIGHT

    def test_create_unknown_exchange_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown exchange"):
            RateLimiterFactory.create("unknown_exchange")

    def test_create_unknown_exchange_with_config(self) -> None:
        limiter = RateLimiterFactory.create(
            "custom_exchange",
            config={"policy": "count", "limit": 50, "window_seconds": 10.0},
        )
        assert limiter._policy == RateLimitPolicy.COUNT
        assert limiter._limit == 50

    def test_create_with_config_overrides(self) -> None:
        limiter = RateLimiterFactory.create("binance", config={"limit": 600})
        assert limiter._policy == RateLimitPolicy.WEIGHT
        assert limiter._limit == 600
