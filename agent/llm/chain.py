"""LLM chain: primary (LMStudio) -> fallback (OpenRouter, optional).

Same surface as LMStudioClient for the calls the chat loop uses:
  - chat(messages, tools=...)
  - embed(texts)  (primary only — OpenRouter doesn't host the embed model)

A tiny circuit breaker trips the primary after N consecutive failures and
keeps it tripped for `cooldown` seconds. Each chat() reports which
provider served it via `_response_provider(resp)` for logging/telemetry.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from ..config import settings
from .lmstudio import LMStudioClient, LMStudioError
from .openrouter import OpenRouterClient, OpenRouterError

log = logging.getLogger(__name__)


class ChainError(RuntimeError):
    pass


_RETRIABLE = (
    LMStudioError,
    httpx.HTTPError,
    TimeoutError,
)


class ChatLLM:
    """Owns one primary client + optional fallback. Single-instance per turn."""

    def __init__(self) -> None:
        self.primary = LMStudioClient()
        self.fallback = OpenRouterClient() if settings.fallback_enabled else None
        self._fail_count = 0
        self._tripped_until = 0.0
        # Set on each chat() call so callers can attribute which side served.
        self.last_provider: str | None = None

    @property
    def primary_tripped(self) -> bool:
        return time.time() < self._tripped_until

    async def __aenter__(self) -> ChatLLM:
        await self.primary.__aenter__()
        if self.fallback:
            await self.fallback.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.primary.__aexit__(*exc)
        if self.fallback:
            await self.fallback.__aexit__(*exc)

    def _record_failure(self) -> None:
        self._fail_count += 1
        if self._fail_count >= settings.llm_primary_failure_threshold:
            self._tripped_until = time.time() + settings.llm_primary_cooldown_seconds
            log.warning(
                "primary LLM circuit tripped for %ds after %d failures",
                settings.llm_primary_cooldown_seconds, self._fail_count,
            )

    def _record_success(self) -> None:
        self._fail_count = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        kwargs = {
            "tools": tools, "tool_choice": tool_choice,
            "temperature": temperature, "max_tokens": max_tokens,
        }

        if not self.primary_tripped:
            try:
                resp = await self.primary.chat(messages, **kwargs)
                self._record_success()
                self.last_provider = "lmstudio"
                return resp
            except _RETRIABLE as e:
                log.warning("primary chat failed: %s", e)
                self._record_failure()
                if self.fallback is None:
                    raise

        if self.fallback is None:
            raise ChainError(
                "primary LLM unavailable and no fallback configured"
            )

        try:
            resp = await self.fallback.chat(messages, **kwargs)
            self.last_provider = "openrouter"
            log.info("served via fallback (openrouter:%s)", settings.openrouter_model)
            return resp
        except OpenRouterError as e:
            raise ChainError(f"both primary and fallback failed; fallback: {e}") from e

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream chat completion deltas. Same fallback semantics as chat().

        Note on tool-calling + streaming: providers send tool_call arguments
        in delta fragments that the caller must concatenate. The caller is
        responsible for assembly; this method just forwards chunks.
        """
        kwargs = {
            "tools": tools, "tool_choice": tool_choice,
            "temperature": temperature, "max_tokens": max_tokens,
        }

        if not self.primary_tripped:
            try:
                async for chunk in self.primary.chat_stream(messages, **kwargs):
                    yield chunk
                self._record_success()
                self.last_provider = "lmstudio"
                return
            except _RETRIABLE as e:
                log.warning("primary chat_stream failed: %s", e)
                self._record_failure()
                if self.fallback is None:
                    raise

        if self.fallback is None:
            raise ChainError("primary LLM unavailable and no fallback configured")

        try:
            async for chunk in self.fallback.chat_stream(messages, **kwargs):
                yield chunk
            self.last_provider = "openrouter"
            log.info("streamed via fallback (openrouter:%s)", settings.openrouter_model)
        except OpenRouterError as e:
            raise ChainError(f"both primary and fallback failed; fallback: {e}") from e

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Embeddings only on the primary — OpenRouter doesn't host our
        # embedding model. If you need fallback embeddings, add a separate
        # provider here. Today the index is built once and queried often,
        # so this is rarely on the hot failure path.
        return await self.primary.embed(texts)
