from __future__ import annotations

import httpx
from fastapi import APIRouter

from ..certmate_client import CertMateClient, CertMateError
from ..config import settings
from ..tools import REGISTRY

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    from ..rag.cache import get_cache

    out: dict = {
        "agent": "ok",
        "mode": settings.agent_mode,
        "tools": len(REGISTRY),
        "persist_conversations": settings.agent_persist_conversations,
        "cleanup_interval_s": settings.agent_cleanup_interval_seconds,
        "audit_ttl_days": settings.agent_audit_ttl_days,
        "admin_enabled": bool(settings.agent_admin_token),
        "docs_cache": get_cache().stats(),
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{settings.lmstudio_url.rstrip('/')}/models")
            out["lmstudio"] = {"status": "ok" if r.status_code == 200 else f"http_{r.status_code}"}
    except Exception as e:
        out["lmstudio"] = {"status": "error", "error": str(e)}

    out["fallback"] = (
        {"provider": "openrouter", "model": settings.openrouter_model, "enabled": True}
        if settings.fallback_enabled
        else {"enabled": False}
    )

    if settings.is_docs_only:
        out["certmate"] = {"status": "disabled", "reason": "docs_only mode"}
    else:
        try:
            async with CertMateClient(agent_session_id="health-probe") as cm:
                await cm.system_health()
                out["certmate"] = {"status": "ok"}
        except CertMateError as e:
            out["certmate"] = {"status": "error", "http": e.status, "error": str(e)}
        except Exception as e:
            out["certmate"] = {"status": "error", "error": str(e)}

    # Auth posture advisory (best-effort, no probing): CertMate v2.5.5+
    # supports scoped operator-role tokens with allowed_domains. The agent
    # cannot introspect the active token (no Bearer-accessible /whoami at
    # the time of writing), so we surface a recommendation instead of a
    # check. Operators should treat this as a deployment-time gate.
    if not settings.is_docs_only:
        out["auth_advice"] = {
            "recommended": "role=operator + allowed_domains (CertMate v2.5.5+)",
            "rationale": (
                "Tools the agent invokes are a subset of CertMate's surface "
                "(no user-management, no full-cert-deletion). A scoped token "
                "limits blast radius; CertMate's audit log records the "
                "X-CertMate-Agent-Session header forwarded by this agent."
            ),
        }

    return out


@router.get("/models")
async def models() -> dict:
    """Quick check that the configured chat + embed models are loaded in LM Studio."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{settings.lmstudio_url.rstrip('/')}/models")
            r.raise_for_status()
            ids = [m["id"] for m in r.json().get("data", [])]
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "available": ids,
        "chat_model": settings.lmstudio_chat_model,
        "chat_loaded": settings.lmstudio_chat_model in ids,
        "embed_model": settings.lmstudio_embed_model,
        "embed_loaded": settings.lmstudio_embed_model in ids,
    }
