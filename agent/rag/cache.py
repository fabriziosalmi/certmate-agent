"""In-memory TTL+LRU cache for docs_search results.

Key: (normalized_query, k). Value: list of hit dicts + insertion time.
Eviction: TTL first, then LRU when size exceeds the cap.

The cache lives in-process; for multi-worker deployments each worker
maintains its own. Index reloads (`/reindex`) invalidate by bumping the
generation counter — cached hits from an older index version are dropped
on access.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from threading import Lock
from typing import Any

from ..config import settings

_WS_RE = re.compile(r"\s+")


def _normalize_query(query: str) -> str:
    return _WS_RE.sub(" ", query.strip().lower())


class DocsCache:
    def __init__(self) -> None:
        self._data: OrderedDict[tuple[str, int], tuple[float, int, list[dict[str, Any]]]] = OrderedDict()
        self._lock = Lock()
        self._generation = 0
        self.hits = 0
        self.misses = 0

    def bump_generation(self) -> None:
        """Invalidate everything (called after /reindex)."""
        with self._lock:
            self._generation += 1
            self._data.clear()

    def get(self, query: str, k: int) -> list[dict[str, Any]] | None:
        if settings.agent_docs_cache_ttl_seconds <= 0 or settings.agent_docs_cache_size <= 0:
            return None
        key = (_normalize_query(query), k)
        now = time.time()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            inserted_at, gen, hits = entry
            if gen != self._generation or now - inserted_at > settings.agent_docs_cache_ttl_seconds:
                # stale; drop
                self._data.pop(key, None)
                self.misses += 1
                return None
            self._data.move_to_end(key)  # LRU touch
            self.hits += 1
            return hits

    def put(self, query: str, k: int, hits: list[dict[str, Any]]) -> None:
        if settings.agent_docs_cache_ttl_seconds <= 0 or settings.agent_docs_cache_size <= 0:
            return
        key = (_normalize_query(query), k)
        with self._lock:
            self._data[key] = (time.time(), self._generation, hits)
            self._data.move_to_end(key)
            while len(self._data) > settings.agent_docs_cache_size:
                self._data.popitem(last=False)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "size": len(self._data),
                "capacity": settings.agent_docs_cache_size,
                "ttl_s": settings.agent_docs_cache_ttl_seconds,
                "generation": self._generation,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": (
                    self.hits / (self.hits + self.misses)
                    if (self.hits + self.misses) > 0
                    else 0.0
                ),
            }


_cache: DocsCache | None = None


def get_cache() -> DocsCache:
    global _cache
    if _cache is None:
        _cache = DocsCache()
    return _cache
