"""`rank()` is the unfiltered top-k; `search()` is `rank()` above the score floor.

The retrieval evaluation (tests/eval) needs the raw scores of queries the floor
would discard, otherwise it cannot say where a floor should sit.
"""

import gzip
import json

import pytest

from agent.rag.store import _MIN_SCORE, RagStore

pytestmark = [pytest.mark.unit] if hasattr(pytest.mark, "unit") else []


def _store(tmp_path) -> RagStore:
    chunks = [
        {"text": "a", "title": "A", "source": "docs/a.md", "url": "u/a", "embedding": [1.0, 0.0]},
        {"text": "b", "title": "B", "source": "docs/b.md", "url": "u/b", "embedding": [0.1, 1.0]},
    ]
    path = tmp_path / "index.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"repo": "r", "branch": "main", "built_at": 0.0,
                   "embed_model": "m", "chunks": chunks}, f)
    store = RagStore(path)
    assert store.load()
    return store


def test_rank_orders_by_cosine_and_applies_no_floor(tmp_path):
    store = _store(tmp_path)
    hits = store.rank([0.0, -1.0], k=2)
    assert [h.source for h in hits] == ["docs/a.md", "docs/b.md"]
    assert hits[0].score < _MIN_SCORE  # below the floor, still returned


def test_search_drops_what_is_below_the_floor(tmp_path):
    store = _store(tmp_path)
    assert store.search([0.0, -1.0], k=2) == []


def test_search_keeps_hits_above_the_floor_in_rank_order(tmp_path):
    store = _store(tmp_path)
    ranked = store.rank([1.0, 0.0], k=2)
    kept = store.search([1.0, 0.0], k=2)
    assert kept == [h for h in ranked if h.score >= _MIN_SCORE]
    assert kept[0].source == "docs/a.md"
