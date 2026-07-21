"""Background scheduler.

Single asyncio task that wakes every `AGENT_CLEANUP_INTERVAL_SECONDS`
to prune:
  - conversation_messages older than `AGENT_CONVERSATION_TTL_DAYS`
    (only when persistence is enabled)

Started in main.py lifespan. Set the interval to 0 to disable.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from .config import settings
from .db import audit_prune_older_than, conversation_prune_older_than

log = logging.getLogger(__name__)


async def _cleanup_once() -> dict[str, int]:
    convs = 0
    if settings.agent_persist_conversations:
        convs = await asyncio.to_thread(
            conversation_prune_older_than, settings.agent_conversation_ttl_days
        )
    audit_rows = await asyncio.to_thread(
        audit_prune_older_than, settings.agent_audit_ttl_days
    )
    return {"conversations": convs, "audit": audit_rows}


async def _scheduler_loop(interval: int) -> None:
    log.info(
        "scheduler started: interval=%ds, conversation_ttl_days=%d, persistence=%s",
        interval,
        settings.agent_conversation_ttl_days,
        settings.agent_persist_conversations,
    )
    # Run one pass immediately on boot so first restart cleans backlog.
    try:
        stats = await _cleanup_once()
        if any(stats.values()):
            log.info("cleanup pass (boot): %s", stats)
    except Exception as e:  # pragma: no cover - keep loop alive on errors
        log.error("cleanup error on boot: %s", e)

    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            log.info("scheduler stopping")
            return
        try:
            stats = await _cleanup_once()
            if any(stats.values()):
                log.info("cleanup pass: %s", stats)
            else:
                log.debug("cleanup pass: nothing to prune")
        except asyncio.CancelledError:
            log.info("scheduler stopping mid-pass")
            return
        except Exception as e:  # pragma: no cover - defensive
            log.error("cleanup error: %s", e)


@asynccontextmanager
async def scheduler_lifespan():
    """Start the scheduler task; cancel cleanly on exit."""
    interval = settings.agent_cleanup_interval_seconds
    if interval <= 0:
        log.info("scheduler disabled (AGENT_CLEANUP_INTERVAL_SECONDS=0)")
        yield
        return
    task = asyncio.create_task(_scheduler_loop(interval), name="agent-cleanup")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def force_cleanup() -> dict[str, int]:
    """Synchronous-style API for tests and admin endpoints."""
    return await _cleanup_once()
