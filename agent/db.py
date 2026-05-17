"""Lightweight sqlite store for conversations, pending confirms, and audit.

Sync API (sqlite3 stdlib) wrapped in `asyncio.to_thread` at call sites.
Single-writer model — fine for an embedded agent.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    ts           INTEGER NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conv_session_ts
    ON conversation_messages(session_id, ts);

CREATE TABLE IF NOT EXISTS pending_actions (
    token        TEXT PRIMARY KEY,
    created_at   INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    tool_name    TEXT NOT NULL,
    args_json    TEXT NOT NULL,
    summary      TEXT NOT NULL,
    kind         TEXT NOT NULL,
    consumed     INTEGER NOT NULL DEFAULT 0,
    session_id   TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    tool_name    TEXT,
    args_json    TEXT,
    status       TEXT NOT NULL,
    detail       TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_expires ON pending_actions(expires_at);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
"""


_conn_cache: sqlite3.Connection | None = None
_conn_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    """Process-singleton sqlite connection.

    Single-writer model: opening/closing per call wastes ~0.3ms each and
    re-executes PRAGMAs that are persistent anyway. WAL allows concurrent
    readers, and ``check_same_thread=False`` lets the asyncio thread pool
    (asyncio.to_thread) share one connection — guarded by a write lock on
    the application side via Python's GIL + the asyncio.to_thread pool's
    per-call serialization.
    """
    global _conn_cache
    if _conn_cache is not None:
        return _conn_cache
    with _conn_lock:
        if _conn_cache is not None:
            return _conn_cache
        path = Path(settings.agent_db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")  # WAL-safe, faster commits
        _conn_cache = conn
        return conn


def init_db() -> None:
    c = _conn()
    c.executescript(_SCHEMA)
    # Idempotent migration (additive only — sqlite has no DROP COLUMN
    # before 3.35, but we don't need that). Re-runnable on every boot.
    cols = {row["name"] for row in c.execute("PRAGMA table_info(pending_actions)")}
    if "session_id" not in cols:
        c.execute("ALTER TABLE pending_actions ADD COLUMN session_id TEXT")
    c.commit()


# ---------- pending actions ----------

def save_pending_action(
    tool_name: str,
    args: dict[str, Any],
    summary: str,
    kind: str,
    session_id: str | None = None,
) -> str:
    token = secrets.token_urlsafe(24)
    now = int(time.time())
    expires = now + settings.agent_confirm_token_ttl_seconds
    with _conn() as c:
        c.execute(
            "INSERT INTO pending_actions(token, created_at, expires_at, tool_name, "
            "args_json, summary, kind, consumed, session_id) VALUES (?,?,?,?,?,?,?,0,?)",
            (token, now, expires, tool_name, json.dumps(args), summary, kind, session_id),
        )
    return token


def consume_pending_action(token: str) -> dict[str, Any] | None:
    """Mark a pending action consumed and return its payload. None if missing/expired/used."""
    now = int(time.time())
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM pending_actions WHERE token=?", (token,)
        ).fetchone()
        if not row or row["consumed"] or row["expires_at"] < now:
            return None
        c.execute("UPDATE pending_actions SET consumed=1 WHERE token=?", (token,))
        return {
            "tool_name": row["tool_name"],
            "args": json.loads(row["args_json"]),
            "summary": row["summary"],
            "kind": row["kind"],
            # session_id may be NULL on rows created before the migration.
            "session_id": row["session_id"] if "session_id" in row.keys() else None,
        }


def prune_expired_pending() -> int:
    """Drop pending_actions rows that are either:
      - past their `expires_at` (token can never be used anymore), or
      - already `consumed=1` and at least 1h past `created_at` (kept
        around briefly for debugging double-submit, then GC'd).

    The audit table records the actual execution, so deleting these
    rows is not a loss of forensic data.
    """
    now = int(time.time())
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM pending_actions "
            "WHERE expires_at < ? OR (consumed = 1 AND created_at < ?)",
            (now, now - 3600),
        )
        return cur.rowcount


# ---------- audit log ----------

def audit(kind: str, status: str, *, tool_name: str | None = None,
          args: dict[str, Any] | None = None, detail: str | None = None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO audit_log(ts, kind, tool_name, args_json, status, detail) "
            "VALUES (?,?,?,?,?,?)",
            (int(time.time()), kind, tool_name,
             json.dumps(args) if args is not None else None, status, detail),
        )


# ---------- conversation persistence ----------

# Only roles we want to round-trip through the LLM. tool_call / tool_result
# events are session-scoped and rebuilt each turn; we do not persist them.
_PERSISTED_ROLES = {"user", "assistant"}


def conversation_load(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Return the most recent `limit` messages for a session_id, oldest first.

    We pull the last N by descending id (cheap on the (session_id, ts) index)
    then reverse in Python so the LLM sees chronological order. Using ASC LIMIT
    would silently truncate the *latest* turns on long sessions.
    """
    if not session_id:
        return []
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content FROM conversation_messages "
            "WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    rows.reverse()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def conversation_append(session_id: str, role: str, content: str) -> None:
    if not session_id or role not in _PERSISTED_ROLES:
        return
    with _conn() as c:
        c.execute(
            "INSERT INTO conversation_messages(session_id, ts, role, content) "
            "VALUES (?,?,?,?)",
            (session_id, int(time.time()), role, content),
        )


def conversation_clear(session_id: str) -> int:
    if not session_id:
        return 0
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM conversation_messages WHERE session_id=?", (session_id,)
        )
        return cur.rowcount


def conversation_prune_older_than(days: int) -> int:
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86400
    with _conn() as c:
        cur = c.execute("DELETE FROM conversation_messages WHERE ts < ?", (cutoff,))
        return cur.rowcount


def audit_prune_older_than(days: int) -> int:
    """Trim the audit log to the last `days` days. days<=0 means never prune."""
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86400
    with _conn() as c:
        cur = c.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,))
        return cur.rowcount
