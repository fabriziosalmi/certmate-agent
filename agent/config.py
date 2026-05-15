from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    lmstudio_url: str = "http://100.66.12.82:1234/v1"
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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.agent_cors_origins.split(",") if o.strip()]

    @property
    def fallback_enabled(self) -> bool:
        return bool(self.openrouter_api_key)


settings = Settings()
