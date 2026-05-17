"""Rendering / formatting tests.

The slash router emits markdown that the widget renders; if the
backend produces a malformed fence or cell value, the widget's
minimal markdown parser may corrupt the surrounding text. These
tests pin the contracts both sides depend on.
"""

from __future__ import annotations

from agent.slash import _cell, _json_codeblock, _md_table, _truncate_excerpt


def test_cell_collapses_missing_to_emdash():
    """None / '' / '?' all read as 'absent' in tables."""
    for v in (None, "", "?"):
        assert _cell(v) == "—"
    assert _cell("cloudflare") == "cloudflare"
    assert _cell(0) == "0"  # 0 is a real value, not absence
    assert _cell(False) == "False"


def test_truncate_excerpt_closes_orphan_fence():
    """RAG hits often straddle a ``` boundary. After truncation we must
    not leave an unbalanced fence — the widget would strip it and the
    body would render as plain text."""
    raw = "intro\n```yaml\nkey: value\nmore stuff that overflows the cap"
    out = _truncate_excerpt(raw, 30)
    # Odd-count of ``` triggers the synthetic close before the ellipsis.
    fences = out.count("```")
    assert fences % 2 == 0
    # Ellipsis terminator present.
    assert out.endswith("…")


def test_truncate_excerpt_passthrough_when_short():
    s = "short content"
    assert _truncate_excerpt(s, 100) == s


def test_json_codeblock_always_closes_fence():
    """A long JSON payload must never leave an open ```."""
    big = {"data": "x" * 5000}
    out = _json_codeblock(big, max_chars=200)
    assert out.startswith("```json\n")
    assert out.rstrip().endswith("```")
    assert "…truncated" in out


def test_md_table_handles_empty():
    assert _md_table([], ["a", "b"]) == "_(no results)_"


def test_md_table_renders_cells_in_order():
    rows = [{"domain": "x.com", "days": 5}, {"domain": "y.com", "days": 12}]
    out = _md_table(rows, ["domain", "days"])
    # Header + separator + 2 body rows = 4 lines.
    assert out.count("\n") == 3
    assert "| x.com | 5 |" in out
    assert "| y.com | 12 |" in out
