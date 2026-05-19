from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from geond.adapters.manus import (
    AGENT_NAME,
    BLOCKED_STATUSES,
    SOURCE,
    ManusApiClient,
    ManusApiError,
    ParsedManusFile,
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
    # 5 messages total: user + status_update + 3 assistant
    assert len(task.messages) == 5
    assert task.messages[0].role == "user"
    assert task.messages[1].role == "assistant_or_tool"  # status_update
    assert task.messages[2].role == "assistant"
    assert "JWT" in task.messages[3].content
    # timestamps converted from Unix ms to ISO
    assert task.messages[0].created_at is not None
    assert "T" in (task.messages[0].created_at or "")


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
    detail = {"id": "task-x", "title": "T", "future_field": "value"}
    task = normalize_task(detail)

    assert task.metadata.get("future_field") == "value"


def test_message_roles_normalized() -> None:
    detail = {"id": "task-r"}
    messages_data = {
        "task_id": "task-r",
        "messages": [
            {
                "id": "m1",
                "type": "user_message",
                "timestamp": None,
                "user_message": {"content": "hi", "message_type": "text"},
            },
            {
                "id": "m2",
                "type": "assistant_message",
                "timestamp": None,
                "assistant_message": {"content": "hello"},
            },
            {
                "id": "m3",
                "type": "status_update",
                "timestamp": None,
                "status_update": {"agent_status": "running", "brief": "Working"},
            },
        ],
    }
    task = normalize_task(detail, messages_data)
    roles = [m.role for m in task.messages]
    assert roles == ["user", "assistant", "assistant_or_tool"]


def test_source_id_is_composited_string_not_hash() -> None:
    detail = {"id": "task-s"}
    messages_data = {
        "task_id": "task-s",
        "messages": [
            {
                "id": "m1",
                "type": "user_message",
                "timestamp": None,
                "user_message": {"content": "test", "message_type": "text"},
            },
        ],
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
        "id": "task-c",
        "title": "T",
        "connectors": ["conn-secret-abc", "conn-secret-def"],
    }
    task = normalize_task(detail)
    assert task.connector_ids == ["conn-secret-abc", "conn-secret-def"]


def test_normalize_current_api_waiting_status_is_blocked() -> None:
    task = normalize_task({"id": "task-wait", "title": "Waiting", "status": "waiting"})

    assert task.is_blocked is True


def test_normalize_error_message_and_attachments_from_current_api_shape() -> None:
    task = normalize_task(
        {"id": "task-current", "title": "Current API", "status": "error"},
        {
            "messages": [
                {
                    "id": "msg-err",
                    "type": "error_message",
                    "timestamp": 1_715_000_000,
                    "error_message": {
                        "error_type": "tool_failed",
                        "content": "Browser automation timed out",
                    },
                },
                {
                    "id": "msg-assistant",
                    "type": "assistant_message",
                    "timestamp": 1_715_000_010,
                    "assistant_message": {
                        "content": "I attached the report.",
                        "attachments": [
                            {
                                "filename": "report.pdf",
                                "url": "https://manus.example/private/report.pdf",
                                "content_type": "application/pdf",
                            }
                        ],
                    },
                },
            ]
        },
    )

    assert task.messages[0].role == "assistant_or_tool"
    assert "timed out" in task.messages[0].content
    assert len(task.files) == 1
    assert task.files[0].name == "report.pdf"
    assert task.files[0].mime_type == "application/pdf"
    assert task.files[0].metadata["source_message_id"] == "msg-assistant"


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


def test_api_client_list_tasks_accepts_current_data_response() -> None:
    from unittest.mock import MagicMock

    client = ManusApiClient(api_key="fake-key")
    response = MagicMock()
    response.read.return_value = json.dumps(
        {
            "ok": True,
            "data": [
                {"id": "task-a", "title": "A", "status": "stopped"},
                {"id": "task-b", "title": "B", "status": "waiting"},
            ],
        }
    ).encode("utf-8")
    response.__enter__ = lambda s: s
    response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=response):
        result = client.list_tasks(status="waiting")

    assert result["tasks"] == [{"id": "task-b", "title": "B", "status": "waiting"}]


def test_api_client_create_task_uses_current_message_body() -> None:
    from unittest.mock import MagicMock

    client = ManusApiClient(api_key="fake-key")
    response = MagicMock()
    response.read.return_value = json.dumps(
        {"ok": True, "task_id": "task-new", "task_title": "Generated title"}
    ).encode("utf-8")
    response.__enter__ = lambda s: s
    response.__exit__ = MagicMock(return_value=False)

    captured = {}

    def fake_urlopen(req, timeout):
        captured["request"] = req
        captured["timeout"] = timeout
        return response

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = client.create_task("Local title", "Use Geond context")

    body = json.loads(captured["request"].data.decode("utf-8"))
    assert body == {
        "message": {"content": [{"type": "text", "text": "Use Geond context"}]},
        "title": "Local title",
    }
    assert result["task_id"] == "task-new"


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


def test_cli_import_manus_task_accepts_positional_task_id(capsys) -> None:
    import sys
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    mock_task = normalize_task(
        {"id": "task-positional", "title": "Positional", "status": "stopped"}
    )
    mock_client = MagicMock()
    mock_client.fetch_task.return_value = mock_task

    with _patch.object(
        sys,
        "argv",
        ["geond", "import-manus-task", "task-positional", "--dry-run"],
    ):
        with _patch("geond.cli.ManusApiClient", return_value=mock_client):
            from geond.cli import main

            main()

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] == "dry-run"
    assert parsed["planned"]["task_id"] == "task-positional"
    mock_client.fetch_task.assert_called_once_with("task-positional")


# ---------------------------------------------------------------------------
# context packet (unit — no DB)
# ---------------------------------------------------------------------------


def test_context_packet_to_prompt_has_no_secrets() -> None:
    from geond.cli import _context_packet_to_prompt

    packet = {
        "schema": "geond.context_packet.v1",
        "workspace_uri": "file:///tmp/ws",
        "query": "auth refactor",
        "open_handoffs": [
            {
                "handoff_id": "hid-1",
                "from_agent": "Codex",
                "to_agent": "Manus",
                "summary": "Refactor auth module",
                "next_steps": ["run tests", "deploy"],
                "blocked_on": [],
            }
        ],
        "active_file_reservations": [
            {
                "file_path": "src/auth.py",
                "agent": "Codex",
                "purpose": "refactor",
                "expires_at": None,
            }
        ],
        "active_symbol_reservations": [],
        "recent_activity": [],
        "search_results": [
            {
                "source": "codex",
                "session_title": "Auth sprint",
                "role": "assistant",
                "ordinal": 3,
                "excerpt": "JWT middleware rewrite",
                "evidence_ref": "geond:codex:sess-001:3",
            }
        ],
        "assessment": None,
        "recommendations": ["No blocking reservations"],
    }

    prompt = _context_packet_to_prompt(packet)

    assert "## Geond Context Packet" in prompt
    assert "auth refactor" in prompt
    assert "Codex → Manus" in prompt
    assert "src/auth.py" in prompt
    assert "geond:codex:sess-001:3" in prompt
    assert "JWT middleware rewrite" in prompt
    assert "No blocking reservations" in prompt
    # No secrets in prompt
    assert "sk-" not in prompt
    assert "MANUS_API_KEY" not in prompt


