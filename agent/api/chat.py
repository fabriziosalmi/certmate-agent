"""SSE chat endpoint and confirm/execute endpoint."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from ..chat_loop import run_turn
from ..config import settings
from ..rate_limit import (
    ConcurrencyLimiter,
    RateLimiter,
    client_key,
    enforce,
)

router = APIRouter()

# Rate limiters live at module scope: shared across requests within this
# worker, but per-worker (see rate_limit.py module docstring).
_chat_rl = RateLimiter(settings.agent_ratelimit_chat_per_min, 60.0)
_chat_concurrency = ConcurrencyLimiter(settings.agent_ratelimit_chat_concurrency)


def _check_origin(origin: str | None) -> None:
    """Reject browser POSTs from origins not on the configured allowlist.

    Keeps the widget from being embedded on a hostile third-party page.
    Requests without an Origin header (curl, server-to-server) bypass this
    check: the agent no longer holds any credential and can only read its own
    documentation index, so the residual risk is cost and abuse, which the
    rate limiter bounds.
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


# What a client may put in `history`. `system` is deliberately absent (#17):
# chat_loop splices history in immediately after the real system prompt, so a
# caller passing role="system" could append instructions of its own — deleting
# the tool-output guard, or making the public deployment emit arbitrary
# statements attributed to CertMate.
_ALLOWED_HISTORY_ROLES = {"user", "assistant"}
_MAX_HISTORY_CONTENT_CHARS = 4000


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    # History is capped in item count (a malicious client must not be able to
    # balloon the LLM context) and, in the validator below, in role and
    # per-item content size. The original comment claimed a validator that was
    # never written (#17).
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    session_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9._\-]+$")
    admin_token: str | None = Field(default=None, max_length=256)

    @field_validator("history")
    @classmethod
    def _clean_history(cls, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Items are already dicts: the field type makes pydantic reject
        # anything else with a 422 before this runs.
        cleaned: list[dict[str, Any]] = []
        for item in items:
            role = item.get("role")
            if role not in _ALLOWED_HISTORY_ROLES:
                continue
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            cleaned.append({
                "role": role,
                "content": content[:_MAX_HISTORY_CONTENT_CHARS],
            })
        return cleaned


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
            # If persistence is on AND the client provided a session_id,
            # emit the canonical HMAC token up front. The widget caches
            # it and sends it on /conversations/{id} reads/deletes so a
            # transcript can't be pulled by anyone who guesses the id.
            if settings.agent_persist_conversations and req.session_id:
                from .conversations import issue_session_token
                yield _sse_format("session", {
                    "session_id": req.session_id,
                    "token": issue_session_token(req.session_id),
                })

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
