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
    embedding_provider: str = os.getenv("GEOND_EMBEDDING_PROVIDER", "none").lower()
    embedding_model: str = os.getenv("GEOND_EMBEDDING_MODEL", "")
    embedding_base_url: str = os.getenv("GEOND_EMBEDDING_BASE_URL", "")
    embedding_api_key: str = os.getenv("GEOND_EMBEDDING_API_KEY", "")
    embedding_dimensions: int = int(os.getenv("GEOND_EMBEDDING_DIMENSIONS", "1536"))
    store_raw_payloads: bool = os.getenv("GEOND_STORE_RAW_PAYLOADS", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    max_import_bytes: int = int(os.getenv("GEOND_MAX_IMPORT_BYTES", "104857600"))


def get_settings() -> Settings:
    return Settings()