def test_context_packet_to_prompt_empty_sections_omitted() -> None:
    from geond.cli import _context_packet_to_prompt

    packet = {
        "workspace_uri": "file:///tmp/ws",
        "query": "",
        "open_handoffs": [],
        "active_file_reservations": [],
        "active_symbol_reservations": [],
        "recent_activity": [],
        "search_results": [],
        "assessment": None,
        "recommendations": [],
    }
    prompt = _context_packet_to_prompt(packet)
    assert "Open Handoffs" not in prompt
    assert "File Reservations" not in prompt
    assert "Relevant Prior Sessions" not in prompt


# ---------------------------------------------------------------------------
# CLI manus-context-packet integration (skipped without Postgres)
# ---------------------------------------------------------------------------


def test_cli_manus_context_packet_outputs_json(tmp_path) -> None:
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
    workspace_uri = f"file:///tmp/geond-manus-ctxpkt-{uuid4()}"

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

        upsert_workspace(conn, root_uri=workspace_uri, name="manus-ctxpkt-test")
        try:
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
                    "manus-context-packet",
                    "--workspace-uri",
                    workspace_uri,
                    "--query",
                    "auth refactor",
                ],
            ):
                with _patch("sys.stdout", captured):
                    main()

            output = json.loads(captured.getvalue())
            assert output["schema"] == "geond.context_packet.v1"
            assert output["workspace_uri"] == workspace_uri
            assert output["query"] == "auth refactor"
            assert "open_handoffs" in output
            assert "active_file_reservations" in output
            assert "active_symbol_reservations" in output
            assert "search_results" in output

        finally:
            with connect(settings) as cleanup_conn:
                with cleanup_conn.cursor() as cur:
                    cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
                cleanup_conn.commit()


# ---------------------------------------------------------------------------
# contract helpers (unit — no DB)
# ---------------------------------------------------------------------------


def test_contract_to_prompt_has_intent_and_files() -> None:
    from geond.cli import _build_task_contract, _contract_to_prompt

    start_result = {
        "workspace_id": "ws-001",
        "agent_name": "Manus",
        "dry_run": True,
        "action_id": None,
        "requested": {"files": ["src/auth.py"], "symbols": ["require_auth"]},
        "conflicts": {"file_reservations": [], "symbol_reservations": []},
        "reservations": {"files": {}, "symbols": {}},
        "review": {
            "workspace_uri": "file:///tmp/ws",
            "recommendations": ["No blocking reservations found."],
        },
    }
    contract = _build_task_contract(
        start_result,
        intent="Refactor auth middleware to use JWT",
        expected_outputs=["updated src/auth.py", "passing tests"],
        validation_commands=["pytest tests/test_auth.py"],
    )
    assert contract["schema"] == "geond.manus_task_contract.v1"
    assert contract["intent"] == "Refactor auth middleware to use JWT"
    assert "src/auth.py" in contract["files"]
    assert "require_auth" in contract["symbols"]
    assert "updated src/auth.py" in contract["expected_outputs"]
    assert "pytest tests/test_auth.py" in contract["validation_commands"]
    assert contract["dry_run"] is True

    prompt = _contract_to_prompt(contract)
    assert "## Geond Task Contract" in prompt
    assert "Refactor auth middleware to use JWT" in prompt
    assert "src/auth.py" in prompt
    assert "require_auth" in prompt
    assert "pytest tests/test_auth.py" in prompt
    assert "No blocking reservations" in prompt
    assert "sk-" not in prompt


def test_contract_to_prompt_shows_conflicts() -> None:
    from geond.cli import _build_task_contract, _contract_to_prompt

    start_result = {
        "workspace_id": "ws-002",
        "agent_name": "Manus",
        "dry_run": True,
        "action_id": None,
        "requested": {"files": ["src/auth.py"], "symbols": []},
        "conflicts": {
            "file_reservations": [
                {
                    "file_path": "src/auth.py",
                    "agent_name": "Codex",
                    "purpose": "ongoing refactor",
                }
            ],
            "symbol_reservations": [],
        },
        "reservations": {"files": {}, "symbols": {}},
        "review": {"workspace_uri": "file:///tmp/ws", "recommendations": []},
    }
    contract = _build_task_contract(
        start_result,
        intent="Add OAuth provider",
        expected_outputs=[],
        validation_commands=[],
    )
    prompt = _contract_to_prompt(contract)
    assert "Active Conflicts" in prompt
    assert "src/auth.py" in prompt
    assert "Codex" in prompt


# ---------------------------------------------------------------------------
# manus-task-contract CLI integration (skipped without Postgres)
# ---------------------------------------------------------------------------


def test_cli_manus_task_contract_dry_run() -> None:
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
    workspace_uri = f"file:///tmp/geond-manus-contract-{uuid4()}"

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

        upsert_workspace(conn, root_uri=workspace_uri, name="manus-contract-test")
        try:
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
                    "manus-task-contract",
                    "--workspace-uri",
                    workspace_uri,
                    "--intent",
                    "Refactor auth middleware",
                    "--file",
                    "src/auth.py",
                    "--expected-output",
                    "passing tests",
                    "--dry-run",
                ],
            ):
                with _patch("sys.stdout", captured):
                    main()

            output = json.loads(captured.getvalue())
            assert output["schema"] == "geond.manus_task_contract.v1"
            assert output["intent"] == "Refactor auth middleware"
            assert "src/auth.py" in output["files"]
            assert "passing tests" in output["expected_outputs"]
            assert output["dry_run"] is True
            assert output["action_id"] is None

        finally:
            with connect(settings) as cleanup_conn:
                with cleanup_conn.cursor() as cur:
                    cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
                cleanup_conn.commit()


# ---------------------------------------------------------------------------
# manus-task-complete CLI integration (skipped without Postgres)
# ---------------------------------------------------------------------------


