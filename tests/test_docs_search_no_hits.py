"""An empty `docs_search` result must tell the model what to do about it."""

import asyncio

from agent.tools import registry
from agent.tools.registry import _NO_HITS_NOTE, _docs_result


def test_empty_result_carries_the_instruction():
    out = _docs_result([], cached=False)
    assert out["hits"] == [] and out["note"] == _NO_HITS_NOTE


def test_result_with_hits_has_no_note():
    out = _docs_result([{"source": "docs/a.md"}], cached=True)
    assert "note" not in out and out["cached"] is True


def test_docs_search_returns_the_note_when_nothing_matches(monkeypatch):
    class _Store:
        ready = True

        def search(self, vec, k=3):
            return []

    class _Llm:
        async def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    async def _client():
        return _Llm()

    monkeypatch.setattr(registry, "get_store", lambda: _Store())
    monkeypatch.setattr("agent.llm.shared.get_embed_client", _client)
    from agent.rag.cache import get_cache
    get_cache().clear() if hasattr(get_cache(), "clear") else None
    out = asyncio.run(registry._docs_search({"query": "something nobody documented"}))
    assert out["hits"] == [] and out["note"] == _NO_HITS_NOTE
