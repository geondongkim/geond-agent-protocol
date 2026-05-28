from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.storage.mcp_audit import audit_mcp_call, record_mcp_audit_event
from geond.storage.repository import upsert_workspace

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
AUDIT_SCHEMA = Path(__file__).parents[1] / "schemas" / "006_mcp_audit_events.sql"


def test_mcp_audit_records_success_without_output_body(monkeypatch) -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-mcp-audit-test-{uuid4()}"

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
            run_schema_file(conn, AUDIT_SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        workspace_id = upsert_workspace(conn, workspace_uri, "mcp-audit", {"source": "pytest"})
        monkeypatch.setenv("GEOND_MCP_AUDIT", "1")
        try:
            result = audit_mcp_call(
                conn,
                workspace_id=workspace_id,
                item_name="search_dev_memory",
                input_payload={"query": "SECRET_TOKEN=abc123456789abcdef"},
                callback=lambda: {
                    "evidence": {
                        "schema": "geond.evidence.v1",
                        "kind": "message",
                        "target_id": "message-1",
                        "locator": {"path": "messages"},
                    }
                },
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status, input_redacted::text, output_redacted, evidence_refs
                    FROM mcp_audit_events
                    WHERE workspace_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (workspace_id,),
                )
                row = cur.fetchone()

            assert result["evidence"]["kind"] == "message"
            assert row[0] == "ok"
            assert "abc123456789abcdef" not in row[1]
            assert row[2] is None
            assert row[3][0]["kind"] == "message"
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()


def test_mcp_audit_records_error() -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-mcp-audit-error-{uuid4()}"

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")

    with conn:
        try:
            run_schema_file(conn, SCHEMA)
            run_schema_file(conn, AUDIT_SCHEMA)
        except psycopg.Error as exc:
            pytest.skip(f"Postgres integration schema is not available: {exc}")

        workspace_id = upsert_workspace(
            conn,
            workspace_uri,
            "mcp-audit-error",
            {"source": "pytest"},
        )
        try:
            audit_id = record_mcp_audit_event(
                conn,
                workspace_id=workspace_id,
                item_name="review_workspace_context",
                input_payload={"workspace_id": workspace_id},
                status="error",
                error_type="ValueError",
                error_message="Bearer abc123456789abcdef",
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, error_type, error_message FROM mcp_audit_events WHERE id = %s",
                    (audit_id,),
                )
                row = cur.fetchone()

            assert row[0] == "error"
            assert row[1] == "ValueError"
            assert "abc123456789abcdef" not in row[2]
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
