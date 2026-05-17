"""Session-keyed conversation persistence.

Only mounted when AGENT_PERSIST_CONVERSATIONS=true (see main.py).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..db import conversation_clear, conversation_load

router = APIRouter()


def _validate_session_id(session_id: str) -> None:
    if not session_id or len(session_id) > 128:
        raise HTTPException(status_code=400, detail="invalid session_id")
    # constrain to a conservative charset to avoid weird routing issues
    if not all(c.isalnum() or c in "-_." for c in session_id):
        raise HTTPException(status_code=400, detail="session_id has invalid characters")


@router.get("/conversations/{session_id}")
async def get_conversation(session_id: str) -> dict:
    _validate_session_id(session_id)
    if not settings.agent_persist_conversations:
        raise HTTPException(
            status_code=503,
            detail="conversation persistence is disabled "
                   "(set AGENT_PERSIST_CONVERSATIONS=true)",
        )
    history = await asyncio.to_thread(conversation_load, session_id)
    return {"session_id": session_id, "messages": history, "count": len(history)}


@router.delete("/conversations/{session_id}")
async def delete_conversation(session_id: str) -> dict:
    _validate_session_id(session_id)
    if not settings.agent_persist_conversations:
        raise HTTPException(
            status_code=503,
            detail="conversation persistence is disabled "
                   "(set AGENT_PERSIST_CONVERSATIONS=true)",
        )
    deleted = await asyncio.to_thread(conversation_clear, session_id)
    return {"session_id": session_id, "deleted": deleted}
