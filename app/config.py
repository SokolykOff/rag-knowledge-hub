"""Application settings, read from environment variables (no hidden magic)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


@dataclass(frozen=True)
class Settings:
    env: str = "dev"
    database_url: str = "postgresql+asyncpg://rag:rag@localhost:5432/rag"
    redis_url: str = "redis://localhost:6379/0"
    kafka_bootstrap: str = "localhost:19092"
    event_bus: str = "memory"  # kafka | memory
    cache_backend: str = "redis"  # redis | memory

    role_tokens: str = ""
    demo_mode: bool = True
    seed_demo: bool = True

    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 384

    min_similarity: float = 0.22
    min_overlap: float = 0.34
    default_top_k: int = 5
    rrf_k: int = 60
    rerank_enabled: bool = True
    cache_ttl_seconds: int = 3600
    rate_limit_per_min: int = 60
    max_upload_mb: int = 25
    max_retries: int = 3
    retry_base_delay: float = 0.5

    b24_application_token: str = "demo-b24-token"
    b24_webhook_url: str = ""
    b24_dry_run: bool = True
    b24_tenant_id: str = "acme"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            env=_str("RAG_ENV", "dev"),
            database_url=_str("DATABASE_URL", cls.database_url),
            redis_url=_str("REDIS_URL", cls.redis_url),
            kafka_bootstrap=_str("KAFKA_BOOTSTRAP", cls.kafka_bootstrap),
            event_bus=_str("EVENT_BUS", "memory"),
            cache_backend=_str("CACHE_BACKEND", "redis"),
            role_tokens=_str("RAG_ROLE_TOKENS"),
            demo_mode=_bool("DEMO_MODE", True),
            seed_demo=_bool("SEED_DEMO", True),
            llm_api_key=_str("LLM_API_KEY"),
            llm_base_url=_str("LLM_BASE_URL", cls.llm_base_url),
            llm_model=_str("LLM_MODEL", cls.llm_model),
            embedding_api_key=_str("EMBEDDING_API_KEY"),
            embedding_base_url=_str("EMBEDDING_BASE_URL", cls.embedding_base_url),
            embedding_model=_str("EMBEDDING_MODEL", cls.embedding_model),
            min_similarity=float(_str("MIN_SIMILARITY", "0.22")),
            min_overlap=float(_str("MIN_OVERLAP", "0.34")),
            rate_limit_per_min=int(_str("RATE_LIMIT_PER_MIN", "60")),
            b24_application_token=_str("B24_APPLICATION_TOKEN", "demo-b24-token"),
            b24_webhook_url=_str("B24_WEBHOOK_URL"),
            b24_dry_run=_bool("B24_DRY_RUN", True),
            b24_tenant_id=_str("B24_TENANT_ID", "acme"),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
