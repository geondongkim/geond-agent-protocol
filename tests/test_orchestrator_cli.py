from __future__ import annotations

import json
import sys
from pathlib import Path

from geond import orchestrator_cli


class FakeConnection:
    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def test_orchestrator_run_cli_wires_service(monkeypatch, capsys, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    graph_path = tmp_path / "graph.md"
    graph_path.write_text("- [ ] repro | Reproduce issue\n", encoding="utf-8")

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_start_run(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {"schema": "geond.orchestrator_run.v1", "status": "ok", "run": {"run_id": "run-1"}}

    monkeypatch.setattr(orchestrator_cli, "connect", fake_connect)
    monkeypatch.setattr(orchestrator_cli.orchestrator, "start_run", fake_start_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "run",
            "Fix checkout flow",
            "--workspace",
            "file:///repo",
            "--risk-level",
            "medium",
            "--task-graph",
            str(graph_path),
            "--format",
            "json",
        ],
    )

    orchestrator_cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == "geond.orchestrator_run.v1"
    assert captured == {
        "goal": "Fix checkout flow",
        "workspace_id_or_uri": "file:///repo",
        "risk_level": "medium",
        "task_graph_path": graph_path,
    }


def test_orchestrator_status_dispatch_resume_finalize_cli(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_status(conn, run_id, **kwargs):  # noqa: ANN001, ANN202
        captured["status"] = {"run_id": run_id, **kwargs}
        return {
            "schema": "geond.orchestrator_status.v1",
            "status": "ok",
            "markdown": "# Status\n",
        }

    def fake_dispatch(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["dispatch"] = kwargs
        return {
            "schema": "geond.orchestrator_dispatch.v1",
            "status": "ok",
            "markdown": "# Dispatch\n",
        }

    def fake_resume(conn, run_id, **kwargs):  # noqa: ANN001, ANN202
        captured["resume"] = {"run_id": run_id, **kwargs}
        return {
            "schema": "geond.orchestrator_resume.v1",
            "status": "ok",
            "markdown": "# Resume\n",
        }

    def fake_finalize(conn, run_id, **kwargs):  # noqa: ANN001, ANN202
        captured["finalize"] = {"run_id": run_id, **kwargs}
        return {
            "schema": "geond.orchestrator_finalize.v1",
            "status": "ok",
            "markdown": "# Finalize\n",
        }

    def fake_plan(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["plan"] = kwargs
        return {
            "schema": "geond.orchestrator_plan.v1",
            "status": "ok",
            "markdown": "# Plan\n",
        }

    def fake_doctor(conn, run_id, **kwargs):  # noqa: ANN001, ANN202
        captured["doctor"] = {"run_id": run_id, **kwargs}
        return {
            "schema": "geond.orchestrator_plan.v1",
            "status": "ok",
            "markdown": "# Doctor\n",
        }

    monkeypatch.setattr(orchestrator_cli, "connect", fake_connect)
    monkeypatch.setattr(orchestrator_cli.orchestrator, "get_status", fake_status)
    monkeypatch.setattr(orchestrator_cli.orchestrator, "dispatch_claim", fake_dispatch)
    monkeypatch.setattr(orchestrator_cli.orchestrator, "resume_run", fake_resume)
    monkeypatch.setattr(orchestrator_cli.orchestrator, "finalize_run", fake_finalize)
    monkeypatch.setattr(orchestrator_cli.orchestrator_planner, "create_plan", fake_plan)
    monkeypatch.setattr(orchestrator_cli.orchestrator_planner, "doctor_run", fake_doctor)

    monkeypatch.setattr(sys, "argv", ["geond-orchestrator", "status", "run-1"])
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Status\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "plan",
            "--workspace",
            "file:///repo",
            "--run",
            "run-1",
            "--agents",
            "codex,claude",
            "--write-bundle",
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Plan\n"

    monkeypatch.setattr(
        sys,
        "argv",
        ["geond-orchestrator", "doctor", "run-1", "--agents", "claude"],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Doctor\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "dispatch",
            "--run",
            "run-1",
            "--mode",
            "claim",
            "--agent",
            "claude",
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Dispatch\n"

    monkeypatch.setattr(sys, "argv", ["geond-orchestrator", "resume", "run-1"])
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Resume\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "finalize",
            "run-1",
            "--write-manifest",
            "--git-checkpoint",
            "--dry-run",
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Finalize\n"

    assert captured["status"]["run_id"] == "run-1"
    assert captured["plan"]["workspace_id_or_uri"] == "file:///repo"
    assert captured["plan"]["run_id"] == "run-1"
    assert captured["plan"]["agents"] == ["codex", "claude"]
    assert captured["plan"]["write_bundle"] is True
    assert captured["doctor"]["run_id"] == "run-1"
    assert captured["doctor"]["agents"] == ["claude"]
    assert captured["dispatch"]["run_id"] == "run-1"
    assert captured["dispatch"]["agent_name"] == "claude"
    assert captured["resume"]["run_id"] == "run-1"
    assert captured["finalize"]["write_manifest"] is True
    assert captured["finalize"]["git_checkpoint"] is True
    assert captured["finalize"]["dry_run"] is True


def test_orchestrator_spawn_dispatch_cli_wires_service(monkeypatch, capsys, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_spawn(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {
            "schema": "geond.orchestrator_dispatch.v1",
            "status": "ok",
            "dispatch_mode": "spawn",
        }

    monkeypatch.setattr(orchestrator_cli, "connect", fake_connect)
    monkeypatch.setattr(orchestrator_cli.orchestrator, "dispatch_spawn", fake_spawn)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "dispatch",
            "--run",
            "run-1",
            "--mode",
            "spawn",
            "--agent",
            "codex",
            "--agents",
            "codex,claude",
            "--execute",
            "--task",
            "task-1",
            "--task",
            "task-2",
            "--model",
            "gpt-5",
            "--sandbox",
            "workspace-write",
            "--timeout-seconds",
            "12",
            "--write-bundle",
            "--max-workers",
            "2",
            "--base-dir",
            str(tmp_path),
            "--format",
            "json",
        ],
    )

    orchestrator_cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["dispatch_mode"] == "spawn"
    assert captured == {
        "run_id": "run-1",
        "agent_name": "codex",
        "execute": True,
        "task_id": "task-1",
        "task_ids": ["task-1", "task-2"],
        "agent_names": ["codex", "claude"],
        "max_workers": 2,
        "model": "gpt-5",
        "sandbox": "workspace-write",
        "timeout_seconds": 12,
        "write_bundle": True,
        "manifest_base_dir": tmp_path,
    }
