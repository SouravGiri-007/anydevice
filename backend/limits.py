"""Small in-memory sliding-window rate limiter (per IP).

Codes are the only gate on a share, so the PRD calls for rate limiting code
lookups to make brute-forcing the code space impractical. In-memory is fine
for a single-process MVP deployment; the API surface here is deliberately tiny
so a Redis-backed limiter could replace it later without touching routes.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds)."""
        if limit <= 0:
            return True, 0.0
        now = time.time()
        with self._lock:
            dq = self._hits[key]
            while dq and dq[0] <= now - window_seconds:
                dq.popleft()
            if len(dq) >= limit:
                retry_after = dq[0] + window_seconds - now
                return False, max(retry_after, 0.0)
            dq.append(now)
            return True, 0.0

    def prune(self, older_than_seconds: int = 3600) -> None:
        now = time.time()
        with self._lock:
            for key in [k for k, dq in self._hits.items() if not dq]:
                del self._hits[key]
            for key, dq in self._hits.items():
                while dq and dq[0] <= now - older_than_seconds:
                    dq.popleft()
