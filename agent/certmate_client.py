"""Async HTTP client for CertMate REST API.

Maps 1:1 onto CertMate's flask-restx endpoints under /api/.
Methods are grouped by namespace and named to match the tool surface
the LLM will see (e.g. `cert_list` -> `GET /api/certificates/`).

Bearer-token auth: pass CERTMATE_TOKEN via env.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import settings


class CertMateError(RuntimeError):
    def __init__(self, status: int, message: str, body: Any = None) -> None:
        super().__init__(f"CertMate API {status}: {message}")
        self.status = status
        self.body = body


class CertMateClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
        agent_session_id: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.certmate_url).rstrip("/")
        self.token = token or settings.certmate_token
        self.timeout = timeout or settings.certmate_timeout_seconds
        # Optional session_id forwarded to CertMate as a request header so
        # the audit log can attribute writes to a specific agent session,
        # not just to the agent's Bearer identity. CertMate ignores
        # unknown headers, so this is a no-op on older versions.
        self.agent_session_id = agent_session_id
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> CertMateClient:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        # User-Agent identifies the source in CertMate's request logs even
        # when the agent-session header is unrecognized.
        headers["User-Agent"] = "certmate-agent/0.1"
        if self.agent_session_id:
            headers["X-CertMate-Agent-Session"] = self.agent_session_id
        self._client = httpx.AsyncClient(
            base_url=f"{self.base_url}/api",
            headers=headers,
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _req(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        if self._client is None:
            raise CertMateError(0, "CertMateClient must be used as async context manager")
        r = await self._client.request(method, path, params=params, json=json_body)
        if r.status_code >= 400:
            try:
                body = r.json()
                msg = body.get("error") or body.get("message") or r.text[:200]
            except Exception:
                body = r.text
                msg = r.text[:200]
            raise CertMateError(r.status_code, msg, body)
        if r.status_code == 204 or not r.content:
            return None
        ctype = r.headers.get("content-type", "")
        if "application/json" in ctype:
            return r.json()
        return r.content

    # ---- read: health / metrics / diagnostics ----
    async def system_health(self) -> Any:
        return await self._req("GET", "/health")

    async def metrics_info(self) -> Any:
        return await self._req("GET", "/metrics")

    async def diagnostics_snapshot(self) -> Any:
        return await self._req("GET", "/diagnostics/snapshot")

    # ---- read: settings ----
    async def settings_get(self) -> Any:
        return await self._req("GET", "/settings")

    async def dns_providers_info(self) -> Any:
        return await self._req("GET", "/settings/dns-providers")

    # ---- read: certificates ----
    async def cert_list(self) -> Any:
        return await self._req("GET", "/certificates")

    async def cert_get(self, domain: str) -> Any:
        return await self._req("GET", f"/certificates/{domain}")

    async def cert_deployment_status(self, domain: str) -> Any:
        return await self._req("GET", f"/certificates/{domain}/deployment-status")

    async def cert_dns_alias_check(self, domain: str) -> Any:
        return await self._req("GET", f"/certificates/{domain}/dns-alias-check")

    # ---- read: dns accounts ----
    async def dns_accounts_list(self, provider: str | None = None) -> Any:
        if provider:
            return await self._req("GET", f"/dns/{provider}/accounts")
        return await self._req("GET", "/dns/accounts")

    async def dns_account_get(self, provider: str, account_id: str) -> Any:
        return await self._req("GET", f"/dns/{provider}/accounts/{account_id}")

    # ---- read: backups / storage / client-certs ----
    async def backups_list(self) -> Any:
        return await self._req("GET", "/backups")

    async def storage_info(self) -> Any:
        return await self._req("GET", "/storage/info")

    async def client_certs_list(self) -> Any:
        return await self._req("GET", "/client-certs")

    # ---- write: certificates ----
    async def cert_create(self, payload: dict[str, Any]) -> Any:
        return await self._req("POST", "/certificates/create", json_body=payload)

    async def cert_renew(self, domain: str, payload: dict[str, Any] | None = None) -> Any:
        return await self._req("POST", f"/certificates/{domain}/renew", json_body=payload or {})

    async def cert_auto_renew_toggle(self, domain: str, enabled: bool) -> Any:
        return await self._req(
            "POST", f"/certificates/{domain}/auto-renew", json_body={"enabled": enabled}
        )

    async def cert_deploy(self, domain: str) -> Any:
        return await self._req("POST", f"/certificates/{domain}/deploy")

    # ---- write: settings / cache / ca ----
    async def settings_update(self, payload: dict[str, Any]) -> Any:
        return await self._req("PUT", "/settings", json_body=payload)

    async def ca_provider_test(self, payload: dict[str, Any]) -> Any:
        return await self._req("POST", "/settings/test-ca-provider", json_body=payload)

    async def cache_clear(self) -> Any:
        return await self._req("POST", "/cache/clear")

    # ---- write: dns accounts ----
    async def dns_account_add(self, provider: str, payload: dict[str, Any]) -> Any:
        return await self._req("POST", f"/dns/{provider}/accounts", json_body=payload)

    async def dns_account_update(
        self, provider: str, account_id: str, payload: dict[str, Any]
    ) -> Any:
        return await self._req(
            "PUT", f"/dns/{provider}/accounts/{account_id}", json_body=payload
        )

    async def dns_account_delete(self, provider: str, account_id: str) -> Any:
        return await self._req("DELETE", f"/dns/{provider}/accounts/{account_id}")

    # ---- write: backups ----
    async def backup_create(self, payload: dict[str, Any] | None = None) -> Any:
        return await self._req("POST", "/backups/create", json_body=payload or {})

    async def backup_restore(self, backup_type: str, payload: dict[str, Any]) -> Any:
        return await self._req("POST", f"/backups/restore/{backup_type}", json_body=payload)

    async def backup_delete(self, backup_type: str, filename: str) -> Any:
        return await self._req("DELETE", f"/backups/delete/{backup_type}/{filename}")
