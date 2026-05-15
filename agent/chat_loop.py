"""Chat orchestration: LLM tool-calling loop + SSE event stream.

Yields dict events that the API layer formats as SSE. Event types:

  {"event": "status",      "data": {"message": "..."}}
  {"event": "tool_call",   "data": {"name": ..., "args": {...}}}
  {"event": "tool_result", "data": {"name": ..., "ok": true, "preview": "..."}}
  {"event": "pending_confirm", "data": {"token": ..., "tool": ..., "args": ..., "summary": ..., "kind": ...}}
  {"event": "token",       "data": {"text": "..."}}     # streamed final assistant content
  {"event": "message",     "data": {"role": "assistant", "content": "..."}}  # full message at end
  {"event": "error",       "data": {"message": "..."}}
  {"event": "done",        "data": {}}

For Gemma/Qwen on LM Studio, we call non-streaming on iterations that
include tool calls (to get the full tool_calls payload cleanly), and
stream the FINAL assistant message to the user.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from . import slash
from .certmate_client import CertMateClient, CertMateError
from .config import settings
from .db import audit, conversation_append, conversation_load, save_pending_action
from .llm import ChainError, ChatLLM
from .llm.lmstudio import LMStudioError
from .tools import REGISTRY, ToolKind, get_tool, openai_tool_schemas

SYSTEM_PROMPT = """\
You are CertMate-Agent: a focused, terse assistant embedded in CertMate, an SSL certificate management system.

Capabilities:
- Call tools to read live state (cert_list, cert_get, system_overview, dns_providers_info, etc.).
- Call `docs_search` for knowledge questions ("what is DNS-01?", "how do deploy hooks work?", "which providers support wildcard?", "how do I configure Hetzner DNS?").
- Propose write actions (cert_renew, cert_create, dns_account_add, etc.) — these are NEVER executed directly; the UI confirms with the user.

Tool selection rules:
- Live state question → cert_list / cert_get / system_overview / dns_accounts_list / backups_list.
- Conceptual / how-to / "what does X mean" → docs_search FIRST, then answer using the excerpts.
- Vague status question → call system_overview.
- Never guess about CertMate features; call docs_search.

