from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.adapters.vscode_copilot import CHAT_INDEX_KEY, SOURCE, parse_storage
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.storage.repository import store_vscode_session, upsert_workspace
from geond.storage.usage import record_vscode_copilot_usage_events, summarize_usage

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "vscode_copilot"
SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
USAGE_SCHEMA = Path(__file__).parents[1] / "schemas" / "003_llm_usage.sql"


def create_state_db(storage_path: Path) -> None:
    db_path = storage_path / "state.vscdb"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
            (
                CHAT_INDEX_KEY,
                json.dumps(
                    {
                        "entries": {
                            "vscode-session-1": {
                                "sessionId": "vscode-session-1",
                                "title": "VS Code fixture session",
                            }
                        }
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def copy_fixture_tree(tmp_path: Path) -> Path:
    storage_path = tmp_path / "workspaceStorage"
    for source in FIXTURE_ROOT.rglob("*"):
        if source.is_dir():
            continue
        target = storage_path / source.relative_to(FIXTURE_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    create_state_db(storage_path)
    return storage_path


def test_import_vscode_fixture_records_usage_estimate(tmp_path: Path) -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-vscode-db-test-{uuid4()}"
    storage_path = copy_fixture_tree(tmp_path)

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
            name="vscode-db-fixture",
            metadata={"source": "pytest"},
        )
        try:
            session = parse_storage(storage_path)[0]
            session_row_id = store_vscode_session(conn, workspace_id, session)
            usage_events = record_vscode_copilot_usage_events(
                conn,
                workspace_id=workspace_id,
                session=session,
                session_row_id=session_row_id,
            )
            record_vscode_copilot_usage_events(
                conn,
                workspace_id=workspace_id,
                session=session,
                session_row_id=session_row_id,
            )
            summary = summarize_usage(conn, workspace_id, source=SOURCE)

            assert usage_events
            assert summary["totals"]["event_count"] == 1
            assert summary["totals"]["estimated_event_count"] == 1
            assert summary["totals"]["total_tokens"] > 0
            assert summary["by_model"][0]["provider"] == "github"
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