def test_cli_manus_task_complete_with_fixture() -> None:
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
    workspace_uri = f"file:///tmp/geond-manus-complete-{uuid4()}"

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

        upsert_workspace(conn, root_uri=workspace_uri, name="manus-complete-test")
        try:
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
                    "manus-task-complete",
                    "--fixture",
                    str(FIXTURES / "task_detail_completed.json"),
                    "--fixture-messages",
                    str(FIXTURES / "task_messages_completed.json"),
                    "--workspace-uri",
                    workspace_uri,
                    "--handoff-summary",
                    "JWT refactor complete",
                    "--next-step",
                    "deploy to staging",
                    "--tested-command",
                    "pytest tests/",
                    "--reservation-mode",
                    "release",
                ],
            ):
                with _patch("sys.stdout", captured):
                    main()

            output = json.loads(captured.getvalue())
            assert output["status"] == "ok"
            assert output["task_id"] == "manus-task-abc123"
            assert output["imported_messages"] == 5
            assert output["finish"]["status"] == "ok"
            assert output["finish"]["command"] == "finish-task"

        finally:
            with connect(settings) as cleanup_conn:
                with cleanup_conn.cursor() as cur:
                    cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
                cleanup_conn.commit()


# ---------------------------------------------------------------------------
# Search: imported Manus task content is findable (integration)
# ---------------------------------------------------------------------------


def test_search_finds_imported_manus_task_content() -> None:
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
    workspace_uri = f"file:///tmp/geond-manus-search-{uuid4()}"

    try:
        conn = connect(settings)
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")

    from geond.retrieval.simple import search_dev_memory
    from geond.storage.repository import store_manus_task, upsert_workspace

    with conn:
        try:
            run_schema_file(conn, schema)
        except psycopg.Error as exc:
            pytest.skip(f"Schema unavailable: {exc}")

        workspace_id = upsert_workspace(conn, root_uri=workspace_uri, name="manus-search-test")
        try:
            task = load_fixture(
                str(FIXTURES / "task_detail_completed.json"),
                str(FIXTURES / "task_messages_completed.json"),
            )
            store_manus_task(conn, workspace_id, task)

            results = search_dev_memory(conn, query="JWT middleware", workspace_uri=workspace_uri)
            assert len(results) > 0, "Expected search results for 'JWT middleware'"
            sources = {r.get("source") for r in results}
            assert SOURCE in sources, f"Expected source={SOURCE!r} in results, got {sources}"
            contents = " ".join(r.get("snippet") or "" for r in results)
            assert "JWT" in contents or "jwt" in contents.lower()

        finally:
            with connect(settings) as cleanup_conn:
                with cleanup_conn.cursor() as cur:
                    cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
                cleanup_conn.commit()


# ---------------------------------------------------------------------------
# ParsedManusFile + _normalize_files (unit -- no DB, no network)
# ---------------------------------------------------------------------------


def test_normalize_task_with_files() -> None:
    detail = {"id": "task-f1", "title": "Task with files"}
    files_data = {
        "files": [
            {
                "id": "file-001",
                "name": "report.pdf",
                "mime_type": "application/pdf",
                "size": 12345,
                "created_at": "2024-01-01T12:00:00Z",
            },
            {
                "id": "file-002",
                "name": "data.csv",
                "mime_type": "text/csv",
                "size": 512,
                "created_at": None,
            },
        ]
    }
    task = normalize_task(detail, task_files=files_data)

    assert len(task.files) == 2
    f = task.files[0]
    assert isinstance(f, ParsedManusFile)
    assert f.file_id == "file-001"
    assert f.name == "report.pdf"
    assert f.mime_type == "application/pdf"
    assert f.size_bytes == 12345
    assert f.created_at == "2024-01-01T12:00:00Z"

    f2 = task.files[1]
    assert f2.file_id == "file-002"
    assert f2.size_bytes == 512
    assert f2.created_at is None


def test_normalize_files_missing_file_id_skipped() -> None:
    detail = {"id": "task-f2"}
    files_data = {
        "files": [
            {"name": "no-id.txt"},
            {"id": "file-ok", "name": "present.txt"},
        ]
    }
    task = normalize_task(detail, task_files=files_data)
    assert len(task.files) == 1
    assert task.files[0].file_id == "file-ok"


def test_normalize_files_size_string_coerced_to_int() -> None:
    detail = {"id": "task-f3"}
    files_data = {"files": [{"id": "file-sz", "size": "9999"}]}
    task = normalize_task(detail, task_files=files_data)
    assert task.files[0].size_bytes == 9999


def test_normalize_files_bad_size_becomes_none() -> None:
    detail = {"id": "task-f4"}
    files_data = {"files": [{"id": "file-bad", "size": "not-a-number"}]}
    task = normalize_task(detail, task_files=files_data)
    assert task.files[0].size_bytes is None


def test_load_fixture_with_files_path(tmp_path) -> None:
    import json as _json

    detail_file = tmp_path / "detail.json"
    files_file = tmp_path / "files.json"

    detail_file.write_text(_json.dumps({"id": "task-fx", "title": "Fixture with files"}))
    files_file.write_text(_json.dumps({"files": [{"id": "file-fx-001", "name": "output.txt"}]}))

    task = load_fixture(str(detail_file), files_path=str(files_file))
    assert task.task_id == "task-fx"
    assert len(task.files) == 1
    assert task.files[0].file_id == "file-fx-001"


def test_load_fixture_without_files_path() -> None:
    task = load_fixture(str(FIXTURES / "task_detail_completed.json"))
    assert task.files == []


# ---------------------------------------------------------------------------
# ManusApiClient.list_task_files -- 404 returns empty (mock HTTP)
# ---------------------------------------------------------------------------


def test_api_client_list_task_files_404_returns_empty() -> None:
    import urllib.error

    client = ManusApiClient(api_key="fake-key")
    mock_exc = urllib.error.HTTPError(
        url="https://api.manus.ai/v2/task.listFiles",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=None,
    )
    mock_exc.read = lambda: b"not found"
    with patch("urllib.request.urlopen", side_effect=mock_exc):
        result = client.list_task_files("task-missing")
    assert result == {"files": [], "task_id": "task-missing"}


def test_api_client_list_task_files_other_errors_propagate() -> None:
    import urllib.error

    client = ManusApiClient(api_key="fake-key")
    mock_exc = urllib.error.HTTPError(
        url="https://api.manus.ai/v2/task.listFiles",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=None,
    )
    mock_exc.read = lambda: b""
    with patch("urllib.request.urlopen", side_effect=mock_exc):
        with pytest.raises(ManusApiError) as exc_info:
            client.list_task_files("task-x")
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# CLI: list-manus-tasks (mock API, no DB)
# ---------------------------------------------------------------------------


