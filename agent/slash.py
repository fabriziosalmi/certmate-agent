"""Deterministic slash-command router.

Commands bypass the LLM entirely: parse argv -> dispatch tool -> format
result with a Python formatter. Sub-200ms for the frequent queries.

Write commands ('/renew', '/deploy', etc.) reuse the same pending_action
flow as LLM-emitted tool calls — they don't execute directly; the widget
must POST /tools/execute with the issued token.

Each handler is an async generator yielding the same event dicts as the
LLM chat loop (see chat_loop.py), so the widget renders them identically.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import shlex
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .certmate_client import CertMateClient, CertMateError
from .config import settings
from .db import audit, save_pending_action
from .rag import get_store
from .rag.indexer import (
    DEFAULT_BRANCH,
    DEFAULT_INDEX_PATH,
    DEFAULT_PATHS,
    DEFAULT_REPO,
    IndexerError,
    build_index_iter,
)
from .tools import REGISTRY, ToolKind, get_tool

# Per-turn session id, set by dispatch() before invoking a handler. Read by
# _run_read / _propose_write so they can forward it to CertMate audit. Using
# a ContextVar keeps the handler signatures untouched (12 handlers).
_current_session: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_session", default=None
)

Handler = Callable[..., AsyncGenerator[dict[str, Any], None]]


_reindex_lock = asyncio.Lock()

@dataclass
class SlashCommand:
    name: str
    handler: Handler
    summary: str
    usage: str
    aliases: list[str] = field(default_factory=list)
    admin_only: bool = False
    # If True, the command requires a live CertMate API connection and is
    # therefore unavailable in docs_only mode.
    requires_certmate: bool = True


_COMMANDS: dict[str, SlashCommand] = {}


def _register(cmd: SlashCommand) -> None:
    _COMMANDS[cmd.name] = cmd
    for a in cmd.aliases:
        _COMMANDS[a] = cmd


def list_commands() -> list[SlashCommand]:
    seen: set[str] = set()
    out: list[SlashCommand] = []
    for c in _COMMANDS.values():
        if c.name in seen:
            continue
        seen.add(c.name)
        out.append(c)
    return out


# ---------- output helpers ----------

def _cell(v: Any) -> str:
    """Render a value for a markdown table cell. Empty / null / placeholder
    values collapse to an em-dash; the widget then styles them subtly via
    the .empty class so missing data reads as 'absent', not '?'."""
    if v is None or v == "" or v == "?":
        return "—"
    return str(v)


def _md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_(no results)_"
    header = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = []
    for r in rows:
        body.append(
            "| " + " | ".join(str(r.get(c, "")) for c in columns) + " |"
        )
    return "\n".join([header, sep, *body])


def _emit_status(msg: str) -> dict[str, Any]:
    return {"event": "status", "data": {"message": msg}}


def _emit_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"event": "tool_call", "data": {"name": name, "args": args}}


def _emit_tool_result(name: str, ok: bool, preview: Any) -> dict[str, Any]:
    p = preview if isinstance(preview, str) else json.dumps(preview, default=str)[:400]
    return {"event": "tool_result", "data": {"name": name, "ok": ok, "preview": p}}


def _emit_message(content: str) -> dict[str, Any]:
    return {"event": "message", "data": {"role": "assistant", "content": content}}


def _emit_error(msg: str) -> dict[str, Any]:
    return {"event": "error", "data": {"message": msg}}


def _emit_done() -> dict[str, Any]:
    return {"event": "done", "data": {}}


def _truncate_excerpt(text: str, max_chars: int) -> str:
    """Truncate a markdown excerpt without leaving an orphan code fence.

    RAG hits are arbitrary slices of doc chunks; a hard cut can land in
    the middle of a ``` block, leaving an open fence that bleeds into
    everything that follows. Count fence markers in the truncated body;
    if odd, append a synthetic close so the rest of the message renders
    cleanly.
    """
    if len(text) <= max_chars:
        return text
    body = text[:max_chars]
    if body.count("```") % 2 == 1:
        body = body.rstrip() + "\n```"
    return body + " …"


