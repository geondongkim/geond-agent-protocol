from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from geond.config import Settings


class EmbeddingProvider(Protocol):
    model: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class DisabledEmbeddingProvider:
    model: str = "none"
    dimensions: int = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError(
            "Embeddings are disabled. Set GEOND_EMBEDDING_PROVIDER and provider credentials."
        )


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider in {"", "none", "disabled"}:
        return DisabledEmbeddingProvider()
    if settings.privacy_mode == "local-only" and settings.embedding_provider in {
        "openai",
        "openai-compatible",
        "github-models",
    }:
        raise RuntimeError(
            "GEOND_PRIVACY_MODE=local-only blocks cloud embedding providers. "
            "Use GEOND_EMBEDDING_PROVIDER=none or a local provider."
        )
    if settings.embedding_provider in {"openai", "openai-compatible", "github-models"}:
        return OpenAICompatibleEmbeddingProvider(settings)
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")


@dataclass(frozen=True)
class OpenAICompatibleEmbeddingProvider:
    settings: Settings

    @property
    def model(self) -> str:
        return self.settings.embedding_model

    @property
    def dimensions(self) -> int:
        return self.settings.embedding_dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install project dependencies first: uv sync") from exc

        if not self.settings.embedding_api_key:
            raise RuntimeError("GEOND_EMBEDDING_API_KEY is required for embeddings.")
        if not self.settings.embedding_model:
            raise RuntimeError("GEOND_EMBEDDING_MODEL is required for embeddings.")

        client = OpenAI(
            api_key=self.settings.embedding_api_key,
            base_url=self.settings.embedding_base_url or None,
        )
        response = client.embeddings.create(model=self.settings.embedding_model, input=texts)
        return [item.embedding for item in response.data]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
