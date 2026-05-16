from __future__ import annotations

import geond.cli_tasks as tasks


class FakeConnection:
    pass


def test_start_task_dry_run_does_not_mutate(monkeypatch) -> None:
    def fail_mutation(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("dry-run should not mutate")

    monkeypatch.setattr(tasks, "resolve_workspace_id", lambda conn, workspace: "workspace-1")
    monkeypatch.setattr(tasks, "get_dashboard_overview", lambda *args, **kwargs: {"counts": {}})
    monkeypatch.setattr(tasks, "list_handoff_summaries", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "list_active_file_reservations", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "list_active_symbol_reservations", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        tasks,
        "review_workspace_context",
        lambda *args, **kwargs: {"assessment": {"status": "clear"}},
    )
    monkeypatch.setattr(tasks, "record_agent_action", fail_mutation)
    monkeypatch.setattr(tasks, "reserve_files", fail_mutation)
    monkeypatch.setattr(tasks, "reserve_symbols", fail_mutation)

    result = tasks.start_task(
        FakeConnection(),
        "file:///repo",
        agent_name="codex",
        intent="Add wrappers",
        file_paths=["src/geond/cli.py"],
        symbols=["main"],
        reserve=True,
        dry_run=True,
    )

    assert result["status"] == "dry_run"
    assert result["action_id"] is None
    assert result["requested"] == {"files": ["src/geond/cli.py"], "symbols": ["main"]}


def test_start_task_records_action_and_reservations(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(tasks, "resolve_workspace_id", lambda conn, workspace: "workspace-1")
    monkeypatch.setattr(tasks, "get_dashboard_overview", lambda *args, **kwargs: {"counts": {}})
    monkeypatch.setattr(tasks, "list_handoff_summaries", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "list_active_file_reservations", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "list_active_symbol_reservations", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        tasks,
        "review_workspace_context",
        lambda *args, **kwargs: {"assessment": {"status": "clear"}},
    )

    def fake_record_action(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["action"] = kwargs
        return "action-1"

    def fake_reserve_files(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["files"] = kwargs
        return {"reservation_ids": ["file-reservation-1"], "blocked": False}

    def fake_reserve_symbols(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["symbols"] = kwargs
        return {"reservation_ids": ["symbol-reservation-1"], "blocked": False}

    monkeypatch.setattr(tasks, "record_agent_action", fake_record_action)
    monkeypatch.setattr(tasks, "reserve_files", fake_reserve_files)
    monkeypatch.setattr(tasks, "reserve_symbols", fake_reserve_symbols)

    result = tasks.start_task(
        FakeConnection(),
        "file:///repo",
        agent_name="codex",
        intent="Add wrappers",
        file_paths=["src/geond/cli.py"],
        symbols=["main"],
        reserve=True,
    )

    assert result["status"] == "ok"
    assert result["action_id"] == "action-1"
    assert captured["action"]["action_type"] == "task_start"
    assert captured["files"]["file_paths"] == ["src/geond/cli.py"]
    assert captured["symbols"]["symbols"] == ["main"]


def test_finish_task_records_handoff_changeset_and_releases_reservations(monkeypatch) -> None:
    monkeypatch.setattr(tasks, "resolve_workspace_id", lambda conn, workspace: "workspace-1")
    monkeypatch.setattr(
        tasks,
        "list_active_file_reservations",
        lambda *args, **kwargs: [
            {"reservation_id": "file-1", "agent_name": "codex", "file_path": "src/geond/cli.py"},
            {"reservation_id": "file-2", "agent_name": "other", "file_path": "README.md"},
        ],
    )
    monkeypatch.setattr(
        tasks,
        "list_active_symbol_reservations",
        lambda *args, **kwargs: [
            {"reservation_id": "symbol-1", "agent_name": "codex", "symbol": "main"}
        ],
    )
    monkeypatch.setattr(tasks, "record_agent_action", lambda *args, **kwargs: "action-1")
    monkeypatch.setattr(
        tasks,
        "record_changeset",
        lambda *args, **kwargs: {"changeset_id": "changeset-1", "files": kwargs["files"]},
    )
    monkeypatch.setattr(tasks, "record_handoff_summary", lambda *args, **kwargs: "handoff-1")
    monkeypatch.setattr(tasks, "release_reservation", lambda *args, **kwargs: 1)
    monkeypatch.setattr(tasks, "release_symbol_reservation", lambda *args, **kwargs: 1)

    result = tasks.finish_task(
        FakeConnection(),
        "file:///repo",
        agent_name="codex",
        summary="Added wrappers",
        changed_files=[{"file_path": "src/geond/cli.py", "status": "modified"}],
        tested_commands=["uv run pytest"],
        reservation_mode="release",
    )

    assert result["status"] == "ok"
    assert result["action_id"] == "action-1"
    assert result["changeset"]["changeset_id"] == "changeset-1"
    assert result["handoff_id"] == "handoff-1"
    assert result["reservation_updates"] == {
        "files": [{"reservation_id": "file-1", "count": 1}],
        "symbols": [{"reservation_id": "symbol-1", "count": 1}],
    }
    assert result["tested_commands"] == ["uv run pytest"]
