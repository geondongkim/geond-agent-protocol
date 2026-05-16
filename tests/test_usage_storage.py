from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.storage.repository import upsert_workspace
from geond.storage.usage import (
    estimate_text_tokens,
    extract_token_usage_candidates,
    insert_usage_event,
    summarize_usage,
)

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
USAGE_SCHEMA = Path(__file__).parents[1] / "schemas" / "003_llm_usage.sql"


def test_token_usage_candidates_normalize_openai_shapes() -> None:
    candidates = extract_token_usage_candidates(
        {
            "payload": {
                "usage": {
                    "prompt_tokens": "120",
                    "completion_tokens": 40,
                    "total_tokens": 160,
                    "prompt_tokens_details": {"cached_tokens": 20},
                    "completion_tokens_details": {"reasoning_tokens": 5},
                }
            }
        }
    )

    assert candidates == [
        {
            "input_tokens": 120,
            "output_tokens": 40,
            "cached_input_tokens": 20,
            "reasoning_tokens": 5,
            "total_tokens": 160,
        }
    ]
    assert estimate_text_tokens("abcd") == 1
    assert estimate_text_tokens("abcde") == 2


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
            assert {row["agent_name"] for row in summary["by_agent"]} == {
                "codex",
                "vscode_copilot",
            }
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
