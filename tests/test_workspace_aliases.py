from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.retrieval.simple import search_dev_memory
from geond.storage.repository import (
    list_workspace_aliases,
    record_workspace_fingerprints,
    register_workspace_alias,
    suggest_workspace_aliases,
    upsert_workspace,
)

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"


def test_workspace_alias_tracks_moved_folder_for_imports_and_search() -> None:
    settings = get_settings()
    old_uri = f"file:///tmp/geond-old-folder-{uuid4()}"
    new_uri = old_uri.replace("old-folder", "new-folder")

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        workspace_id = upsert_workspace(
            conn,
            root_uri=old_uri,
            name="old-folder",
            metadata={"source": "pytest"},
        )
        other_workspace_id = upsert_workspace(
            conn,
            root_uri=f"file:///tmp/geond-other-folder-{uuid4()}",
            name="other-folder",
            metadata={"source": "pytest"},
        )
        try:
            fingerprints = [
                {
                    "fingerprint_type": "git:remote:first-commit",
                    "fingerprint_value": "https://github.com/example/project.git#abc123",
                    "metadata": {"source": "pytest"},
                }
            ]
            recorded_fingerprints = record_workspace_fingerprints(conn, old_uri, fingerprints)
            suggestions = suggest_workspace_aliases(conn, new_uri, fingerprints)
            alias = register_workspace_alias(
                conn,
                workspace_id_or_uri=old_uri,
                alias_uri=new_uri,
                reason="folder-move",
                metadata={"test": True},
            )
            with pytest.raises(ValueError):
                register_workspace_alias(
                    conn,
                    workspace_id_or_uri=other_workspace_id,
                    alias_uri=new_uri,
                    reason="conflicting-folder-move",
                )
            reused_workspace_id = upsert_workspace(
                conn,
                root_uri=new_uri,
                name="new-folder",
                metadata={"moved": True},
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sessions (workspace_id, source, external_id, title)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id::text
                    """,
                    (workspace_id, "pytest", "moved-session", "Moved folder session"),
                )
                session_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO messages (session_id, role, ordinal, content)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        session_id,
                        "user",
                        1,
                        "폴더 이동 후에도 같은 작업 기억을 검색해야 한다.",
                    ),
                )
            conn.commit()

            aliases = list_workspace_aliases(conn, new_uri)
            results = search_dev_memory(conn, "폴더 이동", workspace_uri=new_uri, limit=5)

            assert recorded_fingerprints[0]["workspace_id"] == workspace_id
            assert suggestions[0]["workspace_id"] == workspace_id
            assert suggestions[0]["confidence"] == 1.0
            assert suggestions[0]["recommendation"] == "register_best_candidate"
            assert suggestions[0]["unmatched_fingerprint_count"] == 0
            assert suggestions[0]["competing_top_matches"] == 1
            assert alias["workspace_id"] == workspace_id
            assert reused_workspace_id == workspace_id
            assert aliases[0]["alias_uri"] == new_uri
            assert results
            assert results[0]["workspace_id"] == workspace_id
            assert results[0]["trigram_score"] >= 0
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM workspaces WHERE id IN (%s, %s)",
                    (workspace_id, other_workspace_id),
                )
            conn.commit()


def test_workspace_alias_suggestions_explain_ambiguous_fingerprint_matches() -> None:
    settings = get_settings()
    first_uri = f"file:///tmp/geond-first-candidate-{uuid4()}"
    second_uri = f"file:///tmp/geond-second-candidate-{uuid4()}"
    new_uri = f"file:///tmp/geond-ambiguous-folder-{uuid4()}"

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        first_workspace_id = upsert_workspace(
            conn,
            root_uri=first_uri,
            name="first-candidate",
            metadata={"source": "pytest"},
        )
        second_workspace_id = upsert_workspace(
            conn,
            root_uri=second_uri,
            name="second-candidate",
            metadata={"source": "pytest"},
        )
        try:
            fingerprints = [
                {
                    "fingerprint_type": "file:sha256:pyproject.toml",
                    "fingerprint_value": "abc123",
                    "metadata": {"source": "pytest"},
                }
            ]
            record_workspace_fingerprints(conn, first_uri, fingerprints)
            record_workspace_fingerprints(conn, second_uri, fingerprints)

            suggestions = suggest_workspace_aliases(conn, new_uri, fingerprints)

            assert {item["workspace_id"] for item in suggestions} == {
                first_workspace_id,
                second_workspace_id,
            }
            assert {item["recommendation"] for item in suggestions} == {"ambiguous_top_candidates"}
            assert all(item["competing_top_matches"] == 2 for item in suggestions)
            assert all(item["unmatched_fingerprint_count"] == 0 for item in suggestions)
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM workspaces WHERE id IN (%s, %s)",
                    (first_workspace_id, second_workspace_id),
                )
            conn.commit()
