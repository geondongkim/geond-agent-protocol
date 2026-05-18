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
