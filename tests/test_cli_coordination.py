from __future__ import annotations

import json
import sys

import geond.cli as cli


class FakeConnection:
    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def test_record_agent_action_cli_resolves_workspace_and_links_session(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_resolve_workspace_id(conn, workspace_id_or_uri: str) -> str:  # noqa: ANN001
        captured["workspace_arg"] = workspace_id_or_uri
        return "workspace-1"

    def fake_record_agent_action(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return "action-1"

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr(cli, "record_agent_action", fake_record_agent_action)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "record-agent-action",
            "file:///repo",
            "--agent-name",
            "codex",
            "--action-kind",
            "task_start",
            "--summary",
            "Start usage metrics",
            "--intent",
            "Add usage metrics",
            "--session-external-id",
            "session-abc",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "ok", "workspace_id": "workspace-1", "action_id": "action-1"}
    assert captured["workspace_arg"] == "file:///repo"
    assert captured["workspace_id"] == "workspace-1"
    assert captured["agent_name"] == "codex"
    assert captured["action_type"] == "task_start"
    assert captured["session_external_id"] == "session-abc"
    assert captured["metadata"] == {"source": "cli"}


def test_start_task_cli_wires_wrapper(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_start_task(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {"status": "dry_run", "command": "start-task", "workspace_id": "workspace-1"}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "start_task", fake_start_task)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "start-task",
            "file:///repo",
            "--agent-name",
            "codex",
            "--intent",
            "Add task wrappers",
            "--file",
            "src/geond/cli.py",
            "--symbol",
            "main",
            "--reserve",
            "--dry-run",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["command"] == "start-task"
    assert captured["workspace_id_or_uri"] == "file:///repo"
    assert captured["agent_name"] == "codex"
    assert captured["intent"] == "Add task wrappers"
    assert captured["file_paths"] == ["src/geond/cli.py"]
    assert captured["symbols"] == ["main"]
    assert captured["reserve"] is True
    assert captured["dry_run"] is True


def test_finish_task_cli_wires_wrapper(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_finish_task(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {"status": "ok", "command": "finish-task", "workspace_id": "workspace-1"}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "finish_task", fake_finish_task)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "finish-task",
            "file:///repo",
            "--agent-name",
            "codex",
            "--summary",
            "Added task wrappers",
            "--changeset-file",
            "src/geond/cli.py:modified",
            "--tested-command",
            "uv run pytest tests/test_cli_coordination.py",
            "--release-reservations",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["command"] == "finish-task"
    assert captured["workspace_id_or_uri"] == "file:///repo"
    assert captured["agent_name"] == "codex"
    assert captured["summary"] == "Added task wrappers"
    assert captured["changed_files"] == [{"file_path": "src/geond/cli.py", "status": "modified"}]
    assert captured["tested_commands"] == ["uv run pytest tests/test_cli_coordination.py"]
    assert captured["reservation_mode"] == "release"


def test_usage_summary_cli_wires_storage(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_summarize_usage(conn, workspace_id_or_uri: str, **kwargs):  # noqa: ANN001, ANN202
        captured["workspace_id_or_uri"] = workspace_id_or_uri
        captured.update(kwargs)
        return {"status": "ok", "workspace_id": "workspace-1", "totals": {"event_count": 0}}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "summarize_usage", fake_summarize_usage)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "usage-summary",
            "file:///repo",
            "--source",
            "codex",
            "--provider",
            "openai",
            "--model",
            "gpt-test",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert captured == {
        "workspace_id_or_uri": "file:///repo",
        "source": "codex",
        "provider": "openai",
        "model": "gpt-test",
    }


def test_usage_by_agent_cli_outputs_agent_rollup(monkeypatch, capsys) -> None:
    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_summarize_usage(conn, workspace_id_or_uri: str, **kwargs):  # noqa: ANN001, ANN202
        return {
            "status": "ok",
            "workspace_id": "workspace-1",
            "totals": {"event_count": 1, "total_tokens": 42},
            "filters": kwargs,
            "by_agent": [
                {"agent_name": "codex", "event_count": 1, "total_tokens": 42},
            ],
        }

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "summarize_usage", fake_summarize_usage)
    monkeypatch.setattr(sys, "argv", ["geond", "usage-by-agent", "file:///repo"])

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["by_agent"][0]["agent_name"] == "codex"
    assert output["totals"]["total_tokens"] == 42


def test_usage_by_model_cli_outputs_model_rollup(monkeypatch, capsys) -> None:
    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_summarize_usage(conn, workspace_id_or_uri: str, **kwargs):  # noqa: ANN001, ANN202
        return {
            "status": "ok",
            "workspace_id": "workspace-1",
            "totals": {"event_count": 1, "total_tokens": 42},
            "filters": kwargs,
            "by_model": [
                {"provider": "openai", "model": "gpt-test", "event_count": 1},
            ],
        }

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "summarize_usage", fake_summarize_usage)
    monkeypatch.setattr(sys, "argv", ["geond", "usage-by-model", "file:///repo"])

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["by_model"][0]["model"] == "gpt-test"


def test_usage_risk_signals_cli_compares_usage_to_evidence(monkeypatch, capsys) -> None:
    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_get_dashboard_usage(conn, workspace_id_or_uri: str):  # noqa: ANN001, ANN202
        return {
            "status": "ok",
            "workspace_id": "workspace-1",
            "usage": {
                "totals": {"event_count": 1, "total_tokens": 100},
                "data_quality": {"estimated_token_share": 1.0, "exact_event_count": 0},
            },
            "evidence": {"changesets": 0, "tested_handoffs": 0, "user_prompts": 1},
            "usage_vs_evidence": {"has_output_evidence": False},
        }

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "get_dashboard_usage", fake_get_dashboard_usage)
    monkeypatch.setattr(sys, "argv", ["geond", "usage-risk-signals", "file:///repo"])

    cli.main()

    output = json.loads(capsys.readouterr().out)
    codes = {signal["code"] for signal in output["signals"]}
    assert "usage_without_output_evidence" in codes
    assert "estimated_heavy_usage" in codes