def test_cli_list_manus_tasks_table_format(capsys) -> None:
    import sys
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    mock_tasks = {
        "tasks": [
            {"id": "t1", "title": "Task Alpha", "status": "completed", "created_at": None},
            {"id": "t2", "title": "Task Beta", "status": "running", "created_at": None},
        ]
    }
    mock_client = MagicMock()
    mock_client.list_tasks.return_value = mock_tasks

    with _patch.object(sys, "argv", ["geond", "list-manus-tasks", "--limit", "5"]):
        with _patch("geond.cli.ManusApiClient", return_value=mock_client):
            from geond.cli import main

            main()

    out = capsys.readouterr().out
    assert "t1" in out
    assert "Task Alpha" in out
    assert "completed" in out
    assert "t2" in out


def test_cli_list_manus_tasks_json_format(capsys) -> None:
    import json as _json
    import sys
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    mock_tasks = {
        "tasks": [
            {"id": "t3", "title": "Task Gamma", "status": "failed", "created_at": None},
        ]
    }
    mock_client = MagicMock()
    mock_client.list_tasks.return_value = mock_tasks

    with _patch.object(sys, "argv", ["geond", "list-manus-tasks", "--format", "json"]):
        with _patch("geond.cli.ManusApiClient", return_value=mock_client):
            from geond.cli import main

            main()

    out = capsys.readouterr().out
    parsed = _json.loads(out)
    assert parsed["count"] == 1
    assert parsed["tasks"][0]["id"] == "t3"


def test_cli_list_manus_tasks_status_filter(capsys) -> None:
    import sys
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    mock_client = MagicMock()
    mock_client.list_tasks.return_value = {"tasks": []}

    with _patch.object(sys, "argv", ["geond", "list-manus-tasks", "--status", "completed"]):
        with _patch("geond.cli.ManusApiClient", return_value=mock_client):
            from geond.cli import main

            main()

    mock_client.list_tasks.assert_called_once_with(limit=20, status="completed")


# ---------------------------------------------------------------------------
# CLI: import-manus-tasks dry-run (mock API, no DB)
# ---------------------------------------------------------------------------


def test_cli_import_manus_tasks_dry_run(capsys) -> None:
    import json as _json
    import sys
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    mock_list = {
        "tasks": [
            {"id": "t-dry-1", "title": "Dry Task", "status": "completed"},
        ]
    }
    mock_fetched = normalize_task({"id": "t-dry-1", "title": "Dry Task", "status": "completed"})

    mock_client = MagicMock()
    mock_client.list_tasks.return_value = mock_list
    mock_client.fetch_task.return_value = mock_fetched

    with _patch.object(
        sys,
        "argv",
        [
            "geond",
            "import-manus-tasks",
            "--workspace-uri",
            "file:///tmp/test-dry-ws",
            "--dry-run",
        ],
    ):
        with _patch("geond.cli.ManusApiClient", return_value=mock_client):
            from geond.cli import main

            main()

    out = capsys.readouterr().out
    parsed = _json.loads(out)
    assert parsed["status"] == "dry-run"
    assert parsed["count"] == 1
    assert isinstance(parsed["tasks"], list)


def test_cli_import_manus_tasks_no_workspace_uri_exits() -> None:
    import sys
    from unittest.mock import patch as _patch

    with _patch.object(sys, "argv", ["geond", "import-manus-tasks"]):
        with pytest.raises(SystemExit):
            from geond.cli import main

            main()


# ---------------------------------------------------------------------------
# Storage: store_manus_task with file artifacts (integration, skipped without PG)
# ---------------------------------------------------------------------------


def test_store_manus_task_with_files() -> None:
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
    workspace_uri = f"file:///tmp/geond-manus-files-{uuid4()}"

    try:
        conn = connect(settings)
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")

    from geond.storage.repository import store_manus_task, upsert_workspace

    task = normalize_task(
        {"id": "task-with-files", "title": "Files test"},
        task_files={
            "files": [
                {"id": "f1", "name": "out.pdf", "mime_type": "application/pdf", "size": 100},
                {"id": "f2", "name": "log.txt", "size": 50},
            ]
        },
    )

    with conn:
        try:
            run_schema_file(conn, schema)
        except psycopg.Error as exc:
            pytest.skip(f"Schema unavailable: {exc}")

        workspace_id = upsert_workspace(conn, root_uri=workspace_uri, name="files-test")
        try:
            store_manus_task(conn, workspace_id, task)

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT file_uri FROM file_snapshots WHERE workspace_id = %s ORDER BY file_uri",
                    (workspace_id,),
                )
                rows = cur.fetchall()
            uris = [r[0] for r in rows]
            assert "manus://task-with-files/files/f1" in uris
            assert "manus://task-with-files/files/f2" in uris

        finally:
            with connect(settings) as cleanup_conn:
                with cleanup_conn.cursor() as cur:
                    cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
                cleanup_conn.commit()


def test_store_manus_task_files_idempotent() -> None:
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
    workspace_uri = f"file:///tmp/geond-manus-files-idem-{uuid4()}"

    try:
        conn = connect(settings)
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")

    from geond.storage.repository import store_manus_task, upsert_workspace

    task = normalize_task(
        {"id": "task-idem-files", "title": "Idempotent files"},
        task_files={"files": [{"id": "fid-1", "name": "unique.txt"}]},
    )

    with conn:
        try:
            run_schema_file(conn, schema)
        except psycopg.Error as exc:
            pytest.skip(f"Schema unavailable: {exc}")

        workspace_id = upsert_workspace(conn, root_uri=workspace_uri, name="idem-files-test")
        try:
            store_manus_task(conn, workspace_id, task)
            store_manus_task(conn, workspace_id, task)

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM file_snapshots WHERE workspace_id = %s",
                    (workspace_id,),
                )
                count = cur.fetchone()[0]
            assert count == 1

        finally:
            with connect(settings) as cleanup_conn:
                with cleanup_conn.cursor() as cur:
                    cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
                cleanup_conn.commit()


# ---------------------------------------------------------------------------
# Webhook: _handle_manus_webhook (unit -- no real DB or network)
# ---------------------------------------------------------------------------


def _make_webhook_body(event_type: str, task_id: str = "wh-task-1") -> bytes:
    import json as _json

    payload = {
        "event": event_type,
        "workspace_uri": "file:///tmp/webhook-ws",
        "task": {"id": task_id, "title": "Webhook task", "status": "completed"},
        "messages": None,
    }
    return _json.dumps(payload).encode("utf-8")


