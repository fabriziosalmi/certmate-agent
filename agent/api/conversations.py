"""Session-keyed conversation persistence.

Only mounted when AGENT_PERSIST_CONVERSATIONS=true (see main.py).

Auth model: each session_id is paired with a server-issued session token
(HMAC-SHA256 of the id, keyed by AGENT_SESSION_SECRET). The widget gets
the token back the first time it sends an unknown session_id to /chat
and stores it next to the id in localStorage; subsequent reads/deletes
of /conversations/{id} must echo the token via the X-Session-Token
header.

Without this, anyone who guesses or sniffs a session id could pull
another user's transcript on a public deployment.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets

from fastapi import APIRouter, Header, HTTPException

from ..config import settings
from ..db import conversation_clear, conversation_load

router = APIRouter()
log = logging.getLogger(__name__)

# Cache the effective secret so a server restart preserves token validity
# unless the operator explicitly rotates AGENT_SESSION_SECRET. If the env
# var is empty we mint a process-lifetime random secret — tokens issued
# in one process won't validate in another, which is a deliberate fail-
# closed posture for multi-worker deployments that lack a shared secret.
_secret: str = settings.agent_session_secret
if not _secret:
    _secret = secrets.token_urlsafe(48)
    log.warning(
        "AGENT_SESSION_SECRET not set — using a process-local random secret. "
        "Session tokens will not survive a restart or work across workers. "
        "Set AGENT_SESSION_SECRET to a long random value for production."
    )


def issue_session_token(session_id: str) -> str:
    """Return the canonical token for a session_id. Deterministic given
    the secret, so the same id always maps to the same token (the widget
    can therefore cache it locally and resend it on every request)."""
    return hmac.new(
        _secret.encode("utf-8"),
        session_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_session_token(session_id: str, token: str | None) -> bool:
    """Constant-time compare of the supplied token against the expected
    one. Empty token always fails."""
    if not token:
        return False
    expected = issue_session_token(session_id)
    return hmac.compare_digest(expected, token)


def _validate_session_id(session_id: str) -> None:
    if not session_id or len(session_id) > 128:
        raise HTTPException(status_code=400, detail="invalid session_id")
    # constrain to a conservative charset to avoid weird routing issues
    if not all(c.isalnum() or c in "-_." for c in session_id):
        raise HTTPException(status_code=400, detail="session_id has invalid characters")


def _require_owner(session_id: str, token: str | None) -> None:
    """Reject the call unless the supplied token is the right HMAC for the id."""
    if not verify_session_token(session_id, token):
        # Same 404 for missing/invalid token + missing session, so an
        # attacker probing for valid ids can't distinguish "this id
        # exists but you can't read it" from "this id doesn't exist".
        raise HTTPException(status_code=404, detail="session not found")


@router.get("/conversations/{session_id}")
async def get_conversation(
    session_id: str,
    x_session_token: str | None = Header(default=None, alias="X-Session-Token"),
) -> dict:
    _validate_session_id(session_id)
    if not settings.agent_persist_conversations:
        raise HTTPException(
            status_code=503,
            detail="conversation persistence is disabled "
                   "(set AGENT_PERSIST_CONVERSATIONS=true)",
        )
    _require_owner(session_id, x_session_token)
    history = await asyncio.to_thread(conversation_load, session_id)
    return {"session_id": session_id, "messages": history, "count": len(history)}


@router.delete("/conversations/{session_id}")
async def delete_conversation(
    session_id: str,
    x_session_token: str | None = Header(default=None, alias="X-Session-Token"),
) -> dict:
    _validate_session_id(session_id)
    if not settings.agent_persist_conversations:
        raise HTTPException(
            status_code=503,
            detail="conversation persistence is disabled "
                   "(set AGENT_PERSIST_CONVERSATIONS=true)",
        )
    _require_owner(session_id, x_session_token)
    deleted = await asyncio.to_thread(conversation_clear, session_id)
    return {"session_id": session_id, "deleted": deleted}
