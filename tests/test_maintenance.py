from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import Settings, get_settings
from geond.db import connect, run_schema_file
from geond.embeddings import (
    AzureOpenAIEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    get_embedding_provider,
)
from geond.retrieval.simple import search_dev_memory
from geond.storage.benchmark import benchmark_search
from geond.storage.maintenance import purge_workspace, seed_sample_workspace

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"


def test_local_only_privacy_mode_blocks_cloud_embeddings() -> None:
    settings = Settings(
        embedding_provider="openai",
        embedding_api_key="dummy",
        privacy_mode="local-only",
    )

    with pytest.raises(RuntimeError, match="local-only blocks cloud embedding providers"):
        get_embedding_provider(settings)


def test_local_openai_compatible_provider_is_allowed_in_local_only_mode() -> None:
    settings = Settings(
        embedding_provider="local-openai-compatible",
        embedding_base_url="http://localhost:1234/v1",
        embedding_model="local-embedding-model",
        privacy_mode="local-only",
    )

    provider = get_embedding_provider(settings)

    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)
    assert provider.model == "local-embedding-model"


def test_azure_openai_provider_uses_deployment_name() -> None:
    settings = Settings(
        embedding_provider="azure-openai",
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_api_key="dummy",
        azure_openai_embedding_deployment="text-embedding-small-prod",
    )

    provider = get_embedding_provider(settings)

    assert isinstance(provider, AzureOpenAIEmbeddingProvider)
    assert provider.model == "text-embedding-small-prod"


def test_seed_sample_and_purge_workspace() -> None:
    settings = get_settings()

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        unique_uri = f"file:///tmp/geond-seed-test-{uuid4()}"
        seeded = seed_sample_workspace(conn)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workspaces SET root_uri = %s WHERE id = %s",
                (unique_uri, seeded["workspace_id"]),
            )
        conn.commit()

        results = search_dev_memory(conn, "app_context", workspace_uri=unique_uri)
        benchmark = benchmark_search(
            conn,
            ["app_context"],
            mode="keyword",
            repeat=2,
            workspace_uri=unique_uri,
        )
        purged = purge_workspace(conn, unique_uri)
        missing = purge_workspace(conn, unique_uri)

        assert results
        assert benchmark["queries"][0]["result_count"] >= 1
        assert purged["status"] == "deleted"
        assert purged["deleted"]["messages"] >= 2
        assert missing["status"] == "not_found"
