"""Per-user sliding-window rate limiter. Callers wait politely for a free slot instead of hammering."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class RateLimited(Exception):
    pass


class PerUserRateLimiter:
    def __init__(self, per_minute: int, max_wait_s: float = 30.0):
        self.per_minute = per_minute
        self.max_wait_s = max_wait_s
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, user_id: str) -> None:
        async with self._locks[user_id]:
            q = self._calls[user_id]
            now = time.monotonic()
            while q and now - q[0] >= 60:
                q.popleft()
            if len(q) >= self.per_minute:
                wait = 60 - (now - q[0])
                if wait > self.max_wait_s:
                    raise RateLimited(f"rate limit: {self.per_minute}/min for user {user_id}; retry in {wait:.0f}s")
                await asyncio.sleep(wait)
                q.popleft()
            q.append(time.monotonic())