Output rules:
- Reference domains in backticks. Use absolute dates (not "in 2 weeks").
- When you used docs_search, cite the source filename in parentheses, e.g. "(docs/dns-providers.md)".
- Be concise. Bullet lists for >3 items. No filler.
- If a tool errors, explain briefly what went wrong and what the user can do.
"""


def _preview(value: Any, max_chars: int = 400) -> str:
    try:
        s = json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        s = str(value)
    return s if len(s) <= max_chars else s[: max_chars - 3] + "..."


async def _execute_read_tool(
    tool_name: str, args: dict[str, Any], certmate: CertMateClient
) -> tuple[bool, Any]:
    tool = get_tool(tool_name)
    if tool is None:
        return False, f"Unknown tool '{tool_name}'"
    if tool.kind is not ToolKind.READ:
        return False, f"Tool '{tool_name}' is not a read tool"
    try:
        result = await tool.executor(certmate, dict(args))
        audit("tool_call", "ok", tool_name=tool_name, args=args)
        return True, result
    except CertMateError as e:
        audit("tool_call", "error", tool_name=tool_name, args=args,
              detail=f"http_{e.status}: {e}")
        return False, {"error": str(e), "status": e.status}
    except Exception as e:
        audit("tool_call", "error", tool_name=tool_name, args=args, detail=str(e))
        return False, {"error": str(e)}


async def run_turn(
    user_message: str,
    history: list[dict[str, Any]] | None = None,
    *,
    is_admin: bool = False,
    session_id: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run one chat turn. Yields SSE event dicts.

    If `session_id` is set AND server-side persistence is enabled in
    settings, the canonical history is loaded from sqlite (the `history`
    arg is ignored) and the new user/assistant pair is appended after
    the turn completes.
    """
    # Slash commands short-circuit the LLM entirely.
    slash_gen = await slash.dispatch(user_message, is_admin=is_admin)
    if slash_gen is not None:
        audit("turn", "slash", detail=user_message[:200])
        async for ev in slash_gen:
            yield ev
        # Still record slash commands so the session feels continuous.
        if settings.agent_persist_conversations and session_id:
            await asyncio.to_thread(conversation_append, session_id, "user", user_message)
        return

    use_persistence = bool(settings.agent_persist_conversations and session_id)
    if use_persistence:
        loaded = await asyncio.to_thread(conversation_load, session_id)
        history = loaded
    else:
        history = list(history or [])
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    final_assistant_text: str | None = None

    tools_schema = openai_tool_schemas()

    async with ChatLLM() as llm, CertMateClient() as certmate:
        for iteration in range(settings.agent_max_tool_iterations):
            yield {"event": "status", "data": {"message": f"thinking (iter {iteration + 1})"}}

            # Stream the response: accumulate content tokens (emit each as
            # event:token) and tool_call argument fragments (kept silent
            # until complete so we can dispatch once finish_reason fires).
            content_parts: list[str] = []
            tc_acc: dict[int, dict[str, Any]] = {}
            finish_reason: str | None = None

            try:
                async for chunk in llm.chat_stream(messages, tools=tools_schema):
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    if isinstance(delta.get("content"), str) and delta["content"]:
                        content_parts.append(delta["content"])
                        yield {"event": "token", "data": {"text": delta["content"]}}
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        acc = tc_acc.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                        if tc.get("id"):
                            acc["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            acc["name"] = fn["name"]
                        if fn.get("arguments"):
                            acc["arguments"] += fn["arguments"]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
            except (LMStudioError, ChainError) as e:
                yield {"event": "error", "data": {"message": f"LLM error: {e}"}}
                return

            if llm.last_provider and llm.last_provider != "lmstudio":
                yield {"event": "status",
                       "data": {"message": f"served via {llm.last_provider}"}}

            assistant_content = "".join(content_parts)
            tool_calls = [
                {
                    "id": v["id"] or f"call_{idx}",
                    "type": "function",
                    "function": {"name": v["name"], "arguments": v["arguments"] or "{}"},
                }
                for idx, v in sorted(tc_acc.items())
                if v.get("name")
            ]

            messages.append({
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": tool_calls if tool_calls else None,
            })

            if not tool_calls:
                # Final answer fully streamed already.
                if assistant_content:
                    final_assistant_text = assistant_content
                    yield {"event": "message",
                           "data": {"role": "assistant", "content": assistant_content}}
                else:
                    yield {"event": "error",
                           "data": {"message": "model returned empty content (try a larger "
                                               "max_tokens or a non-thinking model like "
                                               "qwen/qwen3-8b)"}}
                if use_persistence:
                    await asyncio.to_thread(
                        conversation_append, session_id, "user", user_message
                    )
                    if final_assistant_text:
                        await asyncio.to_thread(
                            conversation_append, session_id, "assistant", final_assistant_text
                        )
                yield {"event": "done", "data": {}}
                return

            pending_emitted = False
            for tc in tool_calls:
                fn = (tc.get("function") or {})
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {}

                yield {"event": "tool_call", "data": {"name": name, "args": args}}
                tool = get_tool(name)
                if tool is None:
                    tool_result = {"error": f"Unknown tool '{name}'"}
                    yield {"event": "tool_result",
                           "data": {"name": name, "ok": False, "preview": _preview(tool_result)}}
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": json.dumps(tool_result),
                    })
                    continue

                if tool.kind is ToolKind.READ:
                    ok, result = await _execute_read_tool(name, args, certmate)
                    yield {"event": "tool_result",
                           "data": {"name": name, "ok": ok, "preview": _preview(result)}}
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": json.dumps(result, default=str),
                    })
                else:
                    summary = tool.summarize(args) if tool.summarize else f"Run {name}"
                    token = await asyncio.to_thread(
                        save_pending_action, name, args, summary, tool.kind.value,
                    )
                    audit("pending_action", "queued", tool_name=name, args=args, detail=token)
                    yield {
                        "event": "pending_confirm",
                        "data": {
                            "token": token,
                            "tool": name,
                            "args": args,
                            "summary": summary,
                            "kind": tool.kind.value,
                        },
                    }
                    # Tell the model we DID NOT execute — it should explain to the user
                    # and stop calling this tool again unless the user retries.
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": json.dumps({
                            "status": "pending_user_confirmation",
                            "summary": summary,
                            "note": "Action queued. The user must click 'Execute' "
                                    "in the UI to actually run it.",
                        }),
                    })
                    pending_emitted = True

            if pending_emitted:
                # Let model produce the user-facing explanation in next iteration.
                continue

        yield {"event": "error",
               "data": {"message":
                        f"Exceeded {settings.agent_max_tool_iterations} tool iterations"}}
        yield {"event": "done", "data": {}}
