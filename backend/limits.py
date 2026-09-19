"""In-memory sliding-window rate limiter.

Implements a per-IP rate limiter using a sliding window algorithm. This is
suitable for single-process deployments; for distributed systems, consider
a Redis-backed implementation with the same interface.

Codes are the only gate on a share, so rate limiting lookups prevents
brute-forcing the code space. The API surface is deliberately minimal to
allow implementation swapping without touching route code.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """In-memory rate limiter using a sliding window algorithm.

    Thread-safe. Each bucket tracks request timestamps within the window,
    removing stale entries as time progresses.
    """

    def __init__(self) -> None:
        """Initialize the rate limiter."""
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock: threading.Lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, float]:
        """Check if a request is allowed under the rate limit.

        Uses a sliding window: tracks timestamps within `window_seconds` and
        allows the request if fewer than `limit` requests have been made
        in that window.

        Args:
            key: Rate limit bucket key (typically "bucket:ip")
            limit: Maximum allowed requests in the window (0 = unlimited)
            window_seconds: Time window in seconds

        Returns:
            Tuple of (allowed: bool, retry_after_seconds: float).
            If allowed=False, retry_after_seconds is how long to wait before retrying.
        """
        if limit <= 0:
            return True, 0.0
        now = time.time()
        with self._lock:
            dq = self._hits[key]
            # Remove timestamps outside the window
            while dq and dq[0] <= now - window_seconds:
                dq.popleft()
            # Check if limit exceeded
            if len(dq) >= limit:
                retry_after = dq[0] + window_seconds - now
                return False, max(retry_after, 0.0)
            # Record this request
            dq.append(now)
            return True, 0.0

    def prune(self, older_than_seconds: int = 3600) -> None:
        """Remove stale entries from the rate limiter state.

        Cleans up buckets that have expired entries or are empty,
        helping to prevent unbounded memory growth over time.

        Args:
            older_than_seconds: Remove entries older than this many seconds
        """
        now = time.time()
        with self._lock:
            # Remove empty buckets
            for key in [k for k, dq in self._hits.items() if not dq]:
                del self._hits[key]
            # Remove stale timestamps from non-empty buckets
            for key, dq in self._hits.items():
                while dq and dq[0] <= now - older_than_seconds:
                    dq.popleft()
