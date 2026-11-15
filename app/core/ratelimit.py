"""A small in-memory rate limiter (sliding window).

It protects sign-in, sign-up, the public form and the chat widget from abuse
and from running up AI costs. State lives in this process, which is correct
for the single-worker setup this app ships with. Behind several workers, move
the counters to Redis or Postgres.
"""

import time
from collections import deque

from app.core.errors import TooManyRequests


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._calls = 0

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        self._calls += 1
        if self._calls % 2000 == 0:
            self._prune(now)
        return True

    def _prune(self, now: float) -> None:
        # Keys idle for an hour are dropped so memory does not grow forever.
        stale = [k for k, hits in self._hits.items() if not hits or hits[-1] < now - 3600]
        for key in stale:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()


limiter = RateLimiter()


def enforce(key: str, limit: int, window_seconds: float, message: str | None = None) -> None:
    if not limiter.allow(key, limit, window_seconds):
        raise TooManyRequests(message or "Too many attempts. Please wait a little and try again.")
