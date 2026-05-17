"""Heading-aware markdown chunker.

Splits a markdown document on ## / ### / #### boundaries, then packs
short sibling sections together and breaks long sections by paragraph
to stay near `target_chars` per chunk.

Each chunk carries a path-style 'title' built from the heading lineage
(e.g. 'Installation > Docker > Compose') so the LLM can cite it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class Chunk:
    text: str
    title: str
    source: str  # e.g. "README.md", "docs/dns-providers.md"


def _split_sections(text: str) -> list[tuple[int, str, str]]:
    """Return [(level, title, body)] in document order.
    The pre-heading preamble (if any) is returned as (0, "", body).
    """
    matches = list(HEADING_RE.finditer(text))
    sections: list[tuple[int, str, str]] = []
    if not matches:
        return [(0, "", text.strip())]
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append((0, "", preamble))
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        sections.append((level, title, body))
    return sections


def _heading_path(stack: list[str]) -> str:
    return " > ".join(s for s in stack if s)


def _pack_paragraphs(body: str, max_chars: int) -> list[str]:
    """Split a long section into smaller pieces along paragraph boundaries."""
    paras = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
    out: list[str] = []
    buf: list[str] = []
    cur = 0
    for p in paras:
        if cur + len(p) + 2 > max_chars and buf:
            out.append("\n\n".join(buf))
            buf, cur = [], 0
        buf.append(p)
        cur += len(p) + 2
    if buf:
        out.append("\n\n".join(buf))
    return out or [body]


def chunk_markdown(
    text: str,
    source: str,
    *,
    target_chars: int = 900,
    min_chars: int = 200,
) -> list[Chunk]:
    """Heading-aware chunker. Returns chunks in document order."""
    sections = _split_sections(text)
    chunks: list[Chunk] = []
    stack: list[str] = [""] * 7  # index by heading level

    pending_body: list[str] = []
    pending_title: str = ""

    def flush() -> None:
        if not pending_body:
            return
        joined = "\n\n".join(pending_body).strip()
        if not joined:
            pending_body.clear()
            return
        if len(joined) <= target_chars * 1.4:
            chunks.append(Chunk(text=joined, title=pending_title, source=source))
        else:
            for piece in _pack_paragraphs(joined, target_chars):
                chunks.append(Chunk(text=piece, title=pending_title, source=source))
        pending_body.clear()

    for level, title, body in sections:
        if level > 0:
            stack[level] = title
            for k in range(level + 1, len(stack)):
                stack[k] = ""
            path = _heading_path(stack[1 : level + 1])
        else:
            path = ""

        # If switching to a new section title, flush previous accumulation.
        if path != pending_title:
            flush()
            pending_title = path

        if body:
            pending_body.append(body)

        # Flush eagerly if we exceed target after appending.
        accumulated = sum(len(p) for p in pending_body) + 2 * len(pending_body)
        if accumulated >= target_chars:
            flush()

    flush()

    # Drop tiny chunks (typically just stray short subheadings).
    return [c for c in chunks if len(c.text) >= min_chars]
