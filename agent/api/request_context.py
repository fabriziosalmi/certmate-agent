"""Request correlation ID middleware + global exception handler.

Two cross-cutting concerns the agent needs to be operable in production:

1. **Correlation ID.** Every incoming request gets an ``X-Request-Id``
   either passed through from the client (load balancer, edge) or freshly
   minted server-side. The id is exposed:
     - back to the client via the same response header
     - to log records via a ContextVar pulled in by the log filter so
       every log line emitted during a request is grep-able by id
     - to downstream calls (CertMate, LM Studio, OpenRouter) via the
       same header, so audit logs across services line up
   Closes audit-trail gap when a single user turn fans out to slash →
   tool → LLM → CertMate.

2. **Global exception handler.** Anything not explicitly raised as an
   HTTPException becomes a 500 with a generic body referencing the
   request id. The full traceback goes to the server log only — never
   to the client. Defense against information disclosure.
"""

from __future__ import annotations

import contextvars
import logging
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_request_id", default="-"
)


def current_request_id() -> str:
    """Return the active request id, or '-' if outside a request scope."""
    return _request_id.get()


class _RequestIdLogFilter(logging.Filter):
    """Stamp the active request id onto every LogRecord so log lines are
    grep-able by request id without each callsite having to plumb it in."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


def install_logging() -> None:
    """Apply the request-id filter to the root logger and switch the
    formatter so the id appears in every line.

    Idempotent — safe to call multiple times (filter dedup via type check).
    """
    root = logging.getLogger()
    has_filter = any(isinstance(f, _RequestIdLogFilter) for f in root.filters)
    if not has_filter:
        root.addFilter(_RequestIdLogFilter())
    fmt = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
    for h in root.handlers:
        h.setFormatter(logging.Formatter(fmt))


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind a request id to the per-request ContextVar + response header."""

    HEADER = "X-Request-Id"

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        rid = request.headers.get(self.HEADER)
        if not rid or len(rid) > 64 or not _is_safe_id(rid):
            # Either absent or untrusted shape; mint a fresh one.
            rid = secrets.token_urlsafe(12)
        token = _request_id.set(rid)
        try:
            response = await call_next(request)
        finally:
            _request_id.reset(token)
        response.headers[self.HEADER] = rid
        return response


def _is_safe_id(s: str) -> bool:
    # urlsafe alphabet + dash/underscore; rejects whitespace, controls,
    # quotes, anything that could break log lines or response headers.
    return all(c.isalnum() or c in "-_" for c in s)


def install_exception_handler(app: FastAPI) -> None:
    """Catch any uncaught exception, log with full traceback, return a
    generic 500 referencing the request id. The client never sees stack."""

    log = logging.getLogger("certmate-agent.exception")

    @app.exception_handler(Exception)
    async def _handler(request: Request, exc: Exception):  # noqa: ARG001
        rid = current_request_id()
        log.exception("unhandled exception (request_id=%s): %s", rid, exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "request_id": rid,
                "detail": (
                    "An unexpected error occurred. The incident has been "
                    "logged with the request id above; share it with the "
                    "operator to investigate."
                ),
            },
        )
