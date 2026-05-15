"""Cold-start helper: fetch the published docs index from a GitHub release.

The public docs_only deployment doesn't ship the index inside the Docker
image (would force a rebuild every reindex). Instead it downloads the
artifact from the `index-latest` release on first boot.

Configuration:
    AGENT_INDEX_BOOTSTRAP_URL  full URL to index.pkl (e.g. the GitHub
                               release download URL). Empty disables.
    AGENT_INDEX_PATH           where to write it (already used by RagStore).

Behavior:
    - If the local index file already exists, do nothing (no download).
    - Otherwise GET the URL, stream to a tmp file, rename atomically.
    - Failures are logged but never fatal: the agent still boots; docs_search
      simply returns "not ready" until the next pass (or operator action).
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from ..config import settings

log = logging.getLogger(__name__)


async def maybe_bootstrap_index() -> None:
    """If AGENT_INDEX_BOOTSTRAP_URL is set and the local index is missing,
    download it. Idempotent: safe to call on every boot.
    """
    url = settings.agent_index_bootstrap_url.strip()
    if not url:
        return
    path = Path(settings.agent_index_path)
    if not path.is_absolute():
        path = (Path(__file__).resolve().parent.parent.parent / path).resolve()
    if path.exists():
        log.info("RAG index already present at %s; skipping bootstrap", path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".bootstrap")
    log.info("Bootstrapping RAG index from %s", url)
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
            async with c.stream("GET", url) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                        f.write(chunk)
        tmp.replace(path)
        log.info("RAG index downloaded: %s (%d bytes)", path, path.stat().st_size)
    except Exception as e:
        log.warning("RAG index bootstrap failed: %s — docs_search will be empty until rebuilt", e)
        # Best-effort cleanup of the partial file.
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
