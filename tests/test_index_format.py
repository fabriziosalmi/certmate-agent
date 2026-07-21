"""The docs index is data, not code.

Regression tests for #16. The index was a pickle, fetched over HTTPS from a
GitHub release at cold start on the hosted deployment, with the sha256 pin
left unset in fly.toml — so `pickle.load()` ran on bytes that anyone able to
publish to that release could replace. The code's own docstring described the
risk and shipped the unpinned configuration anyway.

The fix is not a better download. Every field of the index is a string or a
float, so it serialises as JSON, and a tampered JSON index can at worst give
wrong answers — a completely different class of problem from arbitrary code
execution inside the container.
"""

import gzip
import json
import pickle

import pytest

from agent.rag.store import Index, IndexedChunk, RagStore, _read_index


pytestmark = [pytest.mark.unit] if hasattr(pytest.mark, "unit") else []


def _sample_index() -> Index:
    return Index(
        repo="fabriziosalmi/certmate",
        branch="main",
        built_at=1_753_000_000.0,
        embed_model="text-embedding-test",
        chunks=[
            IndexedChunk(
                text="CertMate renews certificates 30 days before expiry.",
                title="Renewal",
                source="docs/renewal.md",
                url="https://example.invalid/renewal",
                embedding=[0.1, -0.2, 0.3],
            )
        ],
    )


def _write_json_gz(path, index: Index) -> None:
    payload = {
        "repo": index.repo,
        "branch": index.branch,
        "built_at": index.built_at,
        "embed_model": index.embed_model,
        "chunks": [c.__dict__ for c in index.chunks],
    }
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f)


def test_a_gzipped_json_index_round_trips(tmp_path):
    path = tmp_path / "index.json.gz"
    _write_json_gz(path, _sample_index())

    loaded = _read_index(path)

    assert loaded.repo == "fabriziosalmi/certmate"
    assert loaded.embed_model == "text-embedding-test"
    assert len(loaded.chunks) == 1
    assert loaded.chunks[0].source == "docs/renewal.md"
    assert loaded.chunks[0].embedding == [0.1, -0.2, 0.3]


def test_plain_json_is_accepted_too(tmp_path):
    """Gzip is a size choice, not a format requirement."""
    path = tmp_path / "index.json"
    payload = {
        "repo": "r", "branch": "b", "built_at": 1.0, "embed_model": "m",
        "chunks": [{
            "text": "t", "title": "ti", "source": "s", "url": "u",
            "embedding": [1.0],
        }],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert _read_index(path).repo == "r"


def test_a_pickle_index_is_refused_not_executed(tmp_path):
    """The whole point: an old (or planted) pickle must never be unpickled."""
    path = tmp_path / "index.json.gz"
    with path.open("wb") as f:
        pickle.dump(_sample_index(), f, protocol=pickle.HIGHEST_PROTOCOL)

    with pytest.raises(ValueError, match="legacy pickle"):
        _read_index(path)


def test_the_store_reports_a_pickle_as_unloadable_rather_than_crashing(tmp_path):
    path = tmp_path / "index.json.gz"
    with path.open("wb") as f:
        pickle.dump(_sample_index(), f, protocol=pickle.HIGHEST_PROTOCOL)

    store = RagStore(path)

    assert store.load() is False
    assert store.ready is False


def test_the_store_loads_a_json_index(tmp_path):
    path = tmp_path / "index.json.gz"
    _write_json_gz(path, _sample_index())

    store = RagStore(path)

    assert store.load() is True
    assert store.ready is True
    assert store.info()["chunks"] == 1


def test_a_class_that_executes_on_unpickle_never_gets_the_chance(tmp_path):
    """Concretely: the payload shape an attacker would publish."""
    marker = tmp_path / "pwned"

    class Exploit:
        def __reduce__(self):
            return (open, (str(marker), "w"))

    path = tmp_path / "index.json.gz"
    with path.open("wb") as f:
        pickle.dump(Exploit(), f)

    store = RagStore(path)
    assert store.load() is False
    assert not marker.exists(), "the payload executed — the index is still a pickle"
