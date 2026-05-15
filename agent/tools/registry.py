"""Tool registry exposed to the LLM.

Two kinds of tools:
  - READ: executed immediately by the chat loop, result fed back to the LLM.
  - WRITE: NOT executed by the loop. The LLM call is intercepted, a pending
    action is saved (with a confirm token + human-readable preview), and
    the UI must POST /tools/execute with the token to actually run it.

Each tool has:
  - name (snake_case, stable)
  - description (LLM-facing)
  - parameters (JSON Schema, OpenAI tool format)
  - kind (read / write_safe / write_destructive)
  - executor (async fn taking (client, args) -> result)
  - summarize(args) -> human string for the confirm UI (write tools only)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..certmate_client import CertMateClient
from ..config import settings
from ..llm.lmstudio import LMStudioClient
from ..rag import get_store


class ToolKind(str, Enum):
    READ = "read"
    WRITE_SAFE = "write_safe"             # mutates but low risk (e.g. cache_clear)
    WRITE_DESTRUCTIVE = "write_destructive"  # high risk (delete, restore, migrate)


Executor = Callable[[CertMateClient, dict[str, Any]], Awaitable[Any]]
Summarizer = Callable[[dict[str, Any]], str]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    kind: ToolKind
    executor: Executor
    summarize: Summarizer | None = None
    aliases: list[str] = field(default_factory=list)
    # True when this tool needs a live CertMate API connection.
    # docs_only mode skips registration of tools where this is True.
    requires_certmate: bool = True

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------- Executors ----------

async def _cert_list(c: CertMateClient, args: dict[str, Any]) -> Any:
    data = await c.cert_list()
    expiring_within = args.get("expiring_within_days")
    if expiring_within is None or not isinstance(data, list):
        return data
    try:
        threshold = int(expiring_within)
    except (TypeError, ValueError):
        return data
    return [
        item for item in data
        if isinstance(item, dict)
        and isinstance(item.get("days_until_expiry"), (int, float))
        and item["days_until_expiry"] <= threshold
    ]


async def _cert_get(c, args):
    return await c.cert_get(args["domain"])


async def _cert_deployment_status(c, args):
    return await c.cert_deployment_status(args["domain"])


async def _cert_dns_alias_check(c, args):
    return await c.cert_dns_alias_check(args["domain"])


async def _system_health(c, _args):
    return await c.system_health()


async def _system_overview(c, _args):
    health = await c.system_health()
    certs = await c.cert_list()
    expiring_soon = []
    if isinstance(certs, list):
        for item in certs:
            if not isinstance(item, dict):
                continue
            days = item.get("days_until_expiry")
            if isinstance(days, (int, float)) and days <= 30:
                expiring_soon.append({
                    "domain": item.get("domain"),
                    "days_until_expiry": days,
                    "status": item.get("status"),
                })
    return {
        "health": health,
        "cert_count": len(certs) if isinstance(certs, list) else None,
        "expiring_within_30d": expiring_soon,
    }


async def _settings_get(c, _args):
    return await c.settings_get()


async def _dns_providers_info(c, _args):
    return await c.dns_providers_info()


async def _dns_accounts_list(c, args):
    return await c.dns_accounts_list(args.get("provider"))


async def _dns_account_get(c, args):
    return await c.dns_account_get(args["provider"], args["account_id"])


async def _backups_list(c, _args):
    return await c.backups_list()


async def _storage_info(c, _args):
    return await c.storage_info()


async def _client_certs_list(c, _args):
    return await c.client_certs_list()


async def _docs_search(_c, args):
    """RAG over CertMate docs. Ignores the CertMateClient arg.
    Cached by normalized query + k; cache invalidated on /reindex.
    """
    from ..rag.cache import get_cache

    query = (args.get("query") or "").strip()
    k = max(1, min(int(args.get("k", 3)), 8))
    if not query:
        return {"error": "query is required"}

    store = get_store()
    if not store.ready:
        return {
            "ready": False,
            "hits": [],
            "note": "Docs index not built. Run: python -m agent.rag.indexer",
        }

    cache = get_cache()
    cached = cache.get(query, k)
    if cached is not None:
        return {"ready": True, "hits": cached, "cached": True}

    async with LMStudioClient() as llm:
        vectors = await llm.embed([query])
    hits = store.search(vectors[0], k=k)
    payload = [
        {
            "title": h.title,
            "source": h.source,
            "url": h.url,
            "score": round(h.score, 3),
            "text": h.text,
        }
        for h in hits
    ]
    cache.put(query, k, payload)
    return {"ready": True, "hits": payload, "cached": False}


# write
async def _cert_create(c, args):
    return await c.cert_create(args)


async def _cert_renew(c, args):
    domain = args.pop("domain")
    return await c.cert_renew(domain, args)


async def _cert_auto_renew_toggle(c, args):
    return await c.cert_auto_renew_toggle(args["domain"], bool(args["enabled"]))


async def _cert_deploy(c, args):
    return await c.cert_deploy(args["domain"])


async def _cache_clear(c, _args):
    return await c.cache_clear()


async def _backup_create(c, args):
    return await c.backup_create(args)


async def _backup_delete(c, args):
    return await c.backup_delete(args["backup_type"], args["filename"])


async def _dns_account_add(c, args):
    provider = args.pop("provider")
    return await c.dns_account_add(provider, args)


async def _dns_account_delete(c, args):
    return await c.dns_account_delete(args["provider"], args["account_id"])


# ---------- Summarizers (write tools) ----------

def _s_cert_create(a):
    domain = a.get("domain") or "?"
    ca = a.get("ca_provider") or "letsencrypt"
    return f"Create certificate for `{domain}` using CA `{ca}`."


def _s_cert_renew(a):
    domain = a.get("domain") or "?"
    return f"Renew certificate `{domain}`."


def _s_cert_auto_renew(a):
    state = "enable" if a.get("enabled") else "disable"
    return f"{state.capitalize()} auto-renew for `{a.get('domain')}`."


def _s_cert_deploy(a):
    return f"Run deployment hook for `{a.get('domain')}`."


def _s_cache_clear(_a):
    return "Clear server-side cache."


def _s_backup_create(_a):
    return "Create a new full backup."


def _s_backup_delete(a):
    return f"DELETE backup `{a.get('backup_type')}/{a.get('filename')}` (irreversible)."


def _s_dns_account_add(a):
    return f"Add DNS account `{a.get('account_id') or '?'}` for provider `{a.get('provider')}`."


def _s_dns_account_delete(a):
    return f"DELETE DNS account `{a.get('provider')}/{a.get('account_id')}` (irreversible)."


# ---------- Registry ----------

def _build_registry() -> dict[str, Tool]:
    domain_param = {
        "type": "object",
        "properties": {"domain": {"type": "string", "description": "Fully-qualified domain name"}},
        "required": ["domain"],
    }
    no_params = {"type": "object", "properties": {}, "additionalProperties": False}

    tools: list[Tool] = [
        # ----- read -----
        Tool(
            name="system_overview",
            description=(
                "High-level snapshot of the CertMate instance: health, total cert count, "
                "and certs expiring within 30 days. Call this when the user asks 'what's "
                "going on', 'status', or starts a session without context."
            ),
            parameters=no_params,
            kind=ToolKind.READ,
            executor=_system_overview,
        ),
        Tool(
            name="system_health",
            description="Health check of the CertMate service (settings, scheduler).",
            parameters=no_params,
            kind=ToolKind.READ,
            executor=_system_health,
        ),
        Tool(
            name="cert_list",
            description=(
                "List managed certificates. Optionally filter to those expiring within "
                "`expiring_within_days` days."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "expiring_within_days": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "If set, only return certs whose days_until_expiry <= this.",
                    }
                },
            },
            kind=ToolKind.READ,
            executor=_cert_list,
        ),
        Tool(
            name="cert_get",
            description="Full details of a single certificate by domain.",
            parameters=domain_param,
            kind=ToolKind.READ,
            executor=_cert_get,
        ),
        Tool(
            name="cert_deployment_status",
            description="Deployment status (deploy hook history) for a certificate.",
            parameters=domain_param,
            kind=ToolKind.READ,
            executor=_cert_deployment_status,
        ),
        Tool(
            name="cert_dns_alias_check",
            description="Verify DNS CNAME alias delegation for a certificate's ACME validation.",
            parameters=domain_param,
            kind=ToolKind.READ,
            executor=_cert_dns_alias_check,
        ),
        Tool(
            name="settings_get",
            description="Current CertMate settings (sensitive fields are redacted server-side).",
            parameters=no_params,
            kind=ToolKind.READ,
            executor=_settings_get,
        ),
        Tool(
            name="dns_providers_info",
            description="List of supported DNS providers and which are currently configured.",
            parameters=no_params,
            kind=ToolKind.READ,
            executor=_dns_providers_info,
        ),
        Tool(
            name="dns_accounts_list",
            description="List configured DNS provider accounts. Optionally filter by provider.",
            parameters={
                "type": "object",
                "properties": {"provider": {"type": "string"}},
            },
            kind=ToolKind.READ,
            executor=_dns_accounts_list,
        ),
        Tool(
            name="dns_account_get",
            description="Get a single DNS account by provider + id (credentials redacted).",
            parameters={
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                    "account_id": {"type": "string"},
                },
                "required": ["provider", "account_id"],
            },
            kind=ToolKind.READ,
            executor=_dns_account_get,
        ),
        Tool(
            name="backups_list",
            description="List available backups.",
            parameters=no_params,
            kind=ToolKind.READ,
            executor=_backups_list,
        ),
        Tool(
            name="storage_info",
            description="Active storage backend info (local fs, S3, etc.).",
            parameters=no_params,
            kind=ToolKind.READ,
            executor=_storage_info,
        ),
        Tool(
            name="client_certs_list",
            description="List client (mTLS) certificates issued by this instance.",
            parameters=no_params,
            kind=ToolKind.READ,
            executor=_client_certs_list,
        ),
        Tool(
            name="docs_search",
            description=(
                "Retrieve relevant excerpts from the CertMate documentation. "
                "USE THIS for knowledge questions about how CertMate works, what a "
                "feature does, ACME / DNS-01 / wildcard concepts, DNS provider setup, "
                "deploy hooks, backup format, API parameters. Do NOT use for live "
                "instance state — call cert_list / cert_get / system_overview instead."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language question. Be specific.",
                    },
                    "k": {
                        "type": "integer",
                        "minimum": 1, "maximum": 8, "default": 3,
                        "description": "Number of excerpts to return.",
                    },
                },
                "required": ["query"],
            },
            kind=ToolKind.READ,
            executor=_docs_search,
            requires_certmate=False,
        ),

        # ----- write_safe -----
        Tool(
            name="cache_clear",
            description="Clear CertMate's server-side cache. Safe operation.",
            parameters=no_params,
            kind=ToolKind.WRITE_SAFE,
            executor=_cache_clear,
            summarize=_s_cache_clear,
        ),
        Tool(
            name="cert_renew",
            description=(
                "Renew a certificate now (skips the normal 30-day window check). "
                "Use only when the user explicitly asks to force a renew."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["domain"],
            },
            kind=ToolKind.WRITE_SAFE,
            executor=_cert_renew,
            summarize=_s_cert_renew,
        ),
        Tool(
            name="cert_auto_renew_toggle",
            description="Enable or disable auto-renewal for one certificate.",
            parameters={
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "enabled": {"type": "boolean"},
                },
                "required": ["domain", "enabled"],
            },
            kind=ToolKind.WRITE_SAFE,
            executor=_cert_auto_renew_toggle,
            summarize=_s_cert_auto_renew,
        ),
        Tool(
            name="cert_deploy",
            description="Run the deploy hook for a certificate (reload nginx, push to S3, etc.).",
            parameters=domain_param,
            kind=ToolKind.WRITE_SAFE,
            executor=_cert_deploy,
            summarize=_s_cert_deploy,
        ),
        Tool(
            name="backup_create",
            description="Create a new full backup of settings + certificates.",
            parameters={
                "type": "object",
                "properties": {"include_certificates": {"type": "boolean", "default": True}},
            },
            kind=ToolKind.WRITE_SAFE,
            executor=_backup_create,
            summarize=_s_backup_create,
        ),

        # ----- write requiring careful inputs -----
        Tool(
            name="cert_create",
            description=(
                "Request a new certificate. Required: domain, dns_provider. Optional: "
                "ca_provider (default letsencrypt), wildcard, dns_account_id."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "dns_provider": {"type": "string"},
                    "ca_provider": {"type": "string"},
                    "wildcard": {"type": "boolean"},
                    "dns_account_id": {"type": "string"},
                },
                "required": ["domain", "dns_provider"],
            },
            kind=ToolKind.WRITE_SAFE,
            executor=_cert_create,
            summarize=_s_cert_create,
        ),
        Tool(
            name="dns_account_add",
            description=(
                "Add a DNS provider account. Credentials object shape depends on provider; "
                "if unsure, call dns_providers_info first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                    "account_id": {"type": "string"},
                    "credentials": {"type": "object"},
                },
                "required": ["provider", "account_id", "credentials"],
            },
            kind=ToolKind.WRITE_SAFE,
            executor=_dns_account_add,
            summarize=_s_dns_account_add,
        ),

        # ----- write_destructive -----
        Tool(
            name="backup_delete",
            description="Permanently delete a backup file. Irreversible.",
            parameters={
                "type": "object",
                "properties": {
                    "backup_type": {"type": "string", "enum": ["settings", "certificates", "full"]},
                    "filename": {"type": "string"},
                },
                "required": ["backup_type", "filename"],
            },
            kind=ToolKind.WRITE_DESTRUCTIVE,
            executor=_backup_delete,
            summarize=_s_backup_delete,
        ),
        Tool(
            name="dns_account_delete",
            description="Delete a DNS provider account. Irreversible.",
            parameters={
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                    "account_id": {"type": "string"},
                },
                "required": ["provider", "account_id"],
            },
            kind=ToolKind.WRITE_DESTRUCTIVE,
            executor=_dns_account_delete,
            summarize=_s_dns_account_delete,
        ),
    ]
    if settings.is_docs_only:
        tools = [t for t in tools if not t.requires_certmate]
    return {t.name: t for t in tools}


REGISTRY: dict[str, Tool] = _build_registry()


def get_tool(name: str) -> Tool | None:
    return REGISTRY.get(name)


def openai_tool_schemas() -> list[dict[str, Any]]:
    return [t.to_openai_schema() for t in REGISTRY.values()]
