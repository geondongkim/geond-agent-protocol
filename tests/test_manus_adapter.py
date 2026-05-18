from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from geond.adapters.manus import (
    AGENT_NAME,
    SOURCE,
    ManusApiClient,
    ManusApiError,
    ParsedManusTask,
    load_fixture,
    normalize_task,
)

FIXTURES = Path(__file__).parent / "fixtures" / "manus"


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# M1 transform tests (no DB, no network)
# ---------------------------------------------------------------------------


def test_normalize_completed_task() -> None:
    detail = _load_json(FIXTURES / "task_detail_completed.json")
    messages = _load_json(FIXTURES / "task_messages_completed.json")

    task = normalize_task(detail, messages)

    assert isinstance(task, ParsedManusTask)
    assert task.task_id == "manus-task-abc123"
    assert task.title == "Refactor authentication middleware"
    assert task.status == "completed"
    assert task.share_visibility == "public"
    assert task.task_url == "https://manus.ai/tasks/manus-task-abc123"
    assert task.share_url == "https://manus.ai/share/manus-task-abc123-pub"
    assert len(task.messages) == 5
    assert task.messages[0].role == "user"
    assert task.messages[1].role == "assistant"
    assert "JWT" in task.messages[3].content


def test_normalize_failed_task() -> None:
    detail = _load_json(FIXTURES / "task_detail_failed.json")
    messages = _load_json(FIXTURES / "task_messages_failed.json")

    task = normalize_task(detail, messages)

    assert task.task_id == "manus-task-def456"
    assert task.status == "failed"
    assert task.share_visibility == "private"
    assert len(task.messages) == 4
    assert task.messages[-1].role == "assistant"
    assert "timeout" in task.messages[-1].content.lower()


def test_normalize_missing_optional_fields_does_not_crash() -> None:
    minimal = {"task_id": "task-min-001"}
    task = normalize_task(minimal)

    assert task.task_id == "task-min-001"
    assert task.title == "task-min-001"
    assert task.status == "unknown"
    assert task.task_url is None
    assert task.share_url is None
    assert task.share_visibility == "private"
    assert task.messages == []


def test_normalize_unknown_future_fields_stored_in_metadata() -> None:
    detail = {"task_id": "task-x", "task_title": "T", "future_field": "value"}
    task = normalize_task(detail)

    assert task.metadata.get("future_field") == "value"


def test_message_roles_normalized() -> None:
    detail = {"task_id": "task-r"}
    messages_data = {
        "task_id": "task-r",
        "messages": [
            {"id": "m1", "role": "user", "content": "hi", "created_at": None},
            {"id": "m2", "role": "assistant", "content": "hello", "created_at": None},
            {"id": "m3", "role": "tool", "content": "result", "created_at": None},
            {"id": "m4", "role": "UNKNOWN_ROLE", "content": "?", "created_at": None},
        ],
    }
    task = normalize_task(detail, messages_data)
    roles = [m.role for m in task.messages]
    assert roles == ["user", "assistant", "assistant_or_tool", "assistant_or_tool"]


def test_source_id_is_composited_string_not_hash() -> None:
    detail = {"task_id": "task-s"}
    messages_data = {
        "task_id": "task-s",
        "messages": [{"id": "m1", "role": "user", "content": "test", "created_at": None}],
    }
    task = normalize_task(detail, messages_data)
    msg = task.messages[0]
    source_id = f"manus:{task.task_id}:{msg.ordinal}"
    assert source_id == "manus:task-s:1"
    assert not source_id.startswith(("sha", "hash"))


def test_load_fixture_completed() -> None:
    task = load_fixture(
        str(FIXTURES / "task_detail_completed.json"),
        str(FIXTURES / "task_messages_completed.json"),
    )
    assert task.task_id == "manus-task-abc123"
    assert len(task.messages) == 5


def test_load_fixture_no_messages_file() -> None:
    task = load_fixture(str(FIXTURES / "task_detail_completed.json"))
    assert task.task_id == "manus-task-abc123"
    assert task.messages == []


# ---------------------------------------------------------------------------
# Redaction tests
# ---------------------------------------------------------------------------


def test_api_key_in_message_is_redacted_before_storage() -> None:
    from geond.redaction import redact_text

    content = "Token: sk-1234567890abcdefghij"
    redacted, findings = redact_text(content)
    assert "sk-1234567890abcdefghij" not in redacted
    assert findings


def test_connector_ids_treated_as_metadata_only() -> None:
    detail = {
        "task_id": "task-c",
        "task_title": "T",
        "connectors": ["conn-secret-abc", "conn-secret-def"],
    }
    task = normalize_task(detail)
    assert task.connector_ids == ["conn-secret-abc", "conn-secret-def"]


# ---------------------------------------------------------------------------
# ManusApiClient error mapping (no real network)
# ---------------------------------------------------------------------------


def test_api_client_raises_manus_api_error_on_404() -> None:
    import urllib.error

    client = ManusApiClient(api_key="fake-key")
    mock_exc = urllib.error.HTTPError(
        url="https://api.manus.im/v1/tasks/bad",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=None,
    )
    mock_exc.read = lambda: b"task not found"
    with patch("urllib.request.urlopen", side_effect=mock_exc):
        with pytest.raises(ManusApiError) as exc_info:
            client.get_task("bad")
    assert exc_info.value.status_code == 404
    assert "not_found" in str(exc_info.value)


