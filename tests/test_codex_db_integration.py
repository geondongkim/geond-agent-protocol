from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.adapters.codex import parse_storage
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.retrieval.simple import search_dev_memory
from geond.storage.repository import store_codex_session, upsert_workspace
from geond.storage.usage import record_codex_usage_events, summarize_usage

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "codex"
SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
USAGE_SCHEMA = Path(__file__).parents[1] / "schemas" / "003_llm_usage.sql"


def test_import_codex_fixture_redacts_payloads_and_supports_search() -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-codex-db-test-{uuid4()}"

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
            run_schema_file(conn, USAGE_SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        workspace_id = upsert_workspace(
            conn,
            root_uri=workspace_uri,
            name="codex-db-fixture",
            metadata={"source": "pytest"},
        )
        try:
            sessions = parse_storage(FIXTURE_ROOT, limit=1)
            stored = []
            usage_events = []
            for session in sessions:
                session_row_id = store_codex_session(conn, workspace_id, session)
                stored.append(session_row_id)
                store_codex_session(conn, workspace_id, session)
                usage_events.extend(
                    record_codex_usage_events(
                        conn,
                        workspace_id=workspace_id,
                        session=session,
                        session_row_id=session_row_id,
                    )
                )
                record_codex_usage_events(
                    conn,
                    workspace_id=workspace_id,
                    session=session,
                    session_row_id=session_row_id,
                )

            results = search_dev_memory(
                conn,
                "추가 테스트베드",
                limit=5,
                workspace_uri=workspace_uri,
                source="codex",
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*)
                    FROM events
                    WHERE workspace_id = %s
                      AND payload::text LIKE %s
                    """,
                    (workspace_id, "%dummy-secret-value%"),
                )
                unredacted_event_count = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT count(*)
                    FROM redaction_findings
                    WHERE workspace_id = %s
                      AND source = 'codex'
                    """,
                    (workspace_id,),
                )
                finding_count = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT count(*)
                    FROM agent_actions aa
                    JOIN agents a ON a.id = aa.agent_id
                    WHERE aa.workspace_id = %s
                      AND aa.action_type = 'session_observed'
                      AND a.name = 'codex'
                    """,
                    (workspace_id,),
                )
                action_count = cur.fetchone()[0]
            usage_summary = summarize_usage(conn, workspace_id, source="codex")

            assert stored
            assert usage_events
            assert usage_summary["totals"]["event_count"] == 1
            assert usage_summary["totals"]["estimated_event_count"] == 1
            assert usage_summary["totals"]["total_tokens"] > 0
            assert results
            assert results[0]["source"] == "codex"
            assert unredacted_event_count == 0
            assert finding_count >= 1
            assert action_count == 1
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
