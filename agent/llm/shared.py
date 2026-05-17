"""Process-singleton LMStudioClient for embeddings.

The chat loop owns a short-lived ChatLLM per turn (correct: each turn
binds to one LMStudio + optional fallback). Embeddings are different —
they run on the primary only, are called from `docs_search` for every
query, and benefit from a reusable httpx connection pool. Opening a
fresh client per call wastes the TLS handshake + DNS roundtrip every
time.

This module exposes a lazy singleton that the FastAPI lifespan opens at
boot and closes at shutdown.
"""

from __future__ import annotations

import asyncio
import logging

from .lmstudio import LMStudioClient

log = logging.getLogger(__name__)

_embed: LMStudioClient | None = None
_lock = asyncio.Lock()


async def get_embed_client() -> LMStudioClient:
    """Return the lazily-initialized embed client. Safe under concurrent
    callers — first one wins, others receive the same instance."""
    global _embed
    if _embed is not None:
        return _embed
    async with _lock:
        if _embed is None:
            client = LMStudioClient()
            await client.__aenter__()
            _embed = client
            log.info("shared embed client opened (%s, model=%s)",
                     client.base_url, client.embed_model)
    return _embed


async def close_embed_client() -> None:
    """Close the singleton if it was opened. Called from the FastAPI
    lifespan shutdown so the connection pool drains cleanly."""
    global _embed
    if _embed is not None:
        try:
            await _embed.__aexit__(None, None, None)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("error closing shared embed client: %s", e)
        finally:
            _embed = None
