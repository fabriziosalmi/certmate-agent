"""SSE chat endpoint and confirm/execute endpoint."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..certmate_client import CertMateClient, CertMateError
from ..chat_loop import run_turn
from ..config import settings
from ..db import audit, consume_pending_action
from ..tools import get_tool

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str | None = Field(default=None, max_length=128)
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
    req: ChatRequest,
    x_agent_admin: str | None = Header(default=None, alias="X-Agent-Admin"),
) -> StreamingResponse:
    is_admin = _is_admin(x_agent_admin, req.admin_token)

    async def stream() -> Any:
        try:
            async for ev in run_turn(
                req.message,
                req.history,
                is_admin=is_admin,
                session_id=req.session_id,
            ):
                yield _sse_format(ev["event"], ev.get("data", {}))
        except asyncio.CancelledError:
            return
        except Exception as e:
            yield _sse_format("error", {"message": f"server error: {e}"})
            yield _sse_format("done", {})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/tools/execute")
async def execute_confirmed(req: ExecuteRequest) -> dict[str, Any]:
    pending = await asyncio.to_thread(consume_pending_action, req.token)
    if pending is None:
        raise HTTPException(status_code=404, detail="token unknown, consumed, or expired")

    tool = get_tool(pending["tool_name"])
    if tool is None:
        raise HTTPException(status_code=500, detail="tool no longer registered")

    try:
        async with CertMateClient() as c:
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
