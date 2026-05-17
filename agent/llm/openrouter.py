"""OpenAI-compatible client for OpenRouter (optional fallback).

OpenRouter exposes the OpenAI chat-completions surface verbatim, so the
shape returned by `chat()` matches what `chat_loop` already consumes from
LMStudioClient — no special-casing needed downstream.

Embeddings are NOT supported here. Only chat completions.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from ..config import settings


class OpenRouterError(RuntimeError):
    pass


class OpenRouterClient:
    def __init__(self) -> None:
        self.base_url = settings.openrouter_url.rstrip("/")
        self.api_key = settings.openrouter_api_key
        self.model = settings.openrouter_model
        self.timeout = settings.openrouter_timeout_seconds
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def __aenter__(self) -> OpenRouterClient:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # OpenRouter recommends sending these for attribution and
        # rate-limit allocation, but they are not strictly required.
        if settings.openrouter_referer:
            headers["HTTP-Referer"] = settings.openrouter_referer
        if settings.openrouter_title:
            headers["X-Title"] = settings.openrouter_title
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require(self) -> httpx.AsyncClient:
        if self._client is None:
            raise OpenRouterError("OpenRouterClient must be used as async context manager")
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.agent_temperature,
            "max_tokens": max_tokens or settings.agent_max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        r = await self._require().post("/chat/completions", json=payload)
        if r.status_code >= 400:
            raise OpenRouterError(
                f"OpenRouter chat error {r.status_code}: {r.text[:500]}"
            )
        return r.json()

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.agent_temperature,
            "max_tokens": max_tokens or settings.agent_max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        async with self._require().stream("POST", "/chat/completions", json=payload) as r:
            if r.status_code >= 400:
                body = await r.aread()
                raise OpenRouterError(f"OpenRouter stream {r.status_code}: {body[:500]!r}")
            async for raw_line in r.aiter_lines():
                if not raw_line or not raw_line.startswith("data: "):
                    continue
                data = raw_line[6:]
                if data == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue
