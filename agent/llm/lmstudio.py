"""OpenAI-compatible client for LM Studio.

Two surfaces:
  - chat_stream(messages, tools): async generator over deltas
  - embed(texts): batch embeddings

Models supported via env: chat (gemma-4-e2b by default), embedding (embeddinggemma-300m).
Note: small Gemma variants are "thinking" models; allow generous max_tokens
or switch to a Qwen instruct model for reliable tool use.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Iterable
from typing import Any

import httpx

from ..config import settings


class LMStudioError(RuntimeError):
    pass


class LMStudioClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        chat_model: str | None = None,
        embed_model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.lmstudio_url).rstrip("/")
        self.api_key = api_key or settings.lmstudio_api_key
        self.chat_model = chat_model or settings.lmstudio_chat_model
        self.embed_model = embed_model or settings.lmstudio_embed_model
        self.timeout = timeout or settings.lmstudio_timeout_seconds
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> LMStudioClient:
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise LMStudioError("LMStudioClient must be used as async context manager")
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
        """Non-streaming completion. Returns the full OpenAI-style response."""
        payload: dict[str, Any] = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.agent_temperature,
            "max_tokens": max_tokens or settings.agent_max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        r = await self._require_client().post("/chat/completions", json=payload)
        if r.status_code >= 400:
            raise LMStudioError(f"LMStudio chat error {r.status_code}: {r.text[:500]}")
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
        """Stream chat completion deltas. Yields parsed JSON chunks from SSE."""
        payload: dict[str, Any] = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.agent_temperature,
            "max_tokens": max_tokens or settings.agent_max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        async with self._require_client().stream(
            "POST", "/chat/completions", json=payload
        ) as r:
            if r.status_code >= 400:
                body = await r.aread()
                raise LMStudioError(f"LMStudio stream error {r.status_code}: {body[:500]!r}")
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

    async def embed(self, texts: Iterable[str]) -> list[list[float]]:
        """Batch embeddings. Returns one vector per input text, in order."""
        input_list = list(texts)
        if not input_list:
            return []
        payload = {"model": self.embed_model, "input": input_list}
        r = await self._require_client().post("/embeddings", json=payload)
        if r.status_code >= 400:
            raise LMStudioError(f"LMStudio embed error {r.status_code}: {r.text[:500]}")
        body = r.json()
        return [item["embedding"] for item in body["data"]]
