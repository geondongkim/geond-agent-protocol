from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.storage.benchmark import save_benchmark_run
from geond.storage.dashboard import (
    get_agent_activity_events,
    get_dashboard_overview,
    get_dashboard_sessions,
)
from geond.storage.repository import (
    record_agent_action,
    record_changeset,
    record_handoff_summary,
    reserve_files,
    reserve_symbols,
    upsert_workspace,
)

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"


def test_dashboard_overview_and_activity_events() -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-dashboard-test-{uuid4()}"
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
            name="dashboard-fixture",
            metadata={"source": "pytest"},
        )
        try:
            reserve_files(
                conn,
                workspace_id,
                agent_name="agent-a",
                file_paths=["service.py"],
                purpose="dashboard smoke",
            )
            reserve_symbols(
                conn,
                workspace_id,
                agent_name="agent-a",
                symbols=["service.build_answer"],
                purpose="dashboard smoke",
            )
            record_agent_action(
                conn,
                workspace_id=workspace_id,
                agent_name="agent-a",
                action_type="review",
                summary="Reviewed current dashboard slice.",
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sessions (workspace_id, source, external_id, title, metadata)
                    VALUES (%s, 'codex', 'dashboard-session', 'Dashboard session', '{}')
                    RETURNING id
                    """,
                    (workspace_id,),
                )
                session_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO messages (session_id, role, ordinal, content)
                    VALUES
                      (%s, 'user', 1, 'Can the dashboard show session context?'),
                      (%s, 'assistant', 2, 'Yes, recent messages are shown per lane.')
                    """,
                    (session_id, session_id),
                )
            record_handoff_summary(
                conn,
                workspace_id=workspace_id,
                from_agent_name="agent-a",
                to_agent_name="agent-b",
                summary="Continue from dashboard overview.",
                next_action="Build the read-only UI shell.",
            )
            record_changeset(
                conn,
                workspace_id=workspace_id,
                files=[{"file_path": "service.py", "status": "modified"}],
                branch="main",
                intent="dashboard read model",
                summary="Added dashboard activity projection.",
            )
            save_benchmark_run(
                conn,
                result={"mode": "keyword", "repeat": 1, "queries": []},
                label="dashboard-smoke",
                workspace_uri=workspace_id,
            )

            overview = get_dashboard_overview(conn, workspace_id, limit=10)
            activity = get_agent_activity_events(conn, workspace_uri, limit=20)
            sessions = get_dashboard_sessions(conn, workspace_id, limit=10, message_limit=2)

            assert overview["status"] == "ok"
            assert overview["counts"]["sessions"] >= 1
            assert overview["counts"]["active_file_reservations"] == 1
            assert overview["counts"]["active_symbol_reservations"] == 1
            assert overview["counts"]["open_handoffs"] == 1
            assert overview["counts"]["changesets"] == 1
            assert overview["counts"]["benchmark_runs"] == 1
            assert overview["lineage"]["node_count"] >= 3
            assert overview["recent_activity"]

            kinds = {event["kind"] for event in activity["events"]}
            assert {
                "agent_action",
                "file_reservation",
                "symbol_reservation",
                "reservation_event",
                "handoff_summary",
                "changeset",
                "benchmark_run",
            } <= kinds
            assert any(event["agent_name"] == "agent-a" for event in activity["events"])
            assert sessions["status"] == "ok"
            assert sessions["sessions"][0]["agent_name"] == "codex"
            assert sessions["sessions"][0]["messages"][0]["role"] == "user"
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