def _make_signature(secret: str, body: bytes) -> str:
    import hashlib
    import hmac as _hmac

    return "sha256=" + _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_ignored_event_type() -> None:
    from unittest.mock import MagicMock

    from geond.config import Settings
    from geond.dashboard_server import _handle_manus_webhook

    body = _make_webhook_body("task.created")
    headers = MagicMock()
    headers.get = lambda k, d=None: str(len(body)) if k == "Content-Length" else d
    rfile = MagicMock()
    rfile.read = lambda n: body

    settings = Settings(manus_webhook_secret="")
    status, resp = _handle_manus_webhook(settings, headers, rfile)

    assert status == 200
    assert resp["status"] == "ignored"
    assert resp["event"] == "task.created"


def test_webhook_missing_workspace_uri_returns_400() -> None:
    import json as _json
    from unittest.mock import MagicMock

    from geond.config import Settings
    from geond.dashboard_server import _handle_manus_webhook

    body = _json.dumps({"event": "task.completed", "task": {"id": "t1"}}).encode("utf-8")
    headers = MagicMock()
    headers.get = lambda k, d=None: str(len(body)) if k == "Content-Length" else d
    rfile = MagicMock()
    rfile.read = lambda n: body

    settings = Settings(manus_webhook_secret="")
    status, resp = _handle_manus_webhook(settings, headers, rfile)

    assert status == 400
    assert resp["message"] == "missing_workspace_uri"


def test_webhook_invalid_json_returns_400() -> None:
    from unittest.mock import MagicMock

    from geond.config import Settings
    from geond.dashboard_server import _handle_manus_webhook

    body = b"not-json"
    headers = MagicMock()
    headers.get = lambda k, d=None: str(len(body)) if k == "Content-Length" else d
    rfile = MagicMock()
    rfile.read = lambda n: body

    settings = Settings(manus_webhook_secret="")
    status, resp = _handle_manus_webhook(settings, headers, rfile)

    assert status == 400
    assert resp["message"] == "invalid_json"


def test_webhook_invalid_signature_rejected() -> None:
    from unittest.mock import MagicMock

    from geond.config import Settings
    from geond.dashboard_server import _handle_manus_webhook

    body = _make_webhook_body("task.completed")
    wrong_sig = "sha256=deadbeef"

    def mock_get(k, d=None):
        if k == "Content-Length":
            return str(len(body))
        if k in {"x-manus-signature", "X-Manus-Signature"}:
            return wrong_sig
        return d

    headers = MagicMock()
    headers.get = mock_get
    rfile = MagicMock()
    rfile.read = lambda n: body

    settings = Settings(manus_webhook_secret="correct-secret")
    status, resp = _handle_manus_webhook(settings, headers, rfile)

    assert status == 401
    assert resp["message"] == "invalid_signature"


def test_webhook_valid_signature_accepted_and_stored() -> None:
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    from geond.config import Settings
    from geond.dashboard_server import _handle_manus_webhook

    secret = "test-secret-abc"
    body = _make_webhook_body("task.completed", task_id="wh-stored-1")
    sig = _make_signature(secret, body)

    def mock_get(k, d=None):
        if k == "Content-Length":
            return str(len(body))
        if k in {"x-manus-signature", "X-Manus-Signature"}:
            return sig
        return d

    headers = MagicMock()
    headers.get = mock_get
    rfile = MagicMock()
    rfile.read = lambda n: body

    settings = Settings(manus_webhook_secret=secret)
    fake_conn = MagicMock()
    fake_conn.__enter__ = lambda s: s
    fake_conn.__exit__ = MagicMock(return_value=False)

    with _patch("geond.dashboard_server.connect", return_value=fake_conn):
        with _patch("geond.storage.repository.upsert_workspace", return_value="ws-uuid"):
            with _patch("geond.storage.repository.store_manus_task", return_value="sess-uuid"):
                status, resp = _handle_manus_webhook(settings, headers, rfile)

    assert status == 200
    assert resp["status"] == "ok"
    assert resp["task_id"] == "wh-stored-1"


def test_webhook_no_secret_skips_signature_check() -> None:
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    from geond.config import Settings
    from geond.dashboard_server import _handle_manus_webhook

    body = _make_webhook_body("task.completed", task_id="wh-nosec-1")
    headers = MagicMock()
    headers.get = lambda k, d=None: str(len(body)) if k == "Content-Length" else d
    rfile = MagicMock()
    rfile.read = lambda n: body

    settings = Settings(manus_webhook_secret="")
    fake_conn = MagicMock()
    fake_conn.__enter__ = lambda s: s
    fake_conn.__exit__ = MagicMock(return_value=False)

    with _patch("geond.dashboard_server.connect", return_value=fake_conn):
        with _patch("geond.storage.repository.upsert_workspace", return_value="ws-id"):
            with _patch("geond.storage.repository.store_manus_task", return_value="sess-id"):
                status, resp = _handle_manus_webhook(settings, headers, rfile)

    assert status == 200
    assert resp["status"] == "ok"


# ---------------------------------------------------------------------------
# Dashboard: dashboard_session_agent maps source="manus" to "Manus" lane
# ---------------------------------------------------------------------------


def test_dashboard_session_agent_manus_lane() -> None:
    from geond.storage.dashboard import dashboard_session_agent

    assert dashboard_session_agent("manus", {}) == "Manus"
    assert dashboard_session_agent("Manus", {}) == "Manus"
    assert dashboard_session_agent("MANUS", {}) == "Manus"


def test_dashboard_session_agent_existing_lanes_not_regressed() -> None:
    from geond.storage.dashboard import dashboard_session_agent

    assert dashboard_session_agent("codex", {}) == "codex"
    assert dashboard_session_agent("claude-code", {}) == "claude"
    assert dashboard_session_agent("vscode-copilot", {}) == "copilot"


# ---------------------------------------------------------------------------
# Storage + Search: search_dev_memory can find Manus task message content
# ---------------------------------------------------------------------------


