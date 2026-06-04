from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from geond import agent_hooks
from geond.config import get_settings
from geond.db import connect, run_schema_file
from geond.storage import orchestration
from geond.storage.dashboard import get_agent_activity_events

SCHEMA = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
ORCHESTRATION_SCHEMA = Path(__file__).parents[1] / "schemas" / "007_orchestration.sql"
TASK_GRAPH_SCHEMA = Path(__file__).parents[1] / "schemas" / "008_orchestration_task_graph.sql"


def _connect_with_schema() -> psycopg.Connection:
    settings = get_settings()
    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres integration database is not available: {exc}")
    try:
        run_schema_file(conn, SCHEMA)
        run_schema_file(conn, ORCHESTRATION_SCHEMA)
        run_schema_file(conn, TASK_GRAPH_SCHEMA)
    except psycopg.Error as exc:
        pytest.skip(f"Postgres integration schema is not available: {exc}")
    return conn


def test_normalize_hook_payload_defaults_validation_status() -> None:
    payload = agent_hooks.normalize_hook_payload(
        {
            "workspace_id_or_uri": "file:///repo",
            "agent_name": "codex",
            "event_type": "validation",
            "session_external_id": "session-1",
            "command": "uv run pytest",
            "exit_code": "0",
        }
    )

    assert payload["schema"] == "geond.agent_hook_event.v1"
    assert payload["status"] == "passed"
    assert payload["summary"] == "codex validation"
    assert payload["exit_code"] == 0


def test_normalize_hook_payload_rejects_invalid_metadata() -> None:
    result = agent_hooks.normalize_hook_payload(
        {
            "workspace_id_or_uri": "file:///repo",
            "agent_name": "codex",
            "event_type": "heartbeat",
            "session_external_id": "session-1",
            "metadata": ["not", "object"],
        }
    )

    assert result["status"] == "error"
    assert result["code"] == "HOOK_PAYLOAD_INVALID"


def test_write_hook_template_is_deterministic(tmp_path: Path) -> None:
    shell = agent_hooks.write_hook_template(
        agent_name="codex",
        output_dir=tmp_path,
        template_format="shell",
    )
    json_result = agent_hooks.write_hook_template(
        agent_name="claude",
        output_dir=tmp_path,
        template_format="json",
    )

    assert shell["schema"] == "geond.agent_hook_template.v1"
    assert (tmp_path / "codex" / "record-hook.sh").exists()
    assert (tmp_path / "codex" / "README.md").exists()
    assert json_result["status"] == "ok"
    assert (tmp_path / "claude" / "hook-event.json").exists()
    assert "GEOND_WORKSPACE" in (tmp_path / "codex" / "record-hook.sh").read_text(encoding="utf-8")


def test_record_hook_event_records_activity_evidence_and_heartbeat() -> None:
    workspace_uri = f"file:///tmp/geond-agent-hook-{uuid4()}"
    conn = _connect_with_schema()
    with conn:
        run = orchestration.create_run(conn, workspace_uri, "Hook capture run")
        task = orchestration.create_task(conn, run["run"]["run_id"], "Capture hook event")
        worker = orchestration.register_worker_session(
            conn,
            run["run"]["run_id"],
            "codex",
            session_external_id="codex-session-1",
        )
        claim = orchestration.claim_task(
            conn,
            task["task"]["task_id"],
            "codex",
            worker_session_id=worker["worker_session"]["worker_session_id"],
        )

        heartbeat = agent_hooks.record_hook_event(
            conn,
            workspace_id_or_uri=workspace_uri,
            agent_name="codex",
            event_type="heartbeat",
            session_external_id="codex-session-1",
            run_id=run["run"]["run_id"],
            task_id=task["task"]["task_id"],
            worker_session_id=worker["worker_session"]["worker_session_id"],
            lease_id=claim["lease"]["lease_id"],
            idempotency_key="hook-heartbeat",
        )
        validation = agent_hooks.record_hook_event(
            conn,
            workspace_id_or_uri=workspace_uri,
            agent_name="codex",
            event_type="validation",
            session_external_id="codex-session-1",
            run_id=run["run"]["run_id"],
            task_id=task["task"]["task_id"],
            worker_session_id=worker["worker_session"]["worker_session_id"],
            command="uv run pytest tests/test_agent_hooks.py",
            exit_code=0,
            metadata={"source_detail": "pytest"},
            idempotency_key="hook-validation",
        )
        replay = agent_hooks.record_hook_event(
            conn,
            workspace_id_or_uri=workspace_uri,
            agent_name="codex",
            event_type="validation",
            session_external_id="codex-session-1",
            run_id=run["run"]["run_id"],
            task_id=task["task"]["task_id"],
            worker_session_id=worker["worker_session"]["worker_session_id"],
            command="uv run pytest tests/test_agent_hooks.py",
            exit_code=0,
            metadata={"source_detail": "pytest"},
            idempotency_key="hook-validation",
        )

        assert heartbeat["lease_renewal"]["status"] == "ok"
        assert validation["schema"] == "geond.agent_hook_event.v1"
        assert validation["hook_event"]["event_type"] == "validation"
        assert validation["action"]["action_type"] == "hook:validation"
        assert validation["command_evidence"]["command_evidence"]["status"] == "passed"
        assert replay["idempotent_replay"] is True
        assert replay["command_evidence"]["command_evidence"]["command"] == (
            "uv run pytest tests/test_agent_hooks.py"
        )

        activity = get_agent_activity_events(conn, workspace_uri, event_kind="agent_action")
        hook_events = [
            event
            for event in activity["events"]
            if event["metadata"]["metadata"].get("source") == "agent_hook"
        ]
        assert {event["metadata"]["metadata"]["event_type"] for event in hook_events} >= {
            "heartbeat",
            "validation",
        }

        with conn.cursor() as cur:
            cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
        conn.commit()
