from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "GEOND_DATABASE_URL",
        "postgresql://geond:geond_dev_password@localhost:55432/geond",
    )
    embedding_provider: str = (os.getenv("GEOND_EMBEDDING_PROVIDER") or "openai").lower()
    embedding_model: str = os.getenv("GEOND_EMBEDDING_MODEL") or "text-embedding-3-small"
    embedding_base_url: str = os.getenv("GEOND_EMBEDDING_BASE_URL", "")
    embedding_api_key: str = os.getenv("GEOND_EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    embedding_dimensions: int = int(os.getenv("GEOND_EMBEDDING_DIMENSIONS", "1536"))
    embedding_max_chars: int = int(os.getenv("GEOND_EMBEDDING_MAX_CHARS", "3000"))
    azure_openai_endpoint: str = os.getenv("GEOND_AZURE_OPENAI_ENDPOINT", "")
    azure_openai_api_key: str = os.getenv("GEOND_AZURE_OPENAI_API_KEY", "")
    azure_openai_auth_mode: str = os.getenv("GEOND_AZURE_OPENAI_AUTH_MODE", "api-key").lower()
    azure_openai_api_version: str = os.getenv("GEOND_AZURE_OPENAI_API_VERSION", "2024-10-21")
    azure_openai_embedding_deployment: str = os.getenv(
        "GEOND_AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
        "",
    )
    openai_api_key: str = os.getenv("OPENAI_API_KEY") or os.getenv("GEOND_EMBEDDING_API_KEY", "")
    llm_model_reasoning: str = os.getenv("GEOND_LLM_MODEL_REASONING") or "gpt-5.4"
    llm_model_balanced: str = os.getenv("GEOND_LLM_MODEL_BALANCED") or "gpt-5.4-mini"
    llm_model_fast: str = os.getenv("GEOND_LLM_MODEL_FAST") or "gpt-5.4-nano"
    store_raw_payloads: bool = os.getenv("GEOND_STORE_RAW_PAYLOADS", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    privacy_mode: str = os.getenv("GEOND_PRIVACY_MODE", "redacted-cloud").lower()
    max_import_bytes: int = int(os.getenv("GEOND_MAX_IMPORT_BYTES", "104857600"))
    rerank_url: str = os.getenv("GEOND_RERANK_URL", "")
    rerank_api_key: str = os.getenv("GEOND_RERANK_API_KEY", "")
    rerank_timeout_seconds: float = float(os.getenv("GEOND_RERANK_TIMEOUT_SECONDS", "10"))


def get_settings() -> Settings:
    return Settings()
