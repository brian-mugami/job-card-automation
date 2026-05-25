"""Lightweight in-process rate limiter.

Adequate for a single-process local deployment. For multi-worker / hosted
production, swap the backing store for Redis / memcached so all workers see
the same counters — the public API stays the same.
"""
from __future__ import annotations

import time
from collections import defaultdict

from fastapi import HTTPException, Request, status


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, *, max_hits: int, window_seconds: int) -> bool:
        """Returns True if the call is allowed, False if it should be blocked.

        Caller is responsible for surfacing the block (e.g. raising a 429).
        """
        now = time.time()
        history = self._hits[key]
        cutoff = now - window_seconds
        # In-place purge so very-stale entries don't grow the list forever.
        history[:] = [t for t in history if t > cutoff]
        if len(history) >= max_hits:
            return False
        history.append(now)
        return True


# Module-level singleton — one limiter per Python process.
rate_limiter = RateLimiter()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce(key: str, *, max_hits: int, window_seconds: int, detail: str) -> None:
    """Raise 429 if the call exceeds the configured budget."""
    if not rate_limiter.check(key, max_hits=max_hits, window_seconds=window_seconds):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail)
