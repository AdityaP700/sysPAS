from typing import List, Optional
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration settings for the RunbookMind compiler app and Splunk MCP connection."""
    model_config = SettingsConfigDict(
        env_prefix="RUNBOOKMIND_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",          # silently ignore SPLUNK_HOST etc. that lack the prefix
        populate_by_name=True,   # allows OPENROUTER_API_KEY (no prefix) to be read
    )

    # MCP server settings
    mcp_transport: str = "stdio"  # "stdio" or "sse"
    mcp_command: str = "python"
    mcp_args: List[str] = ["-m", "splunk_mcp_server"]
    mcp_sse_url: str = "https://localhost:8089/services/mcp"
    mcp_token: Optional[str] = None          # Bearer token for SSE transport (RUNBOOKMIND_MCP_TOKEN)
    mcp_timeout: float = 10.0
    enable_mcp: bool = True
    schema_cache_ttl: float = 300.0
    mcp_tool_get_indexes: str = "splunk_get_indexes"
    mcp_tool_get_fields: str = "splunk_get_fields"
    storage_enabled: bool = True
    sqlite_db_path: str = "runbookmind.db"
    auth_enabled: bool = False
    default_admin_api_key: Optional[str] = None
    allowed_webhook_domains: List[str] = []
    query_timeout_seconds: int = 30
    allow_private_webhooks: bool = False
    vault_enabled: bool = False
    vault_master_key: Optional[str] = None
    secret_cache_ttl_seconds: int = 300

    # -------------------------------------------------------------------------
    # LLM provider selector
    # -------------------------------------------------------------------------
    # RUNBOOKMIND_LLM_PROVIDER=openrouter  → OpenRouter first, Gemini fallback
    # RUNBOOKMIND_LLM_PROVIDER=gemini      → Gemini only (no OpenRouter calls)
    llm_provider: str = "openrouter"

    # -------------------------------------------------------------------------
    # Gemini LLM settings (pluggable LLM layer — fallback / standalone)
    # -------------------------------------------------------------------------
    gemini_api_key: Optional[str] = None   # RUNBOOKMIND_GEMINI_API_KEY
    gemini_model: str = "gemini-2.5-flash"  # override via env
    gemini_rpm_cap: int = 8                 # max requests/min (free tier ≤ 10)
    gemini_cache_ttl: int = 3600            # result cache TTL in seconds (1 h)

    # -------------------------------------------------------------------------
    # OpenRouter LLM settings (primary provider — OpenAI-compatible endpoint)
    # -------------------------------------------------------------------------
    # Key is read from OPENROUTER_API_KEY *or* RUNBOOKMIND_OPENROUTER_API_KEY.
    openrouter_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENROUTER_API_KEY",           # bare env var (preferred)
            "RUNBOOKMIND_OPENROUTER_API_KEY",  # prefixed fallback
        ),
    )

    # Model to use — swap instantly without touching code.
    # Examples:
    #   anthropic/claude-sonnet-4
    #   openai/gpt-4o-mini
    #   google/gemini-2.5-flash
    #   qwen/qwen3-32b
    openrouter_model: str = "google/gemini-2.5-flash"

    # Resource conservation levers
    openrouter_rpm_cap: int = 20           # max requests/min (generous free tier)
    openrouter_max_tokens: int = 512       # SPL queries rarely exceed this
    openrouter_cache_ttl: int = 3600       # result cache TTL in seconds (1 h)

    # -------------------------------------------------------------------------
    # Claude LLM settings (official Anthropic API)
    # -------------------------------------------------------------------------
    claude_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "ANTHROPIC_API_KEY",
            "RUNBOOKMIND_CLAUDE_API_KEY",
        ),
    )
    claude_model: str = "claude-3-5-haiku-latest"
    claude_rpm_cap: int = 15
    claude_max_tokens: int = 512
    claude_cache_ttl: int = 3600


settings = Settings()
