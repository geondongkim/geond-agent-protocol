from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.storage import orchestration
from geond.storage.benchmark import save_benchmark_run
from geond.storage.dashboard import (
    get_agent_activity_events,
    get_dashboard_changesets,
    get_dashboard_code_risk,
    get_dashboard_orchestration,
    get_dashboard_overview,
    get_dashboard_project_activity,
    get_dashboard_sessions,
    get_dashboard_usage,
    get_dashboard_workspaces,
    is_readable_dashboard_message,
)
from geond.storage.repository import (
    record_agent_action,
    record_changeset,
    record_handoff_summary,
    reserve_files,
    reserve_symbols,
    upsert_workspace,
)
from geond.storage.usage import insert_usage_event

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
USAGE_SCHEMA = Path(__file__).parents[1] / "schemas" / "003_llm_usage.sql"
ORCHESTRATION_SCHEMA = Path(__file__).parents[1] / "schemas" / "007_orchestration.sql"
TASK_GRAPH_SCHEMA = Path(__file__).parents[1] / "schemas" / "008_orchestration_task_graph.sql"


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
            run_schema_file(conn, ORCHESTRATION_SCHEMA)
            run_schema_file(conn, TASK_GRAPH_SCHEMA)
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
                for role, ordinal, content in [
                    ("user", 1, "Can the dashboard show context?"),
                    ("assistant", 2, "Yes, readable excerpts are shown."),
                    ("assistant_or_tool", 3, "toolInvocationSerialized"),
                    ("metadata", 4, "1234"),
                ]:
                    cur.execute(
                        """
                        INSERT INTO messages (session_id, role, ordinal, content)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (session_id, role, ordinal, content),
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
            run = orchestration.create_run(
                conn,
                workspace_id,
                "Dashboard orchestration run",
                risk_level="high",
            )
            task = orchestration.create_task(
                conn,
                run["run"]["run_id"],
                "Inspect mission control state",
            )
            worker = orchestration.register_worker_session(
                conn,
                run["run"]["run_id"],
                "codex",
            )
            orchestration.claim_task(
                conn,
                task["task"]["task_id"],
                "codex",
                worker_session_id=worker["worker_session"]["worker_session_id"],
            )
            orchestration.record_command_evidence(
                conn,
                run["run"]["run_id"],
                command="uv run pytest tests/test_dashboard.py",
                task_id=task["task"]["task_id"],
                worker_session_id=worker["worker_session"]["worker_session_id"],
                exit_code=0,
            )
            orchestration.record_review_finding(
                conn,
                run["run"]["run_id"],
                "Mission control needs review.",
                severity="P1",
            )
            orchestration.request_approval(
                conn,
                run["run"]["run_id"],
                "High-risk dashboard release gate.",
                risk_level="high",
            )

            overview = get_dashboard_overview(conn, workspace_id, limit=10)
            activity = get_agent_activity_events(conn, workspace_uri, limit=20)
            filtered_activity = get_agent_activity_events(
                conn,
                workspace_uri,
                limit=20,
                event_kind="agent_action",
                agent_name="agent-a",
                status="recorded",
            )
            sessions = get_dashboard_sessions(conn, workspace_id, limit=10, message_limit=2)
            workspaces = get_dashboard_workspaces(conn, limit=50)
            project = get_dashboard_project_activity(conn, workspace_id, limit=10)
            code_risk = get_dashboard_code_risk(conn, workspace_id, limit=10)
            changesets = get_dashboard_changesets(conn, workspace_id, limit=10)
            orchestration_dashboard = get_dashboard_orchestration(
                conn,
                workspace_id,
                limit=10,
                base_dir=Path("tmp/geond-runs"),
            )

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
            assert any(
                event["kind"] == "session" and event["agent_name"] == "codex"
                for event in activity["events"]
            )
            assert filtered_activity["filters"] == {
                "kind": "agent_action",
                "agent_name": "agent-a",
                "status": "recorded",
            }
            assert [event["kind"] for event in filtered_activity["events"]] == ["agent_action"]
            assert filtered_activity["events"][0]["agent_name"] == "agent-a"
            assert sessions["status"] == "ok"
            assert sessions["sessions"][0]["agent_name"] == "codex"
            assert sessions["sessions"][0]["messages"][0]["role"] == "assistant_or_tool"
            assert sessions["sessions"][0]["readable_messages"][0]["role"] == "user"
            assert sessions["sessions"][0]["readable_excerpt_count"] == 2
            assert sessions["sessions"][0]["role_counts"] == {
                "user": 1,
                "agent": 1,
                "captured": 0,
                "technical": 2,
            }
            assert sessions["sessions"][0]["conversation_signal"] == "readable"
            assert any(item["workspace_id"] == workspace_id for item in workspaces["workspaces"])
            assert project["status"] == "ok"
            assert project["files"][0]["file_path"] == "service.py"
            assert project["files"][0]["status"] == "active"
            assert code_risk["status"] == "ok"
            assert code_risk["summary"]["high"] >= 1
            assert code_risk["files"][0]["file_path"] == "service.py"
            assert code_risk["files"][0]["risk_level"] == "high"
            assert "active file claim" in code_risk["files"][0]["risk_signals"]
            assert changesets["status"] == "ok"
            assert changesets["summary"]["changesets"] == 1
            assert changesets["summary"]["files"] == 1
            assert changesets["changesets"][0]["files"][0]["file_path"] == "service.py"
            assert orchestration_dashboard["schema"] == "geond.dashboard_orchestration.v1"
            assert orchestration_dashboard["summary"]["active_runs"] >= 1
            assert orchestration_dashboard["runs"][0]["readiness_status"] == "not_ready"
            assert orchestration_dashboard["runs"][0]["active_worker_count"] == 1
            assert orchestration_dashboard["runs"][0]["active_lease_count"] == 1
            assert orchestration_dashboard["runs"][0]["open_finding_count"] == 1
            assert orchestration_dashboard["runs"][0]["pending_approval_count"] == 1
            assert orchestration_dashboard["runs"][0]["command_evidence_count"] == 1
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()


def test_dashboard_readable_message_filter_skips_numeric_metadata() -> None:
    assert not is_readable_dashboard_message(
        {"role": "metadata_or_text", "content": "3\n78\n3\n101"}
    )
    assert is_readable_dashboard_message(
        {"role": "metadata_or_text", "content": "Please inspect the dashboard."}
    )


def test_dashboard_usage_rollup_links_usage_to_evidence() -> None:
    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-dashboard-usage-test-{uuid4()}"
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
            name="dashboard-usage-fixture",
            metadata={"source": "pytest"},
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sessions (workspace_id, source, external_id, title, metadata)
                    VALUES (%s, 'codex', 'usage-session', 'Usage session', '{}')
                    RETURNING id::text
                    """,
                    (workspace_id,),
                )
                session_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO messages (session_id, role, ordinal, content)
                    VALUES (%s, 'user', 1, 'Please add usage dashboard evidence.')
                    """,
                    (session_id,),
                )
            record_changeset(
                conn,
                workspace_id=workspace_id,
                files=[{"file_path": "usage.py", "status": "modified"}],
                summary="Added usage dashboard read model.",
            )
            record_handoff_summary(
                conn,
                workspace_id=workspace_id,
                from_agent_name="agent-a",
                summary="Usage dashboard is ready for review.",
                tested_commands=["uv run pytest tests/test_dashboard.py"],
            )
            insert_usage_event(
                conn,
                workspace_id=workspace_id,
                session_id=session_id,
                source="codex",
                provider="openai",
                model="gpt-test",
                input_tokens=100,
                output_tokens=50,
                estimated=False,
                source_record_id=f"dashboard-usage:{uuid4()}",
            )

            usage = get_dashboard_usage(conn, workspace_uri)

            assert usage["status"] == "ok"
            assert usage["usage"]["totals"]["total_tokens"] == 150
            assert usage["usage"]["data_quality"]["exact_token_share"] == 1.0
            assert usage["evidence"]["changesets"] == 1
            assert usage["evidence"]["tested_handoffs"] == 1
            assert usage["evidence"]["user_prompts"] == 1
            assert usage["usage_vs_evidence"]["tokens_per_changeset"] == 150.0
            assert usage["usage_vs_evidence"]["has_output_evidence"] is True
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
