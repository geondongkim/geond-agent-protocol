from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from geond.config import Settings

CLOUD_EMBEDDING_PROVIDERS = {
    "openai",
    "openai-compatible",
    "github-models",
    "gateway",
    "azure-openai",
}
LOCAL_EMBEDDING_PROVIDERS = {"local", "local-openai-compatible", "ollama"}


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
    if (
        settings.privacy_mode == "local-only"
        and settings.embedding_provider in CLOUD_EMBEDDING_PROVIDERS
    ):
        raise RuntimeError(
            "GEOND_PRIVACY_MODE=local-only blocks cloud embedding providers. "
            "Use GEOND_EMBEDDING_PROVIDER=none, local-openai-compatible, or ollama."
        )
    if settings.embedding_provider == "azure-openai":
        return AzureOpenAIEmbeddingProvider(settings)
    if settings.embedding_provider in {"openai", "openai-compatible", "gateway"}:
        return OpenAICompatibleEmbeddingProvider(settings)
    if settings.embedding_provider == "github-models":
        return OpenAICompatibleEmbeddingProvider(
            settings,
            default_base_url="https://models.github.ai/inference",
        )
    if settings.embedding_provider in LOCAL_EMBEDDING_PROVIDERS:
        default_base_url = (
            "http://localhost:11434/v1" if settings.embedding_provider == "ollama" else ""
        )
        return OpenAICompatibleEmbeddingProvider(
            settings,
            default_base_url=default_base_url,
            require_api_key=False,
            require_base_url=True,
        )
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")


@dataclass(frozen=True)
class OpenAICompatibleEmbeddingProvider:
    settings: Settings
    default_base_url: str = ""
    require_api_key: bool = True
    require_base_url: bool = False

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

        api_key = self.settings.embedding_api_key or ("local" if not self.require_api_key else "")
        base_url = self.settings.embedding_base_url or self.default_base_url

        if self.require_api_key and not api_key:
            raise RuntimeError("GEOND_EMBEDDING_API_KEY is required for embeddings.")
        if self.require_base_url and not base_url:
            raise RuntimeError("GEOND_EMBEDDING_BASE_URL is required for local embeddings.")
        if not self.settings.embedding_model:
            raise RuntimeError("GEOND_EMBEDDING_MODEL is required for embeddings.")

        client = OpenAI(
            api_key=api_key,
            base_url=base_url or None,
        )
        response = client.embeddings.create(model=self.settings.embedding_model, input=texts)
        return [item.embedding for item in response.data]


@dataclass(frozen=True)
class AzureOpenAIEmbeddingProvider:
    settings: Settings

    @property
    def model(self) -> str:
        return self.settings.azure_openai_embedding_deployment or self.settings.embedding_model

    @property
    def dimensions(self) -> int:
        return self.settings.embedding_dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            from openai import AzureOpenAI
        except ImportError as exc:
            raise RuntimeError("Install project dependencies first: uv sync") from exc

        endpoint = self.settings.azure_openai_endpoint or self.settings.embedding_base_url
        if not endpoint:
            raise RuntimeError("GEOND_AZURE_OPENAI_ENDPOINT is required for Azure OpenAI.")
        if not self.model:
            raise RuntimeError("GEOND_AZURE_OPENAI_EMBEDDING_DEPLOYMENT is required.")

        client_kwargs = {
            "azure_endpoint": endpoint,
            "api_version": self.settings.azure_openai_api_version,
        }
        if self.settings.azure_openai_auth_mode in {"entra-id", "entra", "aad"}:
            client_kwargs["azure_ad_token_provider"] = azure_ad_token_provider()
        else:
            api_key = self.settings.azure_openai_api_key or self.settings.embedding_api_key
            if not api_key:
                raise RuntimeError("GEOND_AZURE_OPENAI_API_KEY is required for Azure OpenAI.")
            client_kwargs["api_key"] = api_key

        client = AzureOpenAI(**client_kwargs)
        response = client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]


def azure_ad_token_provider() -> Any:
    try:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    except ImportError as exc:
        raise RuntimeError("Install azure-identity to use Azure OpenAI Entra ID auth.") from exc

    return get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://cognitiveservices.azure.com/.default",
    )


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
