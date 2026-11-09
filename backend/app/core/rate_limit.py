"""Lightweight async sliding-window rate limiter.

Used for per-IP limits that have no natural database row to count (e.g. OTP
verification attempts per IP subnet). It is an in-process limiter: adequate for a
single instance and for deterministic tests. A multi-instance production
deployment should back this with a shared store (Redis); the interface is kept
small so that swap is localised.

Database-derived limits that DO have a natural audit trail (OTP *request* counts
per mobile / per IP within an hour, and the resend cooldown) are enforced by
counting ``otp_challenges`` rows in the auth service rather than here, so they
survive restarts and horizontal scaling.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Fixed-capacity sliding-window limiter keyed by an arbitrary string."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check_and_consume(self, key: str, *, limit: int, window_seconds: float) -> bool:
        """Record a hit for ``key``; return ``True`` if within ``limit``/window."""
        now = time.monotonic()
        cutoff = now - window_seconds
        async with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    async def peek(self, key: str, *, window_seconds: float) -> int:
        """Return the current hit count for ``key`` without consuming."""
        now = time.monotonic()
        cutoff = now - window_seconds
        async with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            return len(bucket)

    def reset(self) -> None:
        """Clear all state (test helper)."""
        self._events.clear()


#: Process-wide limiter instance for auth routes.
auth_rate_limiter = SlidingWindowRateLimiter()
