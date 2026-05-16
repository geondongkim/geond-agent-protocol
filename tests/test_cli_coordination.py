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