def test_search_finds_manus_task_message() -> None:
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
    workspace_uri = f"file:///tmp/geond-manus-search-{uuid4()}"

    try:
        conn = connect(settings)
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")

    from geond.retrieval.simple import search_dev_memory
    from geond.storage.repository import store_manus_task, upsert_workspace

    unique_token = f"XQ{uuid4().hex[:12]}"
    task = normalize_task(
        {"id": "task-search-test", "title": "Search test"},
        task_messages={
            "messages": [
                {
                    "id": "msg-s1",
                    "type": "user_message",
                    "timestamp": "1747651200000",
                    "user_message": {"content": f"Please {unique_token} the auth middleware."},
                }
            ]
        },
    )

    with conn:
        try:
            run_schema_file(conn, schema)
        except psycopg.Error as exc:
            pytest.skip(f"Schema unavailable: {exc}")

        workspace_id = upsert_workspace(conn, root_uri=workspace_uri, name="search-test")
        try:
            store_manus_task(conn, workspace_id, task)

            results = search_dev_memory(conn, unique_token, workspace_uri=workspace_uri)
            assert len(results) >= 1
            assert results[0]["source"] == "manus"
            assert unique_token in results[0]["snippet"]

        finally:
            with connect(settings) as cleanup_conn:
                with cleanup_conn.cursor() as cur:
                    cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
                cleanup_conn.commit()


# ---------------------------------------------------------------------------
# is_blocked field
# ---------------------------------------------------------------------------


def test_is_blocked_true_for_blocked_statuses() -> None:
    for status in BLOCKED_STATUSES:
        task = normalize_task({"id": "t1", "title": "T", "status": status})
        assert task.is_blocked is True, f"Expected is_blocked=True for status={status!r}"


def test_is_blocked_false_for_normal_statuses() -> None:
    for status in ("completed", "running", "failed", "created", "unknown"):
        task = normalize_task({"id": "t1", "title": "T", "status": status})
        assert task.is_blocked is False, f"Expected is_blocked=False for status={status!r}"


# ---------------------------------------------------------------------------
# get_task_file_content — size limits
# ---------------------------------------------------------------------------


def test_get_task_file_content_content_length_exceeded() -> None:
    from unittest.mock import MagicMock, patch

    client = ManusApiClient(api_key="test-key")
    limit = 100

    mock_resp = MagicMock()
    mock_resp.headers.get.return_value = str(limit + 1)
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(ManusApiError) as exc_info:
            client.get_task_file_content("task1", "file1", max_bytes=limit)

    assert exc_info.value.status_code == 413


def test_get_task_file_content_body_size_exceeded() -> None:
    from unittest.mock import MagicMock, patch

    client = ManusApiClient(api_key="test-key")
    limit = 5

    mock_resp = MagicMock()
    mock_resp.headers.get.return_value = None
    mock_resp.read.return_value = b"x" * (limit + 2)
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(ManusApiError) as exc_info:
            client.get_task_file_content("task1", "file1", max_bytes=limit)

    assert exc_info.value.status_code == 413


def test_get_task_file_content_success() -> None:
    from unittest.mock import MagicMock, patch

    client = ManusApiClient(api_key="test-key")
    expected = b"hello world"

    mock_resp = MagicMock()
    mock_resp.headers.get.return_value = str(len(expected))
    mock_resp.read.return_value = expected
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = client.get_task_file_content("task1", "file1", max_bytes=1024)

    assert result == expected


# ---------------------------------------------------------------------------
# _mask_task_url helper (via CLI list-manus-tasks --format json)
# ---------------------------------------------------------------------------