def test_api_client_raises_manus_api_error_on_403() -> None:
    import urllib.error

    client = ManusApiClient(api_key="fake-key")
    mock_exc = urllib.error.HTTPError(
        url="https://api.manus.im/v1/tasks/x",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=None,
    )
    mock_exc.read = lambda: b""
    with patch("urllib.request.urlopen", side_effect=mock_exc):
        with pytest.raises(ManusApiError) as exc_info:
            client.get_task("x")
    assert exc_info.value.status_code == 403
    assert "permission_denied" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Storage: idempotent re-import (integration, skipped without Postgres)
# ---------------------------------------------------------------------------


def test_store_manus_task_idempotent() -> None:
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
    workspace_uri = f"file:///tmp/geond-manus-idempotent-{uuid4()}"

    try:
        conn = connect(settings)
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")

    from geond.storage.repository import store_manus_task, upsert_workspace

    with conn:
        try:
            run_schema_file(conn, schema)
        except psycopg.Error as exc:
            pytest.skip(f"Schema unavailable: {exc}")

        workspace_id = upsert_workspace(conn, root_uri=workspace_uri, name="manus-idempotent-test")
        try:
            task = load_fixture(
                str(FIXTURES / "task_detail_completed.json"),
                str(FIXTURES / "task_messages_completed.json"),
            )

            session_id_1 = store_manus_task(conn, workspace_id, task)
            session_id_2 = store_manus_task(conn, workspace_id, task)

            assert session_id_1 == session_id_2

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = %s::uuid",
                    (session_id_1,),
                )
                count = cur.fetchone()[0]
            assert count == len(task.messages)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM agent_actions aa
                    JOIN agents a ON a.id = aa.agent_id
                    WHERE aa.session_id = %s::uuid
                      AND aa.action_type = 'task_observed'
                      AND a.name = %s
                    """,
                    (session_id_1, AGENT_NAME),
                )
                action_count = cur.fetchone()[0]
            assert action_count == 1

        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()


# ---------------------------------------------------------------------------
# Storage: dashboard shows Manus (integration)
# ---------------------------------------------------------------------------


def test_dashboard_shows_manus_agent() -> None:
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
    workspace_uri = f"file:///tmp/geond-manus-dashboard-{uuid4()}"

    try:
        conn = connect(settings)
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")

    from geond.storage.dashboard import get_agent_activity_events, get_dashboard_overview
    from geond.storage.repository import store_manus_task, upsert_workspace

    with conn:
        try:
            run_schema_file(conn, schema)
        except psycopg.Error as exc:
            pytest.skip(f"Schema unavailable: {exc}")

        workspace_id = upsert_workspace(conn, root_uri=workspace_uri, name="manus-dashboard-test")
        try:
            task = load_fixture(
                str(FIXTURES / "task_detail_completed.json"),
                str(FIXTURES / "task_messages_completed.json"),
            )
            store_manus_task(conn, workspace_id, task)

            overview = get_dashboard_overview(conn, workspace_id, limit=10)
            assert overview["status"] == "ok"
            assert overview["counts"]["sessions"] >= 1

            activity = get_agent_activity_events(conn, workspace_uri, limit=20)
            agent_names = {e.get("agent_name") for e in activity["events"]}
            sources = {e.get("source") for e in activity["events"]}
            assert "Manus" in agent_names or SOURCE in sources

        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()


# ---------------------------------------------------------------------------
# CLI dry-run writes nothing (integration)
# ---------------------------------------------------------------------------


def test_cli_dry_run_writes_nothing(tmp_path) -> None:
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
    workspace_uri = f"file:///tmp/geond-manus-dryrun-{uuid4()}"

    try:
        conn = connect(settings)
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")

    from geond.storage.repository import upsert_workspace

    with conn:
        try:
            run_schema_file(conn, schema)
        except psycopg.Error as exc:
            pytest.skip(f"Schema unavailable: {exc}")

        workspace_id = upsert_workspace(conn, root_uri=workspace_uri, name="manus-dryrun-test")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM sessions WHERE workspace_id = %s", (workspace_id,)
                )
                before = cur.fetchone()[0]

            import sys
            from io import StringIO
            from unittest.mock import patch as _patch

            from geond.cli import main

            captured = StringIO()
            with _patch.object(
                sys,
                "argv",
                [
                    "geond",
                    "import-manus-task",
                    "--fixture",
                    str(FIXTURES / "task_detail_completed.json"),
                    "--fixture-messages",
                    str(FIXTURES / "task_messages_completed.json"),
                    "--workspace-uri",
                    workspace_uri,
                    "--dry-run",
                ],
            ):
                with _patch("sys.stdout", captured):
                    main()

            output = json.loads(captured.getvalue())
            assert output["status"] == "dry-run"

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM sessions WHERE workspace_id = %s", (workspace_id,)
                )
                after = cur.fetchone()[0]
            assert after == before

        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
            conn.commit()
