"""SSE chat endpoint and confirm/execute endpoint."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..certmate_client import CertMateClient, CertMateError
from ..chat_loop import run_turn
from ..config import settings
from ..db import audit, consume_pending_action
from ..rate_limit import (
    ConcurrencyLimiter,
    RateLimiter,
    client_key,
    enforce,
)
from ..tools import get_tool

router = APIRouter()

# Rate limiters live at module scope: shared across requests within this
# worker, but per-worker (see rate_limit.py module docstring).
_chat_rl = RateLimiter(settings.agent_ratelimit_chat_per_min, 60.0)
_execute_rl = RateLimiter(settings.agent_ratelimit_execute_per_min, 60.0)
_chat_concurrency = ConcurrencyLimiter(settings.agent_ratelimit_chat_concurrency)


def _check_origin(origin: str | None) -> None:
    """Reject browser POSTs from origins not on the configured allowlist.

    Defense against click-jacking and embedding the widget on a hostile
    third-party page that could trigger writes via /tools/execute.
    Requests without an Origin header (curl, server-to-server) bypass
    this check — they're protected by Bearer auth on the CertMate side.
    """
    if origin is None:
        return
    allowed = settings.cors_origin_list
    if not allowed:
        return  # no allowlist configured = no origin enforcement
    if origin not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"origin '{origin}' is not on the allowlist",
        )


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    # History is capped both in item count (defense against a malicious
    # client trying to balloon LLM context to exhaust upstream tokens or
    # memory) and per-item content size via field_validator below.
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    session_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9._\-]+$")
    admin_token: str | None = Field(default=None, max_length=256)


class ExecuteRequest(BaseModel):
    token: str = Field(..., min_length=8)
    admin_token: str | None = Field(default=None, max_length=256)


def _is_admin(header_val: str | None, body_val: str | None) -> bool:
    """Compare provided admin secret to env-configured token.
    Returns False when admin is disabled (token empty) or no match.
    Accepts either the X-Agent-Admin header or an admin_token body field.
    """
    expected = settings.agent_admin_token
    if not expected:
        return False
    candidate = header_val or body_val or ""
    # constant-time compare to avoid timing oracle
    import hmac
    return hmac.compare_digest(candidate, expected)


def _sse_format(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/chat")
async def chat(
    request: Request,
    req: ChatRequest,
    x_agent_admin: str | None = Header(default=None, alias="X-Agent-Admin"),
    origin: str | None = Header(default=None),
) -> StreamingResponse:
    _check_origin(origin)
    await enforce(_chat_rl, request, "chat")
    key = client_key(request)
    if not await _chat_concurrency.acquire(key):
        raise HTTPException(
            status_code=429,
            detail=f"too many concurrent /chat streams from {key}",
            headers={"Retry-After": "1"},
        )

    is_admin = _is_admin(x_agent_admin, req.admin_token)

    async def stream() -> Any:
        try:
            async for ev in run_turn(
                req.message,
                req.history,
                is_admin=is_admin,
                session_id=req.session_id,
            ):
                # If the client has disconnected mid-stream, stop the
                # tool/LLM loop instead of paying for more upstream
                # tokens that no one will read. starlette gives us a
                # per-request hook we just have to ask for.
                if await request.is_disconnected():
                    return
                yield _sse_format(ev["event"], ev.get("data", {}))
        except asyncio.CancelledError:
            return
        except Exception as e:
            # Avoid leaking the exception message in production; the
            # global handler logged the traceback already with the
            # request id. Surface a short identifier the operator can
            # cross-reference.
            yield _sse_format("error", {
                "message": "server error during stream",
                "kind": type(e).__name__,
            })
            yield _sse_format("done", {})
        finally:
            await _chat_concurrency.release(key)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/tools/execute")
async def execute_confirmed(
    request: Request,
    req: ExecuteRequest,
    origin: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_origin(origin)
    await enforce(_execute_rl, request, "tools/execute")
    pending = await asyncio.to_thread(consume_pending_action, req.token)
    if pending is None:
        raise HTTPException(status_code=404, detail="token unknown, consumed, or expired")

    tool = get_tool(pending["tool_name"])
    if tool is None:
        raise HTTPException(status_code=500, detail="tool no longer registered")

    try:
        async with CertMateClient(
            agent_session_id=pending.get("session_id"),
        ) as c:
            result = await tool.executor(c, dict(pending["args"]))
        audit("tool_execute", "ok", tool_name=tool.name, args=pending["args"])
        return {"ok": True, "tool": tool.name, "result": result}
    except CertMateError as e:
        audit("tool_execute", "error", tool_name=tool.name, args=pending["args"],
              detail=f"http_{e.status}: {e}")
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        audit("tool_execute", "error", tool_name=tool.name, args=pending["args"], detail=str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e
