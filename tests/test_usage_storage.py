from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.storage.repository import upsert_workspace
from geond.storage.usage import insert_usage_event, summarize_usage

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
USAGE_SCHEMA = Path(__file__).parents[1] / "schemas" / "003_llm_usage.sql"


def test_insert_usage_event_and_summary_are_idempotent() -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-usage-test-{uuid4()}"

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
            name="usage-fixture",
            metadata={"source": "pytest"},
        )
        try:
            first_id = insert_usage_event(
                conn,
                workspace_id=workspace_id,
                source="codex",
                agent_name="codex",
                provider="openai",
                model="gpt-test",
                operation="chat.completion",
                input_tokens=100,
                output_tokens=40,
                estimated=False,
                estimated_cost_usd=Decimal("0.0012"),
                source_record_id="codex:session:1",
                metadata={"quality": "exact"},
            )
            second_id = insert_usage_event(
                conn,
                workspace_id=workspace_id,
                source="codex",
                agent_name="codex",
                provider="openai",
                model="gpt-test",
                operation="chat.completion",
                input_tokens=120,
                output_tokens=40,
                estimated=True,
                source_record_id="codex:session:1",
                metadata={"quality": "estimated"},
            )
            insert_usage_event(
                conn,
                workspace_id=workspace_id,
                source="vscode_copilot",
                provider="github",
                model="copilot-chat",
                operation="message",
                total_tokens=50,
                estimated=True,
                source_record_id="copilot:message:1",
            )

            summary = summarize_usage(conn, workspace_id)

            assert second_id == first_id
            assert summary["status"] == "ok"
            assert summary["totals"]["event_count"] == 2
            assert summary["totals"]["total_tokens"] == 210
            assert summary["totals"]["estimated_event_count"] == 2
            assert summary["data_quality"]["estimated_token_share"] == 1.0
            assert {row["source"] for row in summary["by_source"]} == {
                "codex",
                "vscode_copilot",
            }
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
