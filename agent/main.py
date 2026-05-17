"""FastAPI app entry."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import chat as chat_api
from .api import conversations as conversations_api
from .api import health as health_api
from .api.request_context import (
    RequestIdMiddleware,
    install_exception_handler,
    install_logging,
)
from .api.security_headers import SecurityHeadersMiddleware
from .config import settings
from .db import init_db
from .rag.bootstrap import maybe_bootstrap_index
from .scheduler import scheduler_lifespan

logging.basicConfig(
    level=getattr(logging, settings.agent_log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
install_logging()  # add request-id field to every log record
log = logging.getLogger("certmate-agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("certmate-agent %s starting (mode=%s)", __version__, settings.agent_mode)
    log.info("LMStudio: %s (chat=%s, embed=%s)",
             settings.lmstudio_url, settings.lmstudio_chat_model, settings.lmstudio_embed_model)
    if settings.is_docs_only:
        log.info("docs_only mode: CertMate API disabled, write/admin tools hidden")
    else:
        log.info("CertMate: %s", settings.certmate_url)
    if settings.fallback_enabled:
        log.info("Fallback LLM: OpenRouter %s (model=%s)",
                 settings.openrouter_url, settings.openrouter_model)
    # If a bootstrap URL is configured AND the local index is missing,
    # download the published artifact before serving traffic.
    await maybe_bootstrap_index()
    async with scheduler_lifespan():
        yield


app = FastAPI(title="certmate-agent", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
# Security headers (CSP frame-ancestors, XFO, nosniff, referrer, perms).
# Added AFTER CORS so the CORS middleware sees raw requests; the headers
# stamp on the outgoing response either way.
app.add_middleware(SecurityHeadersMiddleware)
# Correlation ID — last in the add order, first in the request path.
# That way request_id is bound before security headers / CORS run, so
# their log lines also carry it.
app.add_middleware(RequestIdMiddleware)

# Global exception handler — turns any uncaught exception into a generic
# 500 referencing the request id, with the full traceback going to the
# server log only.
install_exception_handler(app)

app.include_router(health_api.router)
app.include_router(chat_api.router)
if settings.agent_persist_conversations:
    app.include_router(conversations_api.router)
    log.info("conversation persistence: ENABLED (db=%s)", settings.agent_db_path)

# serve widget statically when present
_widget_dir = Path(__file__).parent.parent / "widget"
if _widget_dir.exists():
    app.mount("/widget", StaticFiles(directory=str(_widget_dir), html=True), name="widget")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "agent.main:app",
        host=settings.agent_host,
        port=settings.agent_port,
        log_level=settings.agent_log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