def test_list_manus_tasks_private_url_masked(capsys) -> None:
    import json as _json
    import sys
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    mock_tasks = {
        "tasks": [
            {
                "id": "t-priv",
                "title": "Private Task",
                "status": "completed",
                "share_visibility": "private",
                "task_url": "https://manus.ai/tasks/t-priv",
                "share_url": "https://manus.ai/share/t-priv",
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.list_tasks.return_value = mock_tasks

    with _patch.object(sys, "argv", ["geond", "list-manus-tasks", "--format", "json"]):
        with _patch("geond.cli.ManusApiClient", return_value=mock_client):
            from geond.cli import main

            main()

    out = capsys.readouterr().out
    parsed = _json.loads(out)
    task = parsed["tasks"][0]
    assert task["task_url"] == "[private]"
    assert task["share_url"] == "[private]"


def test_list_manus_tasks_private_url_shown_with_flag(capsys) -> None:
    import json as _json
    import sys
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    real_url = "https://manus.ai/tasks/t-priv"
    mock_tasks = {
        "tasks": [
            {
                "id": "t-priv",
                "title": "Private Task",
                "status": "completed",
                "share_visibility": "private",
                "task_url": real_url,
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.list_tasks.return_value = mock_tasks

    with _patch.object(
        sys, "argv", ["geond", "list-manus-tasks", "--format", "json", "--show-private-url"]
    ):
        with _patch("geond.cli.ManusApiClient", return_value=mock_client):
            from geond.cli import main

            main()

    out = capsys.readouterr().out
    parsed = _json.loads(out)
    assert parsed["tasks"][0]["task_url"] == real_url


def test_list_manus_tasks_public_url_not_masked(capsys) -> None:
    import json as _json
    import sys
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    real_url = "https://manus.ai/tasks/t-pub"
    mock_tasks = {
        "tasks": [
            {
                "id": "t-pub",
                "title": "Public Task",
                "status": "completed",
                "share_visibility": "public",
                "task_url": real_url,
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.list_tasks.return_value = mock_tasks

    with _patch.object(sys, "argv", ["geond", "list-manus-tasks", "--format", "json"]):
        with _patch("geond.cli.ManusApiClient", return_value=mock_client):
            from geond.cli import main

            main()

    out = capsys.readouterr().out
    parsed = _json.loads(out)
    assert parsed["tasks"][0]["task_url"] == real_url


# ---------------------------------------------------------------------------
# connector_count in session metadata (DB integration)
# ---------------------------------------------------------------------------


def test_connector_count_in_session_metadata() -> None:
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-manus-conncount-{uuid4()}"

    try:
        conn = connect(settings)
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")

    from geond.storage.repository import store_manus_task, upsert_workspace

    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
    task = normalize_task(
        {
            "id": "task-conn-test",
            "title": "Connector count test",
            "status": "completed",
            "connectors": ["uuid-a", "uuid-b", "uuid-c"],
        }
    )

    try:
        with conn:
            try:
                run_schema_file(conn, schema)
            except psycopg.Error as exc:
                pytest.skip(f"Schema unavailable: {exc}")
            workspace_id = upsert_workspace(conn, workspace_uri, "connector-count-test")
            session_row_id = store_manus_task(conn, workspace_id, task)

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT metadata FROM sessions WHERE id = %s",
                    (session_row_id,),
                )
                row = cur.fetchone()
        assert row is not None
        meta = row[0]
        assert meta.get("connector_count") == 3
    finally:
        with connect(settings) as cleanup_conn:
            with cleanup_conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
            cleanup_conn.commit()


# ---------------------------------------------------------------------------
# excerpt_message utility
# ---------------------------------------------------------------------------


def test_excerpt_message_short_content_unchanged() -> None:
    from geond.adapters.manus import excerpt_message

    assert excerpt_message("hello") == "hello"
    assert excerpt_message("") == ""
    assert excerpt_message("x" * 400) == "x" * 400


def test_excerpt_message_long_content_truncated() -> None:
    from geond.adapters.manus import excerpt_message

    long = "a" * 500
    result = excerpt_message(long, max_chars=400)
    assert len(result) == 401  # 400 chars + ellipsis
    assert result.endswith("…")
    assert result[:400] == "a" * 400


def test_excerpt_message_custom_max_chars() -> None:
    from geond.adapters.manus import excerpt_message

    result = excerpt_message("hello world", max_chars=5)
    assert result == "hello…"


# ---------------------------------------------------------------------------
# manus-get-file CLI (mock API)
# ---------------------------------------------------------------------------


def test_cli_manus_get_file_writes_to_output(tmp_path) -> None:
    import sys
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    output_path = tmp_path / "file.bin"
    expected_bytes = b"file content data"

    mock_client = MagicMock()
    mock_client.get_task_file_content.return_value = expected_bytes

    with _patch.object(
        sys,
        "argv",
        [
            "geond",
            "manus-get-file",
            "--task-id",
            "task1",
            "--file-id",
            "file1",
            "--output",
            str(output_path),
        ],
    ):
        with _patch("geond.cli.ManusApiClient", return_value=mock_client):
            from geond.cli import main

            main()

    assert output_path.read_bytes() == expected_bytes
    mock_client.get_task_file_content.assert_called_once_with(
        task_id="task1",
        file_id="file1",
        max_bytes=10 * 1024 * 1024,
    )


def test_cli_manus_get_file_error_exits(capsys) -> None:
    import sys
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    mock_client = MagicMock()
    mock_client.get_task_file_content.side_effect = ManusApiError(
        404, "/v2/task.getFile", "not_found"
    )

    with _patch.object(
        sys,
        "argv",
        ["geond", "manus-get-file", "--task-id", "bad", "--file-id", "f1", "--output", "/tmp/x"],
    ):
        with _patch("geond.cli.ManusApiClient", return_value=mock_client):
            with pytest.raises(SystemExit) as exc_info:
                from geond.cli import main

                main()

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "not_found" in err or "404" in err


# ---------------------------------------------------------------------------
# manus-dashboard CLI (mock DB)
# ---------------------------------------------------------------------------


def test_cli_manus_dashboard_json_format(capsys) -> None:
    import json as _json
    import sys
    from unittest.mock import patch as _patch

    mock_result = {
        "workspace_id": "ws-1",
        "workspace_uri": "file:///test",
        "workspace_name": "test",
        "status": "ok",
        "task_count": 1,
        "tasks": [
            {
                "session_id": "sess-1",
                "task_id": "task-abc",
                "title": "Test task",
                "status": "completed",
                "is_blocked": False,
                "task_url": None,
                "share_visibility": "private",
                "connector_count": 0,
                "message_count": 3,
                "latest_message_at": None,
                "created_at": None,
                "updated_at": None,
                "excerpt": "Some work was done",
            }
        ],
    }

    with _patch.object(
        sys,
        "argv",
        ["geond", "manus-dashboard", "--workspace-uri", "file:///test", "--format", "json"],
    ):
        with _patch("geond.cli.get_dashboard_manus_sessions", return_value=mock_result):
            with _patch("geond.cli.connect"):
                with _patch("geond.cli.get_settings"):
                    from geond.cli import main

                    main()

    out = capsys.readouterr().out
    parsed = _json.loads(out)
    assert parsed["task_count"] == 1
    assert parsed["tasks"][0]["task_id"] == "task-abc"


# ---------------------------------------------------------------------------
# get_dashboard_manus_sessions (DB integration)
# ---------------------------------------------------------------------------


def test_get_dashboard_manus_sessions_returns_task_cards() -> None:
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-manus-dash-{uuid4()}"

    try:
        conn = connect(settings)
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")

    from geond.storage.dashboard import get_dashboard_manus_sessions
    from geond.storage.repository import store_manus_task, upsert_workspace

    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"
    task = normalize_task(
        {
            "id": "task-dash-test",
            "title": "Dashboard test task",
            "status": "completed",
            "share_visibility": "private",
            "connectors": ["c1"],
        },
        task_messages={
            "messages": [
                {
                    "id": "m1",
                    "type": "user_message",
                    "timestamp": None,
                    "user_message": {"content": "Do the thing"},
                },
                {
                    "id": "m2",
                    "type": "assistant_message",
                    "timestamp": None,
                    "assistant_message": {"content": "Done!"},
                },
            ]
        },
    )

    try:
        with conn:
            try:
                run_schema_file(conn, schema)
            except psycopg.Error as exc:
                pytest.skip(f"Schema unavailable: {exc}")
            workspace_id = upsert_workspace(conn, workspace_uri, "manus-dashboard-test")
            store_manus_task(conn, workspace_id, task)

        with connect(settings) as fresh_conn:
            result = get_dashboard_manus_sessions(fresh_conn, workspace_uri)
        assert result["status"] == "ok"
        assert result["task_count"] == 1
        card = result["tasks"][0]
        assert card["task_id"] == "task-dash-test"
        assert card["title"] == "Dashboard test task"
        assert card["status"] == "completed"
        assert card["is_blocked"] is False
        assert card["connector_count"] == 1
        assert card["message_count"] == 2
        assert card["excerpt"] != ""
    finally:
        with connect(settings) as cleanup_conn:
            with cleanup_conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
            cleanup_conn.commit()


# ---------------------------------------------------------------------------
# Multi-task dashboard integration test
# ---------------------------------------------------------------------------


def test_get_dashboard_manus_sessions_multiple_tasks() -> None:
    """Completed, failed, and blocked tasks all appear as separate cards."""
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-manus-multi-{uuid4()}"

    try:
        conn = connect(settings)
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")

    from geond.storage.dashboard import get_dashboard_manus_sessions
    from geond.storage.repository import store_manus_task, upsert_workspace

    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"

    task_completed = normalize_task(
        {"id": "task-multi-completed", "title": "Completed Task", "status": "completed"},
        task_messages={
            "messages": [
                {
                    "id": "mc1",
                    "type": "assistant_message",
                    "timestamp": None,
                    "assistant_message": {"content": "All done."},
                }
            ]
        },
    )
    task_failed = normalize_task(
        {"id": "task-multi-failed", "title": "Failed Task", "status": "failed"},
        task_messages={
            "messages": [
                {
                    "id": "mf1",
                    "type": "assistant_message",
                    "timestamp": None,
                    "assistant_message": {"content": "Error occurred."},
                }
            ]
        },
    )
    task_blocked = normalize_task(
        {
            "id": "task-multi-blocked",
            "title": "Blocked Task",
            "status": "needs_input",
        },
        task_messages={
            "messages": [
                {
                    "id": "mb1",
                    "type": "assistant_message",
                    "timestamp": None,
                    "assistant_message": {"content": "Waiting for you."},
                }
            ]
        },
    )

    try:
        with conn:
            try:
                run_schema_file(conn, schema)
            except psycopg.Error as exc:
                pytest.skip(f"Schema unavailable: {exc}")
            workspace_id = upsert_workspace(conn, workspace_uri, "manus-multi-test")
            store_manus_task(conn, workspace_id, task_completed)
            store_manus_task(conn, workspace_id, task_failed)
            store_manus_task(conn, workspace_id, task_blocked)

        with connect(settings) as fresh_conn:
            result = get_dashboard_manus_sessions(fresh_conn, workspace_uri)

        assert result["status"] == "ok"
        assert result["task_count"] == 3

        by_task_id = {c["task_id"]: c for c in result["tasks"]}
        assert set(by_task_id.keys()) == {
            "task-multi-completed",
            "task-multi-failed",
            "task-multi-blocked",
        }

        assert by_task_id["task-multi-completed"]["status"] == "completed"
        assert by_task_id["task-multi-completed"]["is_blocked"] is False

        assert by_task_id["task-multi-failed"]["status"] == "failed"
        assert by_task_id["task-multi-failed"]["is_blocked"] is False

        assert by_task_id["task-multi-blocked"]["status"] == "needs_input"
        assert by_task_id["task-multi-blocked"]["is_blocked"] is True

        for card in result["tasks"]:
            assert card["message_count"] == 1
            assert card["excerpt"] != ""
    finally:
        with connect(settings) as cleanup_conn2:
            with cleanup_conn2.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
            cleanup_conn2.commit()


# ---------------------------------------------------------------------------
# build_manus_context_packet includes blocked_manus_tasks
# ---------------------------------------------------------------------------


def test_build_manus_context_packet_includes_blocked_tasks() -> None:
    """blocked_manus_tasks section in context packet contains only blocked tasks."""
    try:
        import psycopg

        from geond.config import get_settings
        from geond.db import connect, run_schema_file
    except ImportError:
        pytest.skip("psycopg not available")

    settings = get_settings()
    workspace_uri = f"file:///tmp/geond-manus-ctx-{uuid4()}"

    try:
        conn = connect(settings)
    except Exception as exc:
        pytest.skip(f"Postgres not available: {exc}")

    from geond.cli import build_manus_context_packet
    from geond.storage.repository import store_manus_task, upsert_workspace

    schema = Path(__file__).parents[1] / "schemas" / "001_initial.sql"

    task_blocked = normalize_task(
        {
            "id": "task-ctx-blocked",
            "title": "Blocked for context test",
            "status": "needs_input",
        }
    )
    task_done = normalize_task({"id": "task-ctx-done", "title": "Done task", "status": "completed"})

    try:
        with conn:
            try:
                run_schema_file(conn, schema)
            except psycopg.Error as exc:
                pytest.skip(f"Schema unavailable: {exc}")
            workspace_id = upsert_workspace(conn, workspace_uri, "manus-ctx-test")
            store_manus_task(conn, workspace_id, task_blocked)
            store_manus_task(conn, workspace_id, task_done)

        with connect(settings) as fresh_conn:
            packet = build_manus_context_packet(fresh_conn, workspace_uri, query="")

        blocked_tasks = packet.get("blocked_manus_tasks") or []
        task_ids = [t["task_id"] for t in blocked_tasks]
        assert "task-ctx-blocked" in task_ids
        assert "task-ctx-done" not in task_ids
        for bt in blocked_tasks:
            assert "task_id" in bt
            assert "title" in bt
            assert "status" in bt
    finally:
        with connect(settings) as cleanup_conn3:
            with cleanup_conn3.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,))
            cleanup_conn3.commit()


# ---------------------------------------------------------------------------
# CLI error actionable hint tests
# ---------------------------------------------------------------------------


def test_manus_api_error_permission_denied_has_hint() -> None:
    """403 error message includes actionable API key hint."""
    import urllib.error

    client = ManusApiClient(api_key="fake-key")
    http_err = urllib.error.HTTPError(
        url="https://api.manus.ai/v2/task.detail",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(ManusApiError) as exc_info:
            client.get_task("task-xyz")
    assert exc_info.value.status_code == 403
    assert "permission_denied" in str(exc_info.value).lower()
    assert "api key" in str(exc_info.value).lower()


def test_manus_api_error_not_found_has_hint() -> None:
    """404 error message mentions task_id so the user knows what to check."""
    import urllib.error

    client = ManusApiClient(api_key="fake-key")
    http_err = urllib.error.HTTPError(
        url="https://api.manus.ai/v2/task.detail",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(ManusApiError) as exc_info:
            client.get_task("task-xyz")
    assert exc_info.value.status_code == 404
    assert "not_found" in str(exc_info.value).lower()
    assert "task_id" in str(exc_info.value).lower()


def test_manus_api_error_invalid_argument_includes_body() -> None:
    """400 error message includes response body excerpt."""
    import io
    import urllib.error

    client = ManusApiClient(api_key="fake-key")
    body_bytes = b'{"error": "missing required field: prompt"}'
    http_err = urllib.error.HTTPError(
        url="https://api.manus.ai/v2/task.create",
        code=400,
        msg="Bad Request",
        hdrs=None,
        fp=io.BytesIO(body_bytes),
    )
    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(ManusApiError) as exc_info:
            client.create_task("title", "prompt")
    assert exc_info.value.status_code == 400
    assert "invalid_argument" in str(exc_info.value).lower()


def test_manus_api_error_does_not_leak_api_key() -> None:
    """API key never appears in error messages or endpoint strings."""
    import urllib.error

    secret = "sk-super-secret-key-12345"
    client = ManusApiClient(api_key=secret)
    http_err = urllib.error.HTTPError(
        url="https://api.manus.ai/v2/task.detail",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(ManusApiError) as exc_info:
            client.get_task("task-secret")
    assert secret not in str(exc_info.value)
    assert secret not in exc_info.value.endpoint
