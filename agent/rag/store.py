"""Runtime store for the docs index.

Single global instance, loaded lazily on first access. If the index file
is missing the store is in 'empty' state and search() returns []; the
agent stays functional (no hard dependency on RAG).
"""

from __future__ import annotations

import logging
import math
import gzip
import json
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)


def _default_index_path() -> Path:
    p = Path(settings.agent_index_path)
    if not p.is_absolute():
        # Resolve relative to the repo root (parent of the `agent/` package).
        p = (Path(__file__).resolve().parent.parent.parent / p).resolve()
    return p


DEFAULT_INDEX_PATH = _default_index_path()


# The index is serialised as gzipped JSON, not pickle (#16). Every field is a
# string or a float, so nothing is lost — and `pickle.load()` on a file
# fetched over the network at boot is remote code execution by design, which
# no amount of care around the download can make safe. With JSON the worst a
# tampered index can do is give bad answers, which the sha256 pin catches.
@dataclass
class IndexedChunk:
    text: str
    title: str
    source: str
    url: str
    embedding: list[float]


@dataclass
class Index:
    repo: str
    branch: str
    built_at: float
    embed_model: str
    chunks: list[IndexedChunk]


def _read_index(path: Path) -> "Index":
    """Load a gzipped-JSON index. Refuses a legacy pickle outright.

    Loading the old format would mean unpickling a file that, in the hosted
    deployment, arrived over the network — so this raises instead, with the
    command to rebuild.
    """
    with path.open("rb") as f:
        magic = f.read(2)
    if magic == b"\x80\x04" or magic == b"\x80\x05":
        raise ValueError(
            f"{path} is a legacy pickle index. Rebuild it with "
            "`python -m agent.rag.indexer` — pickle indexes are no longer "
            "loaded (#16)."
        )
    opener = gzip.open if magic == b"\x1f\x8b" else open
    with opener(path, "rt", encoding="utf-8") as f:  # type: ignore[operator]
        raw = json.load(f)
    return Index(
        repo=raw["repo"],
        branch=raw["branch"],
        built_at=raw["built_at"],
        embed_model=raw["embed_model"],
        chunks=[IndexedChunk(**c) for c in raw["chunks"]],
    )


@dataclass
class SearchHit:
    text: str
    title: str
    source: str
    url: str
    score: float


def _norm(v: list[float]) -> float:
    s = 0.0
    for x in v:
        s += x * x
    return math.sqrt(s)


def _dot(a: list[float], b: list[float]) -> float:
    s = 0.0
    for x, y in zip(a, b):
        s += x * y
    return s


# Minimum cosine similarity we'll surface as a hit. Anything below this is
# noise (random query against unrelated corpus) — better to return nothing
# and let the assistant say "no relevant docs" than to cite irrelevance.
_MIN_SCORE = 0.15


class RagStore:
    def __init__(self, path: Path = DEFAULT_INDEX_PATH) -> None:
        self.path = path
        self._index: Index | None = None
        self._loaded_at: float | None = None
        # Pre-normalized chunk vectors. With unit-length vectors cosine
        # similarity collapses to a single dot product per chunk, dropping
        # the per-query sqrt and one half of the FLOPs.
        self._unit_chunks: list[list[float]] = []

    @property
    def ready(self) -> bool:
        return self._index is not None

    @property
    def size(self) -> int:
        return len(self._index.chunks) if self._index else 0

    def info(self) -> dict:
        if not self._index:
            return {"ready": False, "path": str(self.path)}
        return {
            "ready": True,
            "path": str(self.path),
            "repo": self._index.repo,
            "branch": self._index.branch,
            "built_at": self._index.built_at,
            "embed_model": self._index.embed_model,
            "chunks": len(self._index.chunks),
            "loaded_at": self._loaded_at,
        }

    def load(self) -> bool:
        if not self.path.exists():
            log.warning("RAG index not found at %s; docs_search will be empty", self.path)
            return False
        try:
            self._index = _read_index(self.path)
            self._loaded_at = time.time()
            self._unit_chunks = self._normalize_chunks(self._index.chunks)
            log.info("RAG index loaded: %d chunks (%s)",
                     len(self._index.chunks), self._index.embed_model)
            self._warn_if_model_mismatch()
            return True
        except Exception as e:  # pragma: no cover - defensive
            log.error("Failed to load RAG index from %s: %s", self.path, e)
            self._index = None
            self._unit_chunks = []
            return False

    @staticmethod
    def _normalize_chunks(chunks: list[IndexedChunk]) -> list[list[float]]:
        out: list[list[float]] = []
        for c in chunks:
            n = _norm(c.embedding)
            if n == 0.0:
                out.append(c.embedding)
            else:
                out.append([x / n for x in c.embedding])
        return out

    def _warn_if_model_mismatch(self) -> None:
        """If the runtime embed model differs from the index's, cosine
        similarity over the loaded vectors is meaningless. Surface loudly."""
        if not self._index:
            return
        runtime_model = settings.lmstudio_embed_model
        if self._index.embed_model and runtime_model and self._index.embed_model != runtime_model:
            log.error(
                "RAG embed model MISMATCH: index built with '%s' but runtime "
                "configured with '%s'. Cosine scores will be wrong. Either "
                "rebuild the index with %s or change LMSTUDIO_EMBED_MODEL to %s.",
                self._index.embed_model, runtime_model,
                runtime_model, self._index.embed_model,
            )

    def reload(self) -> bool:
        """Hot-reload from disk. Called after `/reindex` writes a new file."""
        return self.load()

    def search(self, query_embedding: list[float], k: int = 3) -> list[SearchHit]:
        if not self._index or not query_embedding or not self._unit_chunks:
            return []
        # Take a local snapshot so a concurrent reload() can't swap the
        # vector list out from under the loop mid-search.
        chunks = self._index.chunks
        unit_chunks = self._unit_chunks
        qn = _norm(query_embedding)
        if qn == 0.0:
            return []
        unit_q = [x / qn for x in query_embedding]
        scored: list[tuple[float, IndexedChunk]] = [
            (_dot(unit_q, uc), c) for uc, c in zip(unit_chunks, chunks)
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[SearchHit] = []
        for score, c in scored[:k]:
            if score < _MIN_SCORE:
                break  # sorted; everything after this is also under the floor
            out.append(SearchHit(
                text=c.text, title=c.title, source=c.source, url=c.url, score=score,
            ))
        return out


_store: RagStore | None = None


def get_store() -> RagStore:
    global _store
    if _store is None:
        _store = RagStore()
        _store.load()
    return _store
