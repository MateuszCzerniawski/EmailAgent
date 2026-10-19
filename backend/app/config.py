"""
Application configuration.

Loads settings from config/config.yaml located in the backend root directory.
All secrets (API keys, OAuth tokens) are NOT stored here — they live in the OS
keyring and are retrieved at runtime by the relevant services.

Usage:
    from app.config import load_config, load_agent_config, AppConfig
    config = load_config()              # reads config/config.yaml
    agent_cfg = load_agent_config()     # reads config/agent_config.yaml
    print(config.llm.model)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Config file paths (all relative to backend/)
# ---------------------------------------------------------------------------

_BACKEND_ROOT = Path(__file__).parent.parent

CONFIG_PATH = _BACKEND_ROOT / "config" / "config.yaml"
LOGGING_CONFIG_PATH = _BACKEND_ROOT / "config" / "logging_config.yaml"
AGENT_CONFIG_PATH = _BACKEND_ROOT / "config" / "agent_config.yaml"


# ---------------------------------------------------------------------------
# Sub-models (one per top-level YAML key)
# ---------------------------------------------------------------------------


class GmailConfig(BaseModel):
    max_results: int = Field(default=50, description="Max emails per /emails request")
    query: str = Field(default="in:inbox OR in:sent", description="Gmail search filter")


class LLMConfig(BaseModel):
    provider: str = Field(default="openai")
    openai_model: str = Field(default="gpt-4o-mini")
    ollama_model: str = Field(default="qwen2.5:7b")
    ollama_base_url: str = Field(default="http://localhost:11434")
    temperature: float = Field(
        default=0.0,
        description="Keep at 0.0 for deterministic output — maximises cache hit rate",
    )
    max_tokens: int = Field(default=512)

    @property
    def active_model(self) -> str:
        return self.ollama_model if self.provider == "ollama" else self.openai_model


class CacheConfig(BaseModel):
    enabled: bool = Field(default=True)
    db_path: str = Field(
        default=".cache/llm_cache.db",
        description="Path to SQLite cache file (email-ID keyed), relative to backend/",
    )


class ServerConfig(BaseModel):
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)


class KeyringConfig(BaseModel):
    service_name: str = Field(default="email-email-agent")
    openai_key_name: str = Field(default="openai_api_key")
    gmail_token_key_name: str = Field(default="gmail_refresh_token")
    gmail_client_id_key_name: str = Field(default="gmail_client_id")
    gmail_client_secret_key_name: str = Field(default="gmail_client_secret")


class RagConfig(BaseModel):
    top_k: int = Field(
        default=5,
        description="Number of similar email chunks to retrieve from the vector store per analysis",
    )
    min_score: float = Field(
        default=0.3,
        description="Minimum similarity score (0–1) for a chunk to be included as RAG context. Chunks below this threshold are discarded.",
    )


# ---------------------------------------------------------------------------
# Root config model
# ---------------------------------------------------------------------------


class AppConfig(BaseModel):
    """Complete application configuration loaded from config/config.yaml."""

    email_categories: list[str] = Field(
        default_factory=lambda: [
            "work", "finance", "newsletter", "social", "promotions",
            "travel", "health", "legal", "spam", "other",
        ]
    )
    gmail: GmailConfig = Field(default_factory=GmailConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    keyring: KeyringConfig = Field(default_factory=KeyringConfig)
    rag: RagConfig = Field(default_factory=RagConfig)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Database configuration (env-var backed)
# ---------------------------------------------------------------------------


class DatabaseConfig(BaseSettings):
    """
    Database layer configuration loaded from environment variables / .env file.

    Kept separate from AppConfig (YAML-backed) to follow the plan's convention
    of using env vars for database credentials and paths.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="sqlite+aiosqlite:///./database/emailagent.db",
        validation_alias="DATABASE_URL",
        description="SQLAlchemy async DSN — sqlite+aiosqlite for dev, postgresql+asyncpg for prod",
    )
    chroma_path: str = Field(
        default="./database/.chromadb",
        validation_alias="CHROMA_PATH",
        description="Filesystem path where ChromaDB stores its data",
    )
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        validation_alias="EMBEDDING_MODEL",
        description="Embedding model spec: 'all-MiniLM-L6-v2' or 'openai:<model>'",
    )
    chunk_size: int = Field(
        default=400,
        validation_alias="CHUNK_SIZE",
        description="Token count per chunk (sliding window)",
    )
    chunk_overlap: int = Field(
        default=80,
        validation_alias="CHUNK_OVERLAP",
        description="Token overlap between consecutive chunks",
    )


def load_database_config() -> DatabaseConfig:
    """Load database configuration from environment variables / .env file."""
    return DatabaseConfig()


def load_config(path: Path | None = None) -> AppConfig:
    """
    Parse config/config.yaml and return a validated AppConfig instance.

    Args:
        path: Override the default config path (useful in tests).
    """
    config_path = path or CONFIG_PATH

    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}\n"
            "Ensure config/config.yaml exists in the backend/ directory."
        )

    with config_path.open(encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    return AppConfig(**raw)


def load_agent_config(path: Path | None = None) -> dict[str, Any]:
    """
    Parse config/agent_config.yaml and return the raw dict.

    Used by LLMService to load system and human prompt templates.

    Args:
        path: Override the default agent config path (useful in tests).
    """
    config_path = path or AGENT_CONFIG_PATH

    if not config_path.exists():
        raise FileNotFoundError(
            f"Agent config file not found: {config_path}\n"
            "Ensure config/agent_config.yaml exists in the backend/ directory."
        )

    with config_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
