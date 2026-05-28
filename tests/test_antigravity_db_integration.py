from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.adapters.antigravity import parse_storage, parse_transcript_file
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.retrieval.simple import search_dev_memory
from geond.storage.repository import store_antigravity_session, upsert_workspace

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "antigravity" / "brain"
SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"


def test_import_antigravity_fixture_redacts_payloads_and_supports_search() -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-antigravity-db-test-{uuid4()}"

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
            name="antigravity-db-fixture",
            metadata={"source": "pytest"},
        )
        try:
            sessions = parse_storage(FIXTURE_ROOT)
            stored = [
                store_antigravity_session(conn, workspace_id, session) for session in sessions
            ]
            store_antigravity_session(conn, workspace_id, sessions[0])

            results = search_dev_memory(
                conn,
                "AGY_SIDE_BY_SIDE_20260529",
                limit=5,
                workspace_uri=workspace_uri,
                source="antigravity",
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*)
                    FROM events
                    WHERE workspace_id = %s
                      AND payload::text LIKE %s
                    """,
                    (workspace_id, "%abc123456789abcdef%"),
                )
                unredacted_event_count = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT count(*)
                    FROM agent_actions
                    WHERE workspace_id = %s
                      AND action_type = 'session_observed'
                    """,
                    (workspace_id,),
                )
                action_count = cur.fetchone()[0]

            assert stored
            assert results
            assert results[0]["source"] == "antigravity"
            assert unredacted_event_count == 0
            assert action_count == 1
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()


def test_import_antigravity_preserves_raw_wrapper_event_but_searches_normalized_message(
    tmp_path: Path,
) -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-antigravity-wrapper-test-{uuid4()}"
    transcript = tmp_path / "wrapped-session" / ".system_generated" / "logs" / "transcript.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        (
            '{"source":"user","type":"message","created_at":"2026-05-29T00:00:00Z",'
            '"content":"<USER_REQUEST>\\nGEOND_WRAPPED_DB_MARKER core request\\n'
            '</USER_REQUEST>\\n<ADDITIONAL_METADATA>\\nnoise=yes\\n</ADDITIONAL_METADATA>"}\n'
        ),
        encoding="utf-8",
    )

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
            name="antigravity-wrapper-fixture",
            metadata={"source": "pytest"},
        )
        try:
            session = parse_transcript_file(transcript)
            session_row_id = store_antigravity_session(conn, workspace_id, session)

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content FROM messages WHERE session_id = %s::uuid",
                    (session_row_id,),
                )
                message_content = cur.fetchone()[0]
                cur.execute(
                    "SELECT payload->>'content' FROM events WHERE session_id = %s::uuid",
                    (session_row_id,),
                )
                raw_event_content = cur.fetchone()[0]

            results = search_dev_memory(
                conn,
                "GEOND_WRAPPED_DB_MARKER",
                limit=5,
                workspace_uri=workspace_uri,
                source="antigravity",
            )

            assert message_content == "GEOND_WRAPPED_DB_MARKER core request"
            assert "<ADDITIONAL_METADATA>" not in message_content
            assert "<ADDITIONAL_METADATA>" in raw_event_content
            assert results
            assert "<ADDITIONAL_METADATA>" not in results[0]["snippet"]
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
