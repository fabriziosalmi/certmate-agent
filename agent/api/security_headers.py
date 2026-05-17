"""Security response headers middleware.

Adds a small, consistent set of headers to every response:

- ``Content-Security-Policy: frame-ancestors`` — the widget is meant to
  be embedded in the CertMate dashboard and possibly the public landing
  on agent.certmate.org. We allow exactly those origins (taken from
  ``AGENT_CORS_ORIGINS``) and nothing else. Defense against click-jacking
  on /tools/execute confirms loaded inside a hostile third-party iframe.
- ``X-Frame-Options`` — legacy companion to frame-ancestors for browsers
  that don't honor CSP (very few left, but cheap). When the allowlist is
  empty we lock to ``DENY``; when it has entries we use ``SAMEORIGIN``
  (X-Frame-Options can't express a multi-origin allowlist, so
  ``frame-ancestors`` does the real work and XFO is just a floor).
- ``X-Content-Type-Options: nosniff`` — prevent MIME-sniffing on the
  static widget JS/CSS and on API JSON.
- ``Referrer-Policy: strict-origin-when-cross-origin`` — same posture
  CertMate already uses (visible in its 405 response headers in audit).
- ``Permissions-Policy`` — disable powerful features the agent never
  needs (camera, microphone, geolocation, payment, USB).

Coherent with the security posture established on CertMate
(Scorecard 8/10, SECURITY.md, audit log).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..config import settings


def _frame_ancestors() -> str:
    """Build the CSP frame-ancestors value from the CORS allowlist.

    Empty allowlist => 'none' (no embedding anywhere). Non-empty list =>
    'self' + each configured origin so the test page and trusted hosts
    can iframe / embed the widget UI.
    """
    origins = [o for o in settings.cors_origin_list if o]
    if not origins:
        return "'none'"
    return "'self' " + " ".join(origins)


_BASE_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    ),
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Stamp a small set of defensive headers on every response."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response: Response = await call_next(request)
        for k, v in _BASE_HEADERS.items():
            response.headers.setdefault(k, v)
        # frame-ancestors derived per-request from settings so a hot config
        # reload (unlikely, but) doesn't require restart to take effect.
        ancestors = _frame_ancestors()
        response.headers.setdefault(
            "Content-Security-Policy", f"frame-ancestors {ancestors}",
        )
        # XFO companion: DENY when no allowlist is set, SAMEORIGIN otherwise.
        # XFO can't express a multi-origin allowlist; the real enforcement
        # happens via CSP. This is a floor for legacy clients.
        xfo = "DENY" if ancestors == "'none'" else "SAMEORIGIN"
        response.headers.setdefault("X-Frame-Options", xfo)
        return response
