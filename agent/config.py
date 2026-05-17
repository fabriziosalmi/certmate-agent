from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AgentMode = Literal["full", "docs_only"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Operating mode:
    #   - "full"       : everything; sidecar to a real CertMate instance.
    #                    Live tools + write commands with confirm + admin.
    #   - "docs_only"  : public-facing chat over the CertMate documentation.
    #                    No CertMate API connection, only docs_search + help.
    #                    Used for agent.certmate.org-style deployments.
    agent_mode: AgentMode = "full"

    # Defaults to the LM Studio default port on the same host. Override via
    # env (LMSTUDIO_URL) — e.g. a Tailscale IP for a remote workstation, or
    # a Cloudflare Workers AI endpoint in production.
    lmstudio_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_chat_model: str = "google/gemma-4-e2b"
    lmstudio_embed_model: str = "text-embedding-embeddinggemma-300m"
    lmstudio_api_key: str = "lm-studio"
    lmstudio_timeout_seconds: float = 120.0

    certmate_url: str = "http://localhost:8000"
    certmate_token: str = ""
    certmate_timeout_seconds: float = 30.0

    agent_host: str = "127.0.0.1"
    agent_port: int = 8765
    agent_db_path: str = "./agent.db"
    agent_index_path: str = "./docs_index/index.pkl"
    # Optional: when set, the agent downloads the index from this URL on
    # boot if the local file is missing. Used by the docs_only deployment
    # on Fly.io to pick up the GH Actions release artifact at cold start.
    agent_index_bootstrap_url: str = ""
    agent_cors_origins: str = "http://localhost:8000"

    agent_log_level: str = "INFO"
    agent_confirm_token_ttl_seconds: int = 300
    agent_max_tool_iterations: int = 6
    agent_max_tokens: int = 2048
    agent_temperature: float = 0.2

    # Empty disables admin-only commands like /reindex.
    agent_admin_token: str = ""

    # When true, the server persists conversations to sqlite keyed by
    # session_id and loads history server-side. When false, the client
    # passes the full history each turn (stateless).
    agent_persist_conversations: bool = False
    agent_conversation_ttl_days: int = 30

    # Background cleanup interval (pending tokens + conversations beyond TTL).
    # Set to 0 to disable the scheduler entirely.
    agent_cleanup_interval_seconds: int = 3600

    # --- Optional fallback LLM (OpenRouter, OpenAI-compatible). ---
    # Embeddings always stay on the primary LM Studio; the fallback only
    # serves chat completions when the primary is unreachable / errors out.
    # Empty api_key disables fallback entirely.
    openrouter_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""
    openrouter_model: str = "meta-llama/llama-3.3-8b-instruct:free"
    openrouter_timeout_seconds: float = 60.0
    # Optional attribution headers (OpenRouter recommends but does not require).
    openrouter_referer: str = "https://github.com/fabriziosalmi/certmate"
    openrouter_title: str = "certmate-agent"
    # Circuit breaker: trip primary after this many consecutive failures,
    # stay tripped for `cooldown` seconds before retrying.
    llm_primary_failure_threshold: int = 3
    llm_primary_cooldown_seconds: int = 60

    # docs_search query cache.
    agent_docs_cache_size: int = 128
    agent_docs_cache_ttl_seconds: int = 300

    # Audit log retention (separate from conversations).
    agent_audit_ttl_days: int = 90

    # --- Rate limiting (per remote IP, in-memory token bucket). ---
    # Set any limit to 0 to disable that endpoint's rate limit.
    # docs_only deployments should keep these tight (public traffic);
    # full / single-tenant deployments can raise them or disable.
    agent_ratelimit_chat_per_min: int = 30
    agent_ratelimit_execute_per_min: int = 30
    # Maximum concurrent in-flight /chat streams per remote IP.
    # SSE streams are long-lived; without this a single client can pin
    # workers by opening many at once.
    agent_ratelimit_chat_concurrency: int = 4

    # --- Tool output sanitization (defense vs OWASP LLM01 prompt injection). ---
    # Hard cap on the size of a tool_result fed back into the LLM as
    # role=tool content. Tool outputs > this many chars are truncated.
    agent_tool_output_max_chars: int = 4000

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.agent_cors_origins.split(",") if o.strip()]

    @property
    def fallback_enabled(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def is_docs_only(self) -> bool:
        return self.agent_mode == "docs_only"


settings = Settings()
