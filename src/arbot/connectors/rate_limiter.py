"""Exchange-specific rate limiting with multiple policy strategies.

Supports weight-based, count-based, token-bucket, and per-endpoint rate limiting
to comply with the varying rate limit policies across cryptocurrency exchanges.
"""

import asyncio
import enum
import time

from arbot.logging import get_logger

logger = get_logger("rate_limiter")


class RateLimitPolicy(enum.Enum):
    """Rate limiting policy type."""

    WEIGHT = "weight"
    COUNT = "count"
    TOKEN_BUCKET = "token_bucket"
    PER_ENDPOINT = "per_endpoint"


class RateLimiter:
    """Async-compatible rate limiter supporting multiple policy types.

    For WEIGHT and COUNT policies, a sliding window approach is used.
    For TOKEN_BUCKET, tokens are refilled continuously at a fixed rate.

    Args:
        policy: The rate limiting strategy to use.
        limit: Maximum allowed requests/weight per window (WEIGHT, COUNT, PER_ENDPOINT).
        window_seconds: Time window in seconds (WEIGHT, COUNT, PER_ENDPOINT).
        capacity: Maximum token capacity (TOKEN_BUCKET only).
        refill_rate: Tokens refilled per second (TOKEN_BUCKET only).
    """

    def __init__(
        self,
        policy: RateLimitPolicy,
        limit: int = 100,
        window_seconds: float = 60.0,
        capacity: int | None = None,
        refill_rate: float | None = None,
    ) -> None:
        self._policy = policy
        self._limit = limit
        self._window_seconds = window_seconds
        self._lock = asyncio.Lock()

        if policy == RateLimitPolicy.TOKEN_BUCKET:
            self._capacity = capacity if capacity is not None else limit
            self._refill_rate = refill_rate if refill_rate is not None else 1.0
            self._tokens = float(self._capacity)
            self._last_refill = time.monotonic()
            # Not used for token bucket
            self._requests: list[tuple[float, int]] = []
        else:
            # Sliding window: store (timestamp, weight) entries
            self._requests = []
            self._capacity = 0
            self._refill_rate = 0.0
            self._tokens = 0.0
            self._last_refill = 0.0

    @property
    def available(self) -> int:
        """Number of available units (tokens/weight/count) right now."""
        if self._policy == RateLimitPolicy.TOKEN_BUCKET:
            self._refill_tokens()
            return int(self._tokens)
        else:
            self._clean_expired()
            used = sum(w for _, w in self._requests)
            return max(0, self._limit - used)

    @property
    def wait_time(self) -> float:
        """Estimated seconds until at least 1 unit becomes available.

        Returns 0.0 if units are available now.
        """
        if self._policy == RateLimitPolicy.TOKEN_BUCKET:
            self._refill_tokens()
            if self._tokens >= 1.0:
                return 0.0
            deficit = 1.0 - self._tokens
            return deficit / self._refill_rate

        self._clean_expired()
        used = sum(w for _, w in self._requests)
        if used < self._limit:
            return 0.0

        # Find the earliest request that would expire
        if self._requests:
            oldest_time = self._requests[0][0]
            return max(0.0, oldest_time + self._window_seconds - time.monotonic())
        return 0.0

    async def acquire(self, weight: int = 1) -> None:
        """Wait until capacity is available, then consume units.

        Blocks until sufficient capacity is free.

        Args:
            weight: Number of units to consume.
        """
        while True:
            async with self._lock:
                if self._try_consume(weight):
                    return
                wait = self._compute_wait(weight)

            await asyncio.sleep(wait)

    def try_acquire(self, weight: int = 1) -> bool:
        """Try to consume units without waiting.

        Args:
            weight: Number of units to consume.

        Returns:
            True if units were consumed, False if capacity is insufficient.
        """
        return self._try_consume(weight)

    def reset(self) -> None:
        """Reset the rate limiter to its initial state."""
        if self._policy == RateLimitPolicy.TOKEN_BUCKET:
            self._tokens = float(self._capacity)
            self._last_refill = time.monotonic()
        else:
            self._requests.clear()

    def _try_consume(self, weight: int) -> bool:
        """Attempt to consume units. Returns True if successful."""
        if self._policy == RateLimitPolicy.TOKEN_BUCKET:
            self._refill_tokens()
            if self._tokens >= weight:
                self._tokens -= weight
                return True
            return False
        else:
            self._clean_expired()
            used = sum(w for _, w in self._requests)
            if used + weight <= self._limit:
                self._requests.append((time.monotonic(), weight))
                return True
            return False

    def _compute_wait(self, weight: int) -> float:
        """Compute how long to wait before retrying acquisition."""
        if self._policy == RateLimitPolicy.TOKEN_BUCKET:
            self._refill_tokens()
            deficit = weight - self._tokens
            if deficit <= 0:
                return 0.0
            return deficit / self._refill_rate

        self._clean_expired()
        if self._requests:
            oldest_time = self._requests[0][0]
            return max(0.01, oldest_time + self._window_seconds - time.monotonic())
        return 0.01

    def _refill_tokens(self) -> None:
        """Refill tokens based on elapsed time (token bucket only)."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            float(self._capacity),
            self._tokens + elapsed * self._refill_rate,
        )
        self._last_refill = now

    def _clean_expired(self) -> None:
        """Remove requests that have fallen outside the sliding window."""
        cutoff = time.monotonic() - self._window_seconds
        while self._requests and self._requests[0][0] < cutoff:
            self._requests.pop(0)


class RateLimiterFactory:
    """Factory for creating exchange-specific rate limiters.

    Exchange rate limit configurations:
        - binance: weight 1200/60s
        - bybit: count 600/5s
        - okx: per_endpoint 20/2s
        - kraken: token_bucket capacity=15, refill=0.33/s
        - upbit: count 10/1s
    """

    # Default configs per exchange
    _EXCHANGE_CONFIGS: dict[str, dict] = {
        "binance": {
            "policy": RateLimitPolicy.WEIGHT,
            "limit": 1200,
            "window_seconds": 60.0,
        },
        "bybit": {
            "policy": RateLimitPolicy.COUNT,
            "limit": 600,
            "window_seconds": 5.0,
        },
        "okx": {
            "policy": RateLimitPolicy.PER_ENDPOINT,
            "limit": 20,
            "window_seconds": 2.0,
        },
        "kraken": {
            "policy": RateLimitPolicy.TOKEN_BUCKET,
            "capacity": 15,
            "refill_rate": 0.33,
        },
        "upbit": {
            "policy": RateLimitPolicy.COUNT,
            "limit": 10,
            "window_seconds": 1.0,
        },
    }

    @staticmethod
    def create(exchange_name: str, config: dict | None = None) -> RateLimiter:
        """Create a rate limiter for the specified exchange.

        Uses built-in defaults if no config is provided. Custom config
        overrides can be passed to adjust limits.

        Args:
            exchange_name: Exchange identifier (e.g. "binance").
            config: Optional config overrides. Keys depend on policy type:
                - weight/count/per_endpoint: limit, window_seconds
                - token_bucket: capacity, refill_rate

        Returns:
            A configured RateLimiter instance.

        Raises:
            ValueError: If the exchange is unknown and no config is provided.
        """
        exchange_lower = exchange_name.lower()

        # Merge defaults with overrides
        defaults = RateLimiterFactory._EXCHANGE_CONFIGS.get(exchange_lower, {})
        if not defaults and config is None:
            raise ValueError(
                f"Unknown exchange '{exchange_name}' and no config provided. "
                f"Known exchanges: {list(RateLimiterFactory._EXCHANGE_CONFIGS.keys())}"
            )

        merged = {**defaults, **(config or {})}

        # Parse policy if it's a string
        policy = merged.get("policy", RateLimitPolicy.COUNT)
        if isinstance(policy, str):
            policy = RateLimitPolicy(policy)

        if policy == RateLimitPolicy.TOKEN_BUCKET:
            return RateLimiter(
                policy=policy,
                capacity=merged.get("capacity", 15),
                refill_rate=merged.get("refill_rate", 1.0),
            )
        else:
            return RateLimiter(
                policy=policy,
                limit=merged.get("limit", 100),
                window_seconds=merged.get("window_seconds", 60.0),
            )
