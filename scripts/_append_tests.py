"""Append new Manus test functions to test_manus_adapter.py."""

new_tests = """

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
    files_file.write_text(
        _json.dumps({"files": [{"id": "file-fx-001", "name": "output.txt"}]})
    )

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
    import sys
    import json as _json
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
    import sys
    import json as _json
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    mock_list = {
        "tasks": [
            {"id": "t-dry-1", "title": "Dry Task", "status": "completed"},
        ]
    }
    mock_fetched = normalize_task(
        {"id": "t-dry-1", "title": "Dry Task", "status": "completed"}
    )

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
    assert parsed["dry_run"] is True
    assert parsed["total"] == 1
    assert parsed["imported"] == 0


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
                    cur.execute(
                        "DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,)
                    )
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
                    cur.execute(
                        "DELETE FROM workspaces WHERE root_uri = %s", (workspace_uri,)
                    )
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
        with _patch("geond.storage.resources.upsert_workspace", return_value="ws-uuid"):
            with _patch(
                "geond.storage.repository.store_manus_task", return_value="sess-uuid"
            ):
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
        with _patch("geond.storage.resources.upsert_workspace", return_value="ws-id"):
            with _patch(
                "geond.storage.repository.store_manus_task", return_value="sess-id"
            ):
                status, resp = _handle_manus_webhook(settings, headers, rfile)

    assert status == 200
    assert resp["status"] == "ok"
"""

path = r"c:/Users/EL035/dataschool/geond-agent-protocol/tests/test_manus_adapter.py"
with open(path, "a", encoding="utf-8") as f:
    f.write(new_tests)

print("done")