def _json_codeblock(data: Any, max_chars: int = 1600) -> str:
    """Render JSON inside a fenced code block, truncating safely.

    We cut on a newline boundary when possible and append an ellipsis line so
    the closing ``` is never lost in mid-token, which would leak markdown
    state into the rest of the assistant message.
    """
    text = json.dumps(data, indent=2, default=str)
    if len(text) <= max_chars:
        return f"```json\n{text}\n```"
    cut = text.rfind("\n", 0, max_chars)
    if cut < max_chars // 2:  # no decent boundary; hard cut
        cut = max_chars
    return f"```json\n{text[:cut]}\n  …truncated ({len(text) - cut} more chars)\n```"


async def _run_read(
    tool_name: str,
    args: dict[str, Any],
    *,
    result_box: list[Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Dispatch a read tool from REGISTRY and yield SSE events.

    If `result_box` is given, append (ok: bool, result: Any) so the caller
    can format the full untruncated result. SSE events still carry the
    standard truncated preview for the widget log.
    """
    tool = get_tool(tool_name)
    if tool is None or tool.kind is not ToolKind.READ:
        yield _emit_error(f"internal: tool '{tool_name}' missing or not read-kind")
        if result_box is not None:
            result_box.append((False, None))
        return
    yield _emit_tool_call(tool_name, args)
    try:
        async with CertMateClient(agent_session_id=_current_session.get()) as c:
            result = await tool.executor(c, dict(args))
        audit("slash_call", "ok", tool_name=tool_name, args=args)
        yield _emit_tool_result(tool_name, True, result)
        if result_box is not None:
            result_box.append((True, result))
        return
    except CertMateError as e:
        audit("slash_call", "error", tool_name=tool_name, args=args,
              detail=f"http_{e.status}: {e}")
        err = {"error": str(e), "status": e.status}
        yield _emit_tool_result(tool_name, False, err)
        if result_box is not None:
            result_box.append((False, err))
    except Exception as e:
        audit("slash_call", "error", tool_name=tool_name, args=args, detail=str(e))
        err = {"error": str(e)}
        yield _emit_tool_result(tool_name, False, err)
        if result_box is not None:
            result_box.append((False, err))


async def _propose_write(
    tool_name: str, args: dict[str, Any]
) -> AsyncGenerator[dict[str, Any], None]:
    """Queue a write tool as a pending_action and emit the confirm event."""
    tool = get_tool(tool_name)
    if tool is None or tool.kind is ToolKind.READ:
        yield _emit_error(f"internal: tool '{tool_name}' is not a write tool")
        return
    summary = tool.summarize(args) if tool.summarize else f"Run {tool_name}"
    token = await asyncio.to_thread(
        save_pending_action, tool_name, args, summary, tool.kind.value,
        _current_session.get(),
    )
    audit("slash_pending", "queued", tool_name=tool_name, args=args, detail=token)
    yield {
        "event": "pending_confirm",
        "data": {
            "token": token,
            "tool": tool_name,
            "args": args,
            "summary": summary,
            "kind": tool.kind.value,
        },
    }


# ---------- handlers ----------

async def _h_help(_argv: list[str], is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    lines = ["**Slash commands**", ""]
    for cmd in list_commands():
        if cmd.admin_only and not is_admin:
            continue
        if settings.is_docs_only and cmd.requires_certmate:
            continue
        aliases = f" _(also: {', '.join('/' + a for a in cmd.aliases)})_" if cmd.aliases else ""
        tag = " _(admin)_" if cmd.admin_only else ""
        lines.append(f"- `{cmd.usage}` — {cmd.summary}{tag}{aliases}")
    lines.append("")
    if settings.is_docs_only:
        lines.append(
            "_Mode: **docs_only** — this instance has no live CertMate connection. "
            "Ask questions about CertMate and its docs._"
        )
    else:
        lines.append("Type a free-form question to talk to the LLM instead.")
    yield _emit_message("\n".join(lines))
    yield _emit_done()


def _unpack(box: list[Any]) -> tuple[bool, Any]:
    return box[0] if box else (False, None)


async def _h_health(_argv: list[str], _is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    box: list[Any] = []
    async for ev in _run_read("system_health", {}, result_box=box):
        yield ev
    ok, data = _unpack(box)
    if ok and isinstance(data, dict):
        status = data.get("status", "?")
        checks = data.get("checks") or {}
        details = "\n".join(f"- `{k}`: {v}" for k, v in checks.items()) or "_(no checks)_"
        yield _emit_message(f"**CertMate health: `{status}`**\n\n{details}")
    else:
        yield _emit_message("Health check failed — CertMate unreachable.")
    yield _emit_done()


async def _h_status(_argv: list[str], _is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    box: list[Any] = []
    async for ev in _run_read("system_overview", {}, result_box=box):
        yield ev
    ok, data = _unpack(box)
    if ok and isinstance(data, dict):
        health = data.get("health") or {}
        cert_count = data.get("cert_count")
        expiring = data.get("expiring_within_30d") or []
        msg = [
            f"**Health:** `{_cell(health.get('status'))}`",
            f"**Certificates:** {_cell(cert_count)}",
            "",
        ]
        if expiring:
            msg.append(f"**Expiring within 30 days ({len(expiring)}):**")
            # Normalize cells so missing fields render as em-dash, not "None".
            normalized = [
                {k: _cell(v) for k, v in row.items()} for row in expiring
            ]
            msg.append(_md_table(normalized, ["domain", "days_until_expiry", "status"]))
        else:
            msg.append("_No certificates expiring within 30 days._")
        yield _emit_message("\n".join(msg))
    else:
        yield _emit_message("Could not fetch system overview.")
    yield _emit_done()


async def _h_expiring(argv: list[str], _is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    days = 30
    if argv:
        try:
            days = int(argv[0])
        except ValueError:
            yield _emit_error(f"`/expiring`: '{argv[0]}' is not a number of days")
            yield _emit_done()
            return
    box: list[Any] = []
    async for ev in _run_read("cert_list", {"expiring_within_days": days}, result_box=box):
        yield ev
    ok, data = _unpack(box)
    if ok and isinstance(data, list) and data:
        rows = [
            {
                "domain": _cell(c.get("domain")),
                "days": _cell(c.get("days_until_expiry")),
                "status": _cell(c.get("status")),
                "provider": _cell(c.get("dns_provider") or c.get("provider")),
            }
            for c in data if isinstance(c, dict)
        ]
        yield _emit_message(
            f"**{len(rows)} certificate(s) expiring within {days} days:**\n\n"
            + _md_table(rows, ["domain", "days", "status", "provider"])
        )
    elif ok:
        yield _emit_message(f"_No certificates expiring within {days} days._")
    else:
        yield _emit_message("Could not list certificates.")
    yield _emit_done()


async def _h_list(_argv: list[str], _is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    box: list[Any] = []
    async for ev in _run_read("cert_list", {}, result_box=box):
        yield ev
    ok, data = _unpack(box)
    if ok and isinstance(data, list) and data:
        rows = [
            {
                "domain": _cell(c.get("domain")),
                "days": _cell(c.get("days_until_expiry")),
                "status": _cell(c.get("status")),
                "auto_renew": _cell(c.get("auto_renew")),
            }
            for c in data if isinstance(c, dict)
        ]
        yield _emit_message(
            f"**{len(rows)} certificate(s):**\n\n"
            + _md_table(rows, ["domain", "days", "status", "auto_renew"])
        )
    elif ok:
        yield _emit_message("_No certificates managed yet._")
    else:
        yield _emit_message("Could not list certificates.")
    yield _emit_done()


async def _h_cert(argv: list[str], _is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    if not argv:
        yield _emit_error("Usage: `/cert <domain>`")
        yield _emit_done()
        return
    domain = argv[0]
    box: list[Any] = []
    async for ev in _run_read("cert_get", {"domain": domain}, result_box=box):
        yield ev
    ok, data = _unpack(box)
    if ok and isinstance(data, dict):
        keys = ("domain", "status", "days_until_expiry", "not_after",
                "dns_provider", "ca_provider", "auto_renew", "wildcard")
        lines = [f"**Certificate `{domain}`**", ""]
        for k in keys:
            if k in data:
                lines.append(f"- `{k}`: {data[k]}")
        yield _emit_message("\n".join(lines))
    else:
        yield _emit_message(f"Could not fetch `{domain}`.")
    yield _emit_done()


async def _h_providers(_argv: list[str], _is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    box: list[Any] = []
    async for ev in _run_read("dns_providers_info", {}, result_box=box):
        yield ev
    ok, data = _unpack(box)
    if ok:
        yield _emit_message(_json_codeblock(data))
    else:
        yield _emit_message("Could not fetch DNS providers.")
    yield _emit_done()


async def _h_accounts(argv: list[str], _is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    args: dict[str, Any] = {}
    if argv:
        args["provider"] = argv[0]
    box: list[Any] = []
    async for ev in _run_read("dns_accounts_list", args, result_box=box):
        yield ev
    ok, data = _unpack(box)
    if ok:
        yield _emit_message(_json_codeblock(data))
    else:
        yield _emit_message("Could not list DNS accounts.")
    yield _emit_done()


async def _h_backups(_argv: list[str], _is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    box: list[Any] = []
    async for ev in _run_read("backups_list", {}, result_box=box):
        yield ev
    ok, data = _unpack(box)
    if ok:
        yield _emit_message(_json_codeblock(data))
    else:
        yield _emit_message("Could not list backups.")
    yield _emit_done()


async def _h_renew(argv: list[str], _is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    if not argv:
        yield _emit_error("Usage: `/renew <domain> [--force]`")
        yield _emit_done()
        return
    args = {"domain": argv[0]}
    if any(a in ("--force", "-f") for a in argv[1:]):
        args["force"] = True
    async for ev in _propose_write("cert_renew", args):
        yield ev
    yield _emit_message(f"Action queued. Click **Execute** to renew `{argv[0]}`.")
    yield _emit_done()


async def _h_deploy(argv: list[str], _is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    if not argv:
        yield _emit_error("Usage: `/deploy <domain>`")
        yield _emit_done()
        return
    async for ev in _propose_write("cert_deploy", {"domain": argv[0]}):
        yield ev
    yield _emit_message(f"Action queued. Click **Execute** to run the deploy hook for `{argv[0]}`.")
    yield _emit_done()


async def _h_cache_clear(_argv: list[str], _is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    async for ev in _propose_write("cache_clear", {}):
        yield ev
    yield _emit_message("Action queued. Click **Execute** to clear cache.")
    yield _emit_done()


async def _h_reindex(argv: list[str], is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    if not settings.agent_admin_token:
        yield _emit_error(
            "Admin commands disabled: set `AGENT_ADMIN_TOKEN` to enable."
        )
        yield _emit_done()
        return
    if not is_admin:
        yield _emit_error(
            "Forbidden: this command requires the admin token "
            "(send via `X-Agent-Admin` header or `admin_token` body field)."
        )
        yield _emit_done()
        return
    if _reindex_lock.locked():
        yield _emit_error("A reindex is already running. Try again in a minute.")
        yield _emit_done()
        return

    # Optional positional args: repo, branch
    repo = argv[0] if len(argv) >= 1 else DEFAULT_REPO
    branch = argv[1] if len(argv) >= 2 else DEFAULT_BRANCH
    out_path = Path(settings.agent_index_path)
    if not out_path.is_absolute():
        out_path = (DEFAULT_INDEX_PATH.parent.parent / out_path).resolve()

    async with _reindex_lock:
        yield _emit_message(f"Starting reindex of `{repo}@{branch}` …")
        audit("reindex", "start", detail=f"{repo}@{branch}")
        try:
            async for ev in build_index_iter(
                repo=repo, branch=branch, paths=DEFAULT_PATHS, out_path=out_path
            ):
                phase = ev["phase"]
                if phase == "start":
                    yield _emit_status(f"fetching {ev['files']} files …")
                elif phase == "fetch":
                    if "skipped" in ev:
                        yield _emit_status(f"[{ev['done']}/{ev['total']}] skipped: {ev['path']}")
                    else:
                        yield _emit_status(
                            f"[{ev['done']}/{ev['total']}] {ev['path']} → {ev['chunks']} chunks"
                        )
                elif phase == "embed":
                    yield _emit_status(f"embedding {ev['done']}/{ev['total']}")
                elif phase == "done":
                    kb = ev["bytes"] // 1024
                    audit("reindex", "ok", detail=f"{ev['chunks']} chunks, {kb} KB")
                    # Hot-swap the in-memory store and invalidate the
                    # docs_search cache (cached scores reference old chunks).
                    swapped = await asyncio.to_thread(get_store().reload)
                    from .rag.cache import get_cache
                    get_cache().bump_generation()
                    yield _emit_message(
                        f"**Reindex complete.**\n\n"
                        f"- chunks: {ev['chunks']}\n"
                        f"- size: {kb} KB\n"
                        f"- elapsed: {ev['elapsed_s']}s\n"
                        f"- in-memory swap: {'ok' if swapped else 'failed'}"
                    )
        except IndexerError as e:
            audit("reindex", "error", detail=str(e))
            yield _emit_error(f"Reindex failed: {e}")
        except Exception as e:
            audit("reindex", "error", detail=str(e))
            yield _emit_error(f"Reindex failed: {e}")
    yield _emit_done()


async def _h_docs(argv: list[str], _is_admin: bool = False) -> AsyncGenerator[dict[str, Any], None]:
    if not argv:
        yield _emit_error("Usage: `/docs <natural language query>`")
        yield _emit_done()
        return
    query = " ".join(argv)
    box: list[Any] = []
    async for ev in _run_read("docs_search", {"query": query, "k": 3}, result_box=box):
        yield ev
    ok, payload = _unpack(box)
    if not ok or not isinstance(payload, dict):
        yield _emit_message("Could not search docs.")
        yield _emit_done()
        return
    if not payload.get("ready"):
        yield _emit_message("Docs index not built. Run: `python -m agent.rag.indexer`")
        yield _emit_done()
        return
    hits = payload.get("hits") or []
    if not hits:
        yield _emit_message(f"_No relevant docs for_ `{query}`")
        yield _emit_done()
        return
    lines = [f"**Top {len(hits)} excerpt(s) for** `{query}`", ""]
    for h in hits:
        title = h.get("title") or "_"
        # Strip underscores from the title so they can't open stray italic
        # spans in the heading (titles often contain identifiers like
        # API_BEARER_TOKEN_FILE that would otherwise corrupt the render).
        safe_title = title.replace("_", " ")
        head = f"`{h['source']}` _({safe_title})_ — score {h['score']}"
        body = _truncate_excerpt(h["text"], 600)
        lines.append(f"### {head}")
        lines.append(body)
        lines.append("")
    yield _emit_message("\n".join(lines))
    yield _emit_done()


# ---------- registration ----------

_register(SlashCommand("help", _h_help, "List slash commands.", "/help",
                       aliases=["?"], requires_certmate=False))
_register(SlashCommand("health", _h_health, "CertMate service health.", "/health"))
_register(SlashCommand("status", _h_status,
                       "Overview: health + cert count + expiring within 30d.",
                       "/status", aliases=["overview"]))
_register(SlashCommand("expiring", _h_expiring,
                       "Certs expiring within N days (default 30).",
                       "/expiring [days]"))
_register(SlashCommand("list", _h_list, "All managed certificates.",
                       "/list", aliases=["certs", "ls"]))
_register(SlashCommand("cert", _h_cert, "Details for one certificate.",
                       "/cert <domain>"))
_register(SlashCommand("providers", _h_providers,
                       "Supported and configured DNS providers.",
                       "/providers", aliases=["dns"]))
_register(SlashCommand("accounts", _h_accounts,
                       "Configured DNS accounts; optionally filtered by provider.",
                       "/accounts [provider]"))
_register(SlashCommand("backups", _h_backups, "List available backups.", "/backups"))
_register(SlashCommand("renew", _h_renew,
                       "Renew a certificate (requires confirm).",
                       "/renew <domain> [--force]"))
_register(SlashCommand("deploy", _h_deploy,
                       "Run deploy hook for a certificate (requires confirm).",
                       "/deploy <domain>"))
_register(SlashCommand("cache-clear", _h_cache_clear,
                       "Clear server cache (requires confirm).",
                       "/cache-clear"))
_register(SlashCommand("docs", _h_docs,
                       "Search the CertMate documentation (RAG over docs).",
                       "/docs <query>",
                       aliases=["ask"],
                       requires_certmate=False))
_register(SlashCommand("reindex", _h_reindex,
                       "Rebuild the docs index (admin only; requires AGENT_ADMIN_TOKEN).",
                       "/reindex [repo] [branch]",
                       admin_only=True,
                       requires_certmate=False))


# ---------- entrypoint used by chat_loop ----------

def parse(message: str) -> tuple[str, list[str]] | None:
    """Return (command_name, argv) if message starts with '/', else None.
    Returns ('', []) for an empty slash to render help safely upstream.
    """
    m = message.strip()
    if not m.startswith("/"):
        return None
    body = m[1:].strip()
    if not body:
        return ("help", [])
    try:
        parts = shlex.split(body)
    except ValueError:
        parts = body.split()
    return (parts[0].lower(), parts[1:])


async def dispatch(
    message: str,
    *,
    is_admin: bool = False,
    session_id: str | None = None,
) -> AsyncGenerator[dict[str, Any], None] | None:
    """If message is a slash command, return the handler's async generator.
    Returns None if not a slash command (caller should fall through to LLM).
    """
    parsed = parse(message)
    if parsed is None:
        return None
    name, argv = parsed
    cmd = _COMMANDS.get(name)
    # Stash session_id in a ContextVar so _run_read / _propose_write can
    # forward it to CertMate without changing 12 handler signatures.
    _current_session.set(session_id)
    if cmd is None:
        async def _unknown() -> AsyncGenerator[dict[str, Any], None]:
            yield _emit_error(
                f"Unknown command `/{name}`. Type `/help` for the list."
            )
            yield _emit_done()
        return _unknown()
    if settings.is_docs_only and cmd.requires_certmate:
        async def _disabled() -> AsyncGenerator[dict[str, Any], None]:
            yield _emit_error(
                f"`/{name}` is not available in docs_only mode "
                "(no CertMate connection). Try `/docs <question>` or `/help`."
            )
            yield _emit_done()
        return _disabled()
    return cmd.handler(argv, is_admin)


# Sanity: every tool we reference here must exist in REGISTRY *for the
# current mode*. In docs_only the CertMate-coupled tools are absent on
# purpose, so we only require docs_search.
_log_slash = logging.getLogger(__name__)
_REFERENCED_TOOLS_FULL = {
    "system_health", "system_overview", "cert_list", "cert_get",
    "dns_providers_info", "dns_accounts_list", "backups_list",
    "cert_renew", "cert_deploy", "cache_clear", "docs_search",
}
_REFERENCED_TOOLS_DOCS_ONLY = {"docs_search"}
_required = (
    _REFERENCED_TOOLS_DOCS_ONLY if settings.is_docs_only else _REFERENCED_TOOLS_FULL
)
_missing = _required - set(REGISTRY)
if _missing:  # pragma: no cover - dev guard
    _log_slash.warning("slash.py references missing tools for mode=%s: %s",
                       settings.agent_mode, _missing)
