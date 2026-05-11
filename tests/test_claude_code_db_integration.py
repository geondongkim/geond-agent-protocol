from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.adapters.claude_code import SOURCE, parse_storage
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.retrieval.simple import search_dev_memory
from geond.storage.repository import store_claude_code_session, upsert_workspace

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "claude_code"
SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"


def test_import_claude_code_fixture_redacts_payloads_and_supports_search() -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-claude-code-db-test-{uuid4()}"

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
            root_uri=workspace_uri,
            name="claude-code-db-fixture",
            metadata={"source": "pytest"},
        )
        try:
            sessions = parse_storage(FIXTURE_ROOT, limit=1)
            stored = [
                store_claude_code_session(conn, workspace_id, session) for session in sessions
            ]
            results = search_dev_memory(
                conn,
                "importer fixture",
                workspace_uri=workspace_uri,
                source=SOURCE,
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT content
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    WHERE s.workspace_id = %s
                      AND s.source = %s
                      AND m.role = 'user'
                    """,
                    (workspace_id, SOURCE),
                )
                message_content = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT count(*)
                    FROM redaction_findings
                    WHERE workspace_id = %s
                      AND source = %s
                    """,
                    (workspace_id, SOURCE),
                )
                finding_count = cur.fetchone()[0]

            assert stored
            assert results
            assert results[0]["source"] == SOURCE
            assert "dummyBearerTokenValue12345" not in message_content
            assert "[REDACTED]" in message_content
            assert finding_count >= 1
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
