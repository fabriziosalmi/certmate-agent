"""In-memory per-IP rate limiter for /chat and /tools/execute.

Simple token-bucket: each remote IP gets `capacity` tokens, refilled at
`capacity/period` tokens/sec. When the bucket is empty the request is
denied with 429 + Retry-After.

In-process only; for multi-worker deployments each worker maintains its
own state. That's acceptable for our scale; for stricter guarantees use
a reverse-proxy rate limit (Caddy/Traefik/Cloudflare) in addition.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class RateLimiter:
    """Token bucket. capacity = max burst; period seconds to refill from 0."""

    def __init__(self, capacity: int, period_seconds: float) -> None:
        self.capacity = max(0, int(capacity))
        self.period = max(0.001, float(period_seconds))
        self._rate = self.capacity / self.period if self.capacity else 0.0
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds). retry_after_seconds is
        0.0 when allowed; otherwise the seconds until one token regenerates.
        """
        if self.capacity == 0:
            return True, 0.0
        async with self._lock:
            now = time.monotonic()
            b = self._buckets.get(key)
            if b is None:
                b = _Bucket(tokens=self.capacity - 1.0, last_refill=now)
                self._buckets[key] = b
                return True, 0.0
            elapsed = now - b.last_refill
            b.tokens = min(self.capacity, b.tokens + elapsed * self._rate)
            b.last_refill = now
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                return True, 0.0
            # need one full token: how long until we have one?
            retry_in = (1.0 - b.tokens) / self._rate
            return False, retry_in

    async def gc(self, idle_seconds: float = 300.0) -> int:
        """Drop buckets idle for more than `idle_seconds`. Returns count."""
        async with self._lock:
            now = time.monotonic()
            dead = [k for k, b in self._buckets.items()
                    if now - b.last_refill > idle_seconds]
            for k in dead:
                del self._buckets[k]
            return len(dead)


class ConcurrencyLimiter:
    """Cap concurrent in-flight handlers per key (used for SSE streams)."""

    def __init__(self, limit: int) -> None:
        self.limit = max(0, int(limit))
        self._counts: defaultdict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def acquire(self, key: str) -> bool:
        if self.limit == 0:
            return True
        async with self._lock:
            if self._counts[key] >= self.limit:
                return False
            self._counts[key] += 1
            return True

    async def release(self, key: str) -> None:
        if self.limit == 0:
            return
        async with self._lock:
            if self._counts[key] > 0:
                self._counts[key] -= 1
            if self._counts[key] == 0:
                self._counts.pop(key, None)


def client_key(request: Request) -> str:
    """Identify the caller. Trust X-Forwarded-For only if a single hop;
    a hostile proxy could spoof, so this is for fair-use, not security."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # take the leftmost (original client) but only the first hop
        return xff.split(",", 1)[0].strip()
    client = request.client
    return client.host if client else "unknown"


async def enforce(limiter: RateLimiter, request: Request, scope: str) -> None:
    """Raise 429 if the request exceeds `limiter`."""
    allowed, retry_in = await limiter.check(client_key(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"rate limit exceeded for {scope}; retry in ~{retry_in:.1f}s",
            headers={"Retry-After": str(max(1, int(retry_in + 0.5)))},
        )
