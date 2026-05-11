from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import Settings, get_settings
from geond.db import connect, run_schema_file
from geond.embeddings import get_embedding_provider
from geond.retrieval.simple import search_dev_memory
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
        purged = purge_workspace(conn, unique_uri)
        missing = purge_workspace(conn, unique_uri)

        assert results
        assert purged["status"] == "deleted"
        assert purged["deleted"]["messages"] >= 2
        assert missing["status"] == "not_found"
