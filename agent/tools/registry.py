"""Tool registry exposed to the LLM.

One tool: `docs_search`, retrieval over the CertMate documentation. It runs
entirely locally against the RAG index and touches no external API.

This file used to register 23 tools mapped onto CertMate's REST API, with a
confirm-token flow for the write ones. That surface is gone (#18): CertMate
ships its own MCP server, maintained in the repository where the API actually
changes, so a second mapping here could only drift — and it had, in six
places. Its removal also removes what the mapping made possible: a
model-controlled path reaching the certificate download endpoint (#12), an
unauthenticated execute endpoint (#13), and an "operator token"
recommendation that could never work (#15).

Every tool is READ and executes inline; there is no write path and no
confirmation flow, because there is nothing left to confirm.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..rag import get_store


class ToolKind(str, Enum):
    READ = "read"


Executor = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    kind: ToolKind
    executor: Executor
    aliases: list[str] = field(default_factory=list)

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


async def _docs_search(args: dict[str, Any]) -> Any:
    """RAG over CertMate docs.

    Cached by normalized query + k; cache invalidated on /reindex.
    """
    from ..llm.shared import get_embed_client
    from ..rag.cache import get_cache

    query = (args.get("query") or "").strip()
    k = max(1, min(int(args.get("k", 3)), 8))
    if not query:
        return {"error": "query is required"}

    store = get_store()
    if not store.ready:
        return {
            "ready": False,
            "hits": [],
            "note": "Docs index not built. Run: python -m agent.rag.indexer",
        }

    cache = get_cache()
    cached = cache.get(query, k)
    if cached is not None:
        return {"ready": True, "hits": cached, "cached": True}

    # Shared embed client (process singleton). Reuses the httpx connection
    # pool across queries — saves the TLS handshake on every docs_search call.
    llm = await get_embed_client()
    vectors = await llm.embed([query])
    hits = store.search(vectors[0], k=k)
    payload = [
        {
            "title": h.title,
            "source": h.source,
            "url": h.url,
            "score": round(h.score, 3),
            "text": h.text,
        }
        for h in hits
    ]
    cache.put(query, k, payload)
    return {"ready": True, "hits": payload, "cached": False}


def _build_registry() -> dict[str, Tool]:
    tools = [
        Tool(
            name="docs_search",
            description=(
                "Retrieve relevant excerpts from the CertMate documentation. "
                "USE THIS for any question about how CertMate works, what a "
                "feature does, ACME / DNS-01 / wildcard concepts, DNS provider "
                "setup, deploy hooks, backup format or API parameters. This "
                "agent has no connection to a running CertMate instance and "
                "cannot see live state."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language question. Be specific.",
                    },
                    "k": {
                        "type": "integer",
                        "minimum": 1, "maximum": 8, "default": 3,
                        "description": "Number of excerpts to return.",
                    },
                },
                "required": ["query"],
            },
            kind=ToolKind.READ,
            executor=_docs_search,
        ),
    ]
    return {t.name: t for t in tools}


REGISTRY: dict[str, Tool] = _build_registry()


def get_tool(name: str) -> Tool | None:
    return REGISTRY.get(name)


def openai_tool_schemas() -> list[dict[str, Any]]:
    return [t.to_openai_schema() for t in REGISTRY.values()]
