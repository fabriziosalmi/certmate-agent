"""One-shot indexer: fetch CertMate docs from GitHub, chunk, embed, persist.

Usage:
    python -m agent.rag.indexer                          # default: README + docs/*.md
    python -m agent.rag.indexer --repo owner/repo --branch main

Writes `docs_index/index.pkl` next to the package by default.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import pickle
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from ..llm.lmstudio import LMStudioClient
from .chunker import Chunk, chunk_markdown
from .store import DEFAULT_INDEX_PATH as STORE_INDEX_PATH
from .store import Index, IndexedChunk

DEFAULT_REPO = "fabriziosalmi/certmate"
DEFAULT_BRANCH = "main"
DEFAULT_PATHS = [
    "README.md",
    "docs/README.md",
    "docs/index.md",
    "docs/guide.md",
    "docs/api.md",
    "docs/architecture.md",
    "docs/ca-providers.md",
    "docs/deploy-hooks.md",
    "docs/dns-providers.md",
    "docs/docker.md",
    "docs/installation.md",
    "docs/testing.md",
]
DEFAULT_INDEX_PATH = STORE_INDEX_PATH
EMBED_BATCH = 16


async def _fetch_markdown(
    client: httpx.AsyncClient, repo: str, branch: str, path: str
) -> str | None:
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}"
    r = await client.get(url, headers={"Accept": "application/vnd.github+json"})
    if r.status_code == 404:
        print(f"  skip (404): {path}")
        return None
    r.raise_for_status()
    body = r.json()
    if body.get("encoding") != "base64":
        raise RuntimeError(f"unexpected encoding for {path}: {body.get('encoding')}")
    return base64.b64decode(body["content"]).decode("utf-8", errors="replace")


def _raw_url(repo: str, branch: str, path: str) -> str:
    return f"https://github.com/{repo}/blob/{branch}/{path}"


class IndexerError(RuntimeError):
    pass


async def build_index_iter(
    *,
    repo: str,
    branch: str,
    paths: list[str],
    out_path: Path,
) -> AsyncGenerator[dict[str, Any], None]:
    """Build the index, yielding progress events.

    Event shapes:
        {"phase": "start", "repo": ..., "branch": ..., "files": int}
        {"phase": "fetch", "done": int, "total": int, "path": str, "chunks": int}
        {"phase": "fetch", "done": int, "total": int, "path": str, "skipped": "404"}
        {"phase": "embed", "done": int, "total": int}
        {"phase": "done", "chunks": int, "bytes": int, "elapsed_s": float}
    Errors are raised as IndexerError; the caller is responsible for surfacing them.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    yield {"phase": "start", "repo": repo, "branch": branch, "files": len(paths)}

    chunks: list[Chunk] = []
    chunk_urls: list[str] = []

    async with httpx.AsyncClient(timeout=30.0) as gh:
        # Fetch all files concurrently. GitHub API tolerates this comfortably
        # for a dozen requests; sequential fetch was a needless ~2s of wall
        # time on a cold reindex.
        results = await asyncio.gather(
            *[_fetch_markdown(gh, repo, branch, p) for p in paths],
            return_exceptions=True,
        )
        for i, (path, md) in enumerate(zip(paths, results), start=1):
            if isinstance(md, Exception):
                yield {"phase": "fetch", "done": i, "total": len(paths),
                       "path": path, "skipped": f"error: {md}"}
                continue
            if md is None:
                yield {"phase": "fetch", "done": i, "total": len(paths),
                       "path": path, "skipped": "404"}
                continue
            file_chunks = chunk_markdown(md, source=path)
            chunks.extend(file_chunks)
            chunk_urls.extend([_raw_url(repo, branch, path)] * len(file_chunks))
            yield {"phase": "fetch", "done": i, "total": len(paths),
                   "path": path, "chunks": len(file_chunks)}

    if not chunks:
        raise IndexerError("no chunks produced — check paths")

    yield {"phase": "embed", "done": 0, "total": len(chunks)}
    embeddings: list[list[float]] = []
    async with LMStudioClient() as llm:
        for i in range(0, len(chunks), EMBED_BATCH):
            batch = chunks[i : i + EMBED_BATCH]
            inputs = [_format_for_embedding(c) for c in batch]
            vectors = await llm.embed(inputs)
            embeddings.extend(vectors)
            yield {"phase": "embed", "done": i + len(batch), "total": len(chunks)}

    indexed = [
        IndexedChunk(text=c.text, title=c.title, source=c.source, url=u, embedding=v)
        for c, u, v in zip(chunks, chunk_urls, embeddings)
    ]
    idx = Index(
        repo=repo,
        branch=branch,
        built_at=time.time(),
        embed_model=settings.lmstudio_embed_model,
        chunks=indexed,
    )
    # Atomic swap: write to tmp, rename onto target.
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("wb") as f:
        pickle.dump(idx, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(out_path)

    yield {
        "phase": "done",
        "chunks": len(indexed),
        "bytes": out_path.stat().st_size,
        "elapsed_s": round(time.time() - t0, 2),
    }


async def build_index(
    *,
    repo: str,
    branch: str,
    paths: list[str],
    out_path: Path,
) -> None:
    """CLI wrapper that prints progress to stdout."""
    print(f"Indexing {repo}@{branch} into {out_path}")
    async for ev in build_index_iter(repo=repo, branch=branch, paths=paths, out_path=out_path):
        phase = ev["phase"]
        if phase == "fetch":
            if "skipped" in ev:
                print(f"  skip ({ev['skipped']}): {ev['path']}")
            else:
                print(f"  {ev['path']}: {ev['chunks']} chunks")
        elif phase == "embed" and ev["done"] > 0:
            print(f"  embedded {ev['done']}/{ev['total']}")
        elif phase == "done":
            kb = ev["bytes"] // 1024
            print(f"Wrote {ev['chunks']} chunks → {out_path} ({kb} KB, {ev['elapsed_s']}s)")


def _format_for_embedding(c: Chunk) -> str:
    """Prefix chunks with their heading path so the embedding captures topic."""
    if c.title:
        return f"# {c.source} > {c.title}\n\n{c.text}"
    return f"# {c.source}\n\n{c.text}"


def main() -> None:
    p = argparse.ArgumentParser(description="Build the CertMate-Agent docs index")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--branch", default=DEFAULT_BRANCH)
    p.add_argument(
        "--paths",
        default=None,
        help="Comma-separated paths to index (default: hardcoded README + docs/*.md)",
    )
    p.add_argument("--out", default=str(DEFAULT_INDEX_PATH), type=Path)
    args = p.parse_args()

    paths = args.paths.split(",") if args.paths else DEFAULT_PATHS
    asyncio.run(
        build_index(repo=args.repo, branch=args.branch, paths=paths, out_path=args.out)
    )


if __name__ == "__main__":
    main()
