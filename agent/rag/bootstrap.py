"""Cold-start helper: fetch the published docs index from a GitHub release.

The public docs_only deployment doesn't ship the index inside the Docker
image (would force a rebuild every reindex). Instead it downloads the
artifact from the `index-latest` release on first boot.

Configuration:
    AGENT_INDEX_BOOTSTRAP_URL  full URL to index.json.gz (e.g. the GitHub
                               release download URL). Empty disables.
    AGENT_INDEX_PATH           where to write it (already used by RagStore).

Behavior:
    - If the local index file already exists, do nothing (no download).
    - Otherwise GET the URL, stream to a tmp file, rename atomically.
    - Failures are logged but never fatal: the agent still boots; docs_search
      simply returns "not ready" until the next pass (or operator action).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import httpx

from ..config import settings

log = logging.getLogger(__name__)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def maybe_bootstrap_index() -> None:
    """If AGENT_INDEX_BOOTSTRAP_URL is set and the local index is missing,
    download it. Idempotent: safe to call on every boot.

    Security: the index is gzipped JSON, never pickle (#16), so a tampered
    file cannot execute code — the worst it can do is give wrong answers.
    The URL must still be HTTPS, and when AGENT_INDEX_BOOTSTRAP_SHA256 is
    configured the bytes are verified before the file is moved into place; a
    mismatch is left as <name>.bootstrap.reject for inspection, never loaded.
    """
    url = settings.agent_index_bootstrap_url.strip()
    if not url:
        return
    if not url.lower().startswith("https://"):
        log.error("AGENT_INDEX_BOOTSTRAP_URL must be https:// — refusing to fetch")
        return
    path = Path(settings.agent_index_path)
    if not path.is_absolute():
        path = (Path(__file__).resolve().parent.parent.parent / path).resolve()
    if path.exists():
        log.info("RAG index already present at %s; skipping bootstrap", path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".bootstrap")
    expected_sha = settings.agent_index_bootstrap_sha256.strip().lower()
    log.info("Bootstrapping RAG index from %s", url)
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
            async with c.stream("GET", url) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                        f.write(chunk)
        if expected_sha:
            got = _sha256_of(tmp)
            if got != expected_sha:
                rejected = tmp.with_suffix(tmp.suffix + ".reject")
                tmp.replace(rejected)
                log.error(
                    "RAG index bootstrap sha256 MISMATCH: expected=%s got=%s. "
                    "Refusing to install. File kept for inspection at %s.",
                    expected_sha, got, rejected,
                )
                return
            log.info("RAG index sha256 verified: %s", got)
        else:
            log.warning(
                "RAG index bootstrap fetched without a sha256 pin. Set "
                "AGENT_INDEX_BOOTSTRAP_SHA256 to verify integrity: without "
                "it, whoever can publish to that URL decides what this agent "
                "says about CertMate."
            )
        tmp.replace(path)
        log.info("RAG index downloaded: %s (%d bytes)", path, path.stat().st_size)
    except Exception as e:
        log.warning("RAG index bootstrap failed: %s — docs_search will be empty until rebuilt", e)
        # Best-effort cleanup of the partial file.
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
