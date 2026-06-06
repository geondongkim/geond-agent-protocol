from __future__ import annotations

import json
import sys
from types import SimpleNamespace

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


def test_dashboard_orchestration_cli_wires_storage(monkeypatch, capsys, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_dashboard_orchestration(conn, workspace_id_or_uri, **kwargs):  # noqa: ANN001, ANN202
        captured["workspace_id_or_uri"] = workspace_id_or_uri
        captured.update(kwargs)
        return {"schema": "geond.dashboard_orchestration.v1", "status": "ok", "runs": []}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "get_dashboard_orchestration", fake_dashboard_orchestration)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "dashboard-orchestration",
            "file:///repo",
            "--limit",
            "5",
            "--base-dir",
            str(tmp_path),
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == "geond.dashboard_orchestration.v1"
    assert captured == {
        "workspace_id_or_uri": "file:///repo",
        "limit": 5,
        "base_dir": tmp_path,
    }


def test_dashboard_orchestration_actions_cli_wires_queue(monkeypatch, capsys, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_action_queue(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {
            "schema": "geond.orchestrator_action_queue.v1",
            "status": "ok",
            "items": [],
        }

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli.orchestrator_action_queue, "list_action_queue", fake_action_queue)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "dashboard-orchestration-actions",
            "file:///repo",
            "--run",
            "run-1",
            "--agents",
            "codex,claude",
            "--limit",
            "5",
            "--base-dir",
            str(tmp_path),
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == "geond.orchestrator_action_queue.v1"
    assert captured == {
        "workspace_id_or_uri": "file:///repo",
        "run_id": "run-1",
        "agents": ["codex", "claude"],
        "limit": 5,
        "base_dir": tmp_path,
    }


def test_dashboard_orchestration_scheduler_cli_wires_service(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_scheduler(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {
            "schema": "geond.orchestrator_scheduler.v1",
            "status": "ok",
            "selected_actions": [],
        }

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli.orchestrator_scheduler, "build_dashboard_scheduler", fake_scheduler)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "dashboard-orchestration-scheduler",
            "file:///repo",
            "--limit",
            "5",
            "--base-dir",
            str(tmp_path),
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == "geond.orchestrator_scheduler.v1"
    assert captured == {
        "workspace_id_or_uri": "file:///repo",
        "limit": 5,
        "base_dir": tmp_path,
    }


def test_dashboard_orchestration_budget_and_daemon_cli_wires_services(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_budget(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["budget"] = kwargs
        return {
            "schema": "geond.orchestrator_budget_report.v1",
            "status": "ok",
        }

    def fake_daemon(**kwargs):  # noqa: ANN001, ANN202
        captured["daemon"] = kwargs
        return {
            "schema": "geond.orchestrator_daemon.v1",
            "status": "ok",
        }

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli.orchestrator_budget, "build_dashboard_budget", fake_budget)
    monkeypatch.setattr(cli.orchestrator_daemon, "build_dashboard_daemon", fake_daemon)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "dashboard-orchestration-budget",
            "file:///repo",
            "--limit",
            "5",
            "--base-dir",
            str(tmp_path),
        ],
    )
    cli.main()
    budget_output = json.loads(capsys.readouterr().out)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "dashboard-orchestration-daemon",
            "file:///repo",
            "--limit",
            "7",
            "--base-dir",
            str(tmp_path),
        ],
    )
    cli.main()
    daemon_output = json.loads(capsys.readouterr().out)

    assert budget_output["schema"] == "geond.orchestrator_budget_report.v1"
    assert daemon_output["schema"] == "geond.orchestrator_daemon.v1"
    assert captured["budget"] == {
        "workspace_id_or_uri": "file:///repo",
        "limit": 5,
        "base_dir": tmp_path,
    }
    assert captured["daemon"] == {
        "workspace_id_or_uri": "file:///repo",
        "limit": 7,
        "base_dir": tmp_path,
    }


def test_orchestration_goal_start_cli_wires_storage(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_create_goal(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {"status": "ok", "goal": {"goal_id": "goal-1"}}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "orchestration_create_goal", fake_create_goal)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "goal",
            "start",
            "file:///repo",
            "--title",
            "Prepare MCP orchestration",
            "--description",
            "MCP-first foundation",
            "--agent-name",
            "codex",
            "--idempotency-key",
            "goal-key",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["goal"]["goal_id"] == "goal-1"
    assert captured == {
        "workspace_id_or_uri": "file:///repo",
        "title": "Prepare MCP orchestration",
        "summary": "MCP-first foundation",
        "status": "accepted",
        "created_by_agent": "codex",
        "idempotency_key": "goal-key",
    }


def test_orchestration_worker_register_cli_wires_metadata(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_register_worker(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {"status": "ok", "worker_session": {"worker_session_id": "worker-1"}}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "orchestration_register_worker", fake_register_worker)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "worker",
            "register",
            "run-1",
            "--agent",
            "codex",
            "--model",
            "gpt-5",
            "--capability",
            "cli",
            "--capability",
            "mcp",
            "--session-external-id",
            "session-1",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["worker_session"]["worker_session_id"] == "worker-1"
    assert captured == {
        "run_id": "run-1",
        "agent_name": "codex",
        "status": "active",
        "session_external_id": "session-1",
        "metadata": {"model": "gpt-5", "capabilities": ["cli", "mcp"]},
        "idempotency_key": None,
    }


def test_orchestration_worker_claim_cli_can_pick_first_claimable_task(
    monkeypatch,
    capsys,
) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_claimable(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["claimable"] = kwargs
        return {"status": "ok", "tasks": [{"task_id": "task-1"}]}

    def fake_claim(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["claim"] = kwargs
        return {"status": "ok", "lease": {"lease_id": "lease-1"}}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "orchestration_claimable_tasks", fake_claimable)
    monkeypatch.setattr(cli, "orchestration_claim_task", fake_claim)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "worker",
            "claim",
            "--run",
            "run-1",
            "--agent",
            "codex",
            "--ttl-minutes",
            "30",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["lease"]["lease_id"] == "lease-1"
    assert captured["claimable"] == {"run_id": "run-1", "limit": 1}
    assert captured["claim"] == {
        "task_id": "task-1",
        "agent_name": "codex",
        "worker_session_id": None,
        "ttl_minutes": 30,
        "idempotency_key": None,
    }


def test_orchestration_worker_finish_and_readiness_cli_wire_storage(
    monkeypatch,
    capsys,
) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_finish(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["finish"] = kwargs
        return {"status": "ok", "handoff_id": "handoff-1"}

    def fake_readiness(conn, run_id: str):  # noqa: ANN001
        captured["readiness"] = run_id
        return {"schema": "geond.readiness_report.v1", "status": "ready", "run_id": run_id}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "orchestration_finish_task", fake_finish)
    monkeypatch.setattr(cli, "orchestration_readiness", fake_readiness)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "worker",
            "finish",
            "lease-1",
            "--summary",
            "Done",
            "--tested-command",
            "uv run pytest",
            "--next-action",
            "review",
        ],
    )

    cli.main()
    finish_output = json.loads(capsys.readouterr().out)

    monkeypatch.setattr(sys, "argv", ["geond", "readiness", "run-1"])
    cli.main()
    readiness_output = json.loads(capsys.readouterr().out)

    assert finish_output["handoff_id"] == "handoff-1"
    assert captured["finish"] == {
        "lease_id": "lease-1",
        "summary": "Done",
        "task_status": "done",
        "tested_commands": ["uv run pytest"],
        "remaining_risks": None,
        "next_action": "review",
        "blocked_on": None,
        "worker_session_id": None,
        "idempotency_key": None,
    }
    assert readiness_output["status"] == "ready"
    assert captured["readiness"] == "run-1"


def test_orchestration_evidence_command_cli_wires_storage(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_record_command(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {"status": "ok", "command_evidence": {"command_evidence_id": "cmd-1"}}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "orchestration_record_command_evidence", fake_record_command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "evidence",
            "command",
            "--run",
            "run-1",
            "--task",
            "task-1",
            "--worker-session-id",
            "worker-1",
            "--command",
            "uv run pytest",
            "--purpose",
            "validation",
            "--exit-code",
            "0",
            "--stdout-summary",
            "passed",
            "--idempotency-key",
            "cmd-key",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["command_evidence"]["command_evidence_id"] == "cmd-1"
    assert captured == {
        "run_id": "run-1",
        "command": "uv run pytest",
        "task_id": "task-1",
        "worker_session_id": "worker-1",
        "purpose": "validation",
        "status": None,
        "exit_code": 0,
        "stdout_summary": "passed",
        "stderr_summary": "",
        "log_path": None,
        "idempotency_key": "cmd-key",
    }


def test_orchestration_approval_review_decision_cli_wire_storage(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_request_approval(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["approval_request"] = kwargs
        return {"status": "ok", "approval": {"approval_id": "approval-1"}}

    def fake_resolve_approval(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["approval_resolve"] = kwargs
        return {"status": "ok", "approval": {"approval_id": "approval-1"}}

    def fake_record_finding(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["finding"] = kwargs
        return {"status": "ok", "finding": {"finding_id": "finding-1"}}

    def fake_resolve_finding(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["finding_resolve"] = kwargs
        return {"status": "ok", "finding": {"finding_id": "finding-1"}}

    def fake_record_decision(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["decision"] = kwargs
        return {"status": "ok", "decision": {"decision_id": "decision-1"}}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "orchestration_request_approval", fake_request_approval)
    monkeypatch.setattr(cli, "orchestration_resolve_approval", fake_resolve_approval)
    monkeypatch.setattr(cli, "orchestration_record_review_finding", fake_record_finding)
    monkeypatch.setattr(cli, "orchestration_resolve_review_finding", fake_resolve_finding)
    monkeypatch.setattr(cli, "orchestration_record_decision", fake_record_decision)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "approval",
            "request",
            "--run",
            "run-1",
            "--reason",
            "deploy",
            "--risk-level",
            "high",
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["approval"]["approval_id"] == "approval-1"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "approval",
            "resolve",
            "approval-1",
            "--status",
            "approved",
            "--resolved-by",
            "human",
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["approval"]["approval_id"] == "approval-1"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "review",
            "finding",
            "--run",
            "run-1",
            "--summary",
            "Missing test",
            "--severity",
            "P1",
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["finding"]["finding_id"] == "finding-1"

    monkeypatch.setattr(
        sys,
        "argv",
        ["geond", "review", "resolve", "finding-1", "--status", "fixed", "--reason", "test added"],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["finding"]["finding_id"] == "finding-1"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "decision",
            "record",
            "--run",
            "run-1",
            "--decision",
            "ship",
            "--evidence-ref",
            "command:cmd-1",
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["decision"]["decision_id"] == "decision-1"

    assert captured["approval_request"]["run_id"] == "run-1"
    assert captured["approval_resolve"]["status"] == "approved"
    assert captured["finding"]["severity"] == "P1"
    assert captured["finding_resolve"]["reason"] == "test added"
    assert captured["decision"]["evidence_refs"] == [{"type": "command", "id": "cmd-1"}]


def test_orchestration_run_summarize_and_manifest_cli(monkeypatch, capsys, tmp_path) -> None:
    captured: dict[str, object] = {}
    package = {"schema": "geond.run_handoff_package.v1", "status": "ok", "run": {"run_id": "run-1"}}
    summary = {"schema": "geond.run_summary.v1", "status": "ok", "markdown": "# Run\n"}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_summarize(conn, run_id: str):  # noqa: ANN001
        captured["summarize"] = run_id
        return summary

    def fake_package(conn, run_id: str):  # noqa: ANN001
        captured["package"] = run_id
        return package

    def fake_manifest(package_payload, summary_markdown, **kwargs):  # noqa: ANN001, ANN202
        captured["manifest"] = {
            "package": package_payload,
            "summary_markdown": summary_markdown,
            **kwargs,
        }
        return {"status": "ok", "run_dir": str(tmp_path / "run-1")}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "orchestration_summarize_run", fake_summarize)
    monkeypatch.setattr(cli, "orchestration_handoff_package", fake_package)
    monkeypatch.setattr(cli, "write_run_manifest", fake_manifest)

    output_path = tmp_path / "summary.md"
    monkeypatch.setattr(
        sys,
        "argv",
        ["geond", "run", "summarize", "run-1", "--output", str(output_path)],
    )
    cli.main()
    assert capsys.readouterr().out == ""
    assert output_path.read_text(encoding="utf-8") == "# Run\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "run",
            "manifest",
            "run-1",
            "--base-dir",
            str(tmp_path),
            "--write-result",
        ],
    )
    cli.main()
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert captured["manifest"]["base_dir"] == tmp_path
    assert captured["manifest"]["write_result"] is True


def test_orchestration_task_graph_cli_wires_storage(monkeypatch, capsys, tmp_path) -> None:
    captured: dict[str, object] = {}
    graph_path = tmp_path / "graph.md"
    graph_path.write_text(
        "- [ ] repro | Reproduce issue | priority=100\n"
        "- [ ] fix | Implement fix | depends_on=repro\n",
        encoding="utf-8",
    )

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_create_graph(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {
            "schema": "geond.task_graph.v1",
            "status": "ok",
            "run_id": kwargs["run_id"],
            "tasks": [{"task_id": "task-1", "title": "Reproduce issue"}],
            "edges": [],
        }

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "orchestration_create_task_graph", fake_create_graph)
    monkeypatch.setattr(
        sys,
        "argv",
        ["geond", "task", "graph", "run-1", "--from", str(graph_path)],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == "geond.task_graph.v1"
    assert captured["run_id"] == "run-1"
    assert [task["key"] for task in captured["tasks"]] == ["repro", "fix"]


def test_degraded_ledger_reconcile_cli_wires_service(monkeypatch, capsys, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_reconcile(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {
            "schema": "geond.degraded_ledger_reconcile.v1",
            "status": "ok",
            "run_id": kwargs["run_id"],
            "dry_run": kwargs["dry_run"],
            "pending_count": 1,
            "applied_count": 0,
            "results": [],
        }

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli.degraded_ledger, "reconcile", fake_reconcile)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "ledger",
            "reconcile",
            "run-1",
            "--base-dir",
            str(tmp_path),
            "--dry-run",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == "geond.degraded_ledger_reconcile.v1"
    assert captured == {"run_id": "run-1", "base_dir": tmp_path, "dry_run": True}


def test_hook_record_cli_wires_service(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_record(conn, payload, **kwargs):  # noqa: ANN001, ANN202
        captured["payload"] = payload
        captured["kwargs"] = kwargs
        return {
            "schema": "geond.agent_hook_event.v1",
            "status": "ok",
            "hook_event": {"event_type": payload["event_type"]},
        }

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli.agent_hooks, "record_hook_event_payload", fake_record)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "hook",
            "record",
            "--workspace",
            "file:///repo",
            "--agent",
            "codex",
            "--event",
            "validation",
            "--session-external-id",
            "codex-session-1",
            "--run",
            "run-1",
            "--task",
            "task-1",
            "--worker-session-id",
            "worker-1",
            "--lease-id",
            "lease-1",
            "--command",
            "uv run pytest",
            "--exit-code",
            "0",
            "--metadata-json",
            '{"source_detail":"pytest"}',
            "--idempotency-key",
            "hook-key",
            "--ttl-minutes",
            "30",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == "geond.agent_hook_event.v1"
    assert captured["payload"]["workspace_id_or_uri"] == "file:///repo"
    assert captured["payload"]["event_type"] == "validation"
    assert captured["payload"]["command"] == "uv run pytest"
    assert captured["payload"]["exit_code"] == 0
    assert captured["payload"]["metadata"] == {"source_detail": "pytest"}
    assert captured["kwargs"] == {"idempotency_key": "hook-key", "ttl_minutes": 30}


def test_hook_record_cli_accepts_payload_file(monkeypatch, capsys, tmp_path) -> None:
    captured: dict[str, object] = {}
    payload_path = tmp_path / "hook.json"
    payload_path.write_text(
        json.dumps(
            {
                "workspace_id_or_uri": "file:///repo",
                "agent_name": "claude",
                "event_type": "session_start",
                "session_external_id": "claude-session-1",
            }
        ),
        encoding="utf-8",
    )

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_record(conn, payload, **kwargs):  # noqa: ANN001, ANN202
        captured["payload"] = payload
        captured["kwargs"] = kwargs
        return {"schema": "geond.agent_hook_event.v1", "status": "ok"}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli.agent_hooks, "record_hook_event_payload", fake_record)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "hook",
            "record",
            "--payload",
            str(payload_path),
            "--idempotency-key",
            "payload-key",
        ],
    )

    cli.main()

    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert captured["payload"]["agent_name"] == "claude"
    assert captured["kwargs"]["idempotency_key"] == "payload-key"


def test_hook_template_cli_writes_templates(monkeypatch, capsys, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_template(**kwargs):  # noqa: ANN202
        captured.update(kwargs)
        return {
            "schema": "geond.agent_hook_template.v1",
            "status": "ok",
            "files": [{"path": str(tmp_path / "codex" / "record-hook.sh")}],
        }

    monkeypatch.setattr(cli.agent_hooks, "write_hook_template", fake_template)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "hook",
            "template",
            "--agent",
            "codex",
            "--output-dir",
            str(tmp_path),
            "--format",
            "shell",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == "geond.agent_hook_template.v1"
    assert captured == {
        "agent_name": "codex",
        "output_dir": tmp_path,
        "template_format": "shell",
    }


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


def test_dashboard_events_cli_wires_filters(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_get_agent_activity_events(conn, workspace_id_or_uri: str, **kwargs):  # noqa: ANN001, ANN202
        captured["workspace_id_or_uri"] = workspace_id_or_uri
        captured.update(kwargs)
        return {"status": "ok", "events": [{"kind": "agent_action"}]}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "get_agent_activity_events", fake_get_agent_activity_events)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "dashboard-events",
            "file:///repo",
            "--limit",
            "7",
            "--kind",
            "agent_action",
            "--agent",
            "copilot",
            "--status",
            "recorded",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["events"][0]["kind"] == "agent_action"
    assert captured == {
        "workspace_id_or_uri": "file:///repo",
        "limit": 7,
        "event_kind": "agent_action",
        "agent_name": "copilot",
        "status": "recorded",
    }


def test_dashboard_code_risk_cli_wires_storage(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_get_dashboard_code_risk(conn, workspace_id_or_uri: str, limit: int):  # noqa: ANN001
        captured["workspace_id_or_uri"] = workspace_id_or_uri
        captured["limit"] = limit
        return {"status": "ok", "summary": {"high": 1}, "files": []}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "get_dashboard_code_risk", fake_get_dashboard_code_risk)
    monkeypatch.setattr(
        sys,
        "argv",
        ["geond", "dashboard-code-risk", "file:///repo", "--limit", "7"],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["high"] == 1
    assert captured == {"workspace_id_or_uri": "file:///repo", "limit": 7}


def test_dashboard_changesets_cli_wires_storage(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_get_dashboard_changesets(conn, workspace_id_or_uri: str, limit: int):  # noqa: ANN001
        captured["workspace_id_or_uri"] = workspace_id_or_uri
        captured["limit"] = limit
        return {"status": "ok", "summary": {"changesets": 1}, "changesets": []}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "get_dashboard_changesets", fake_get_dashboard_changesets)
    monkeypatch.setattr(
        sys,
        "argv",
        ["geond", "dashboard-changesets", "file:///repo", "--limit", "9"],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["changesets"] == 1
    assert captured == {"workspace_id_or_uri": "file:///repo", "limit": 9}


def test_dashboard_graph_cli_wires_lineage(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_get_workspace_lineage(conn, workspace_id_or_uri: str, limit: int):  # noqa: ANN001
        captured["workspace_id_or_uri"] = workspace_id_or_uri
        captured["limit"] = limit
        return {
            "workspace_id": "workspace-1",
            "nodes": [{"kind": "agent", "id": "agent:codex"}],
            "edges": [{"kind": "precedes", "source": "a", "target": "b"}],
        }

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "get_workspace_lineage", fake_get_workspace_lineage)
    monkeypatch.setattr(
        sys,
        "argv",
        ["geond", "dashboard-graph", "file:///repo", "--limit", "11"],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["nodes"][0]["kind"] == "agent"
    assert output["edges"][0]["kind"] == "precedes"
    assert captured == {"workspace_id_or_uri": "file:///repo", "limit": 11}


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


def test_record_agent_run_cli_wires_storage(monkeypatch, tmp_path, capsys) -> None:
    captured: dict[str, object] = {}
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Use Geond evidence refs.", encoding="utf-8")
    final_output_file = tmp_path / "final.txt"
    final_output_file.write_text("Done with token SECRET_TOKEN redacted.", encoding="utf-8")

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_save_agent_run_benchmark(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return "agent-run-1"

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "save_agent_run_benchmark", fake_save_agent_run_benchmark)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "record-agent-run",
            "--agent",
            "antigravity",
            "--command",
            "agy --print",
            "--workspace-uri",
            "file:///repo",
            "--label",
            "smoke",
            "--prompt-file",
            str(prompt_file),
            "--prompt-label",
            "smoke-prompt",
            "--wall-time-ms",
            "1234",
            "--provider",
            "google",
            "--model",
            "gemini-test",
            "--final-output-file",
            str(final_output_file),
            "--stdout-bytes",
            "0",
            "--stderr-bytes",
            "14",
            "--transcript-path",
            "transcript.jsonl",
            "--transcript-session-id",
            "agy-session-1",
            "--geond-session-id",
            "session-row-1",
            "--log-path",
            "agy.log",
            "--input-tokens",
            "10",
            "--total-tokens",
            "30",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "ok", "benchmark_run_id": "agent-run-1"}
    assert captured["agent"] == "antigravity"
    assert captured["command"] == "agy --print"
    assert captured["workspace_uri"] == "file:///repo"
    assert captured["prompt_text"] == "Use Geond evidence refs."
    assert captured["prompt_label"] == "smoke-prompt"
    assert captured["wall_time_ms"] == 1234.0
    assert captured["provider"] == "google"
    assert captured["model"] == "gemini-test"
    assert captured["final_output"] == "Done with token SECRET_TOKEN redacted."
    assert captured["stdout_bytes"] == 0
    assert captured["stderr_bytes"] == 14
    assert captured["transcript_paths"] == ["transcript.jsonl"]
    assert captured["transcript_session_id"] == "agy-session-1"
    assert captured["geond_session_id"] == "session-row-1"
    assert captured["log_paths"] == ["agy.log"]
    assert captured["token_usage"] == {"input_tokens": 10, "total_tokens": 30}
    assert captured["metadata"] == {"source": "cli"}


def test_benchmark_report_cli_routes_agent_run_markdown(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}
    report = {
        "runs": [
            {
                "benchmark_run_id": "run-1",
                "mode": "codex",
                "result": {"agent": "codex"},
            }
        ]
    }

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_compare_agent_run_benchmark_runs(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return report

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(
        cli,
        "compare_agent_run_benchmark_runs",
        fake_compare_agent_run_benchmark_runs,
    )
    monkeypatch.setattr(cli, "format_agent_run_report_markdown", lambda result: "agent table")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "benchmark-report",
            "--kind",
            "agent-run",
            "--workspace-uri",
            "file:///repo",
            "--mode",
            "codex",
            "--limit",
            "3",
            "--format",
            "markdown",
        ],
    )

    cli.main()

    assert capsys.readouterr().out.strip() == "agent table"
    assert captured == {"workspace_uri": "file:///repo", "agent": "codex", "limit": 3}


def test_benchmark_report_cli_routes_all_kind_to_separate_reports(monkeypatch, capsys) -> None:
    captured: dict[str, dict[str, object]] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_compare_benchmark_runs(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["search"] = kwargs
        return {"runs": [{"label": "search"}]}

    def fake_compare_agent_run_benchmark_runs(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["agent_run"] = kwargs
        return {"runs": [{"label": "agent"}]}

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "compare_benchmark_runs", fake_compare_benchmark_runs)
    monkeypatch.setattr(
        cli,
        "compare_agent_run_benchmark_runs",
        fake_compare_agent_run_benchmark_runs,
    )
    monkeypatch.setattr(
        cli,
        "format_combined_benchmark_report_markdown",
        lambda result: (
            f"{result['search']['runs'][0]['label']}+{result['agent_run']['runs'][0]['label']}"
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "benchmark-report",
            "--kind",
            "all",
            "--workspace-uri",
            "file:///repo",
            "--mode",
            "codex",
            "--limit",
            "4",
            "--format",
            "markdown",
        ],
    )

    cli.main()

    assert capsys.readouterr().out.strip() == "search+agent"
    assert captured == {
        "search": {
            "workspace_uri": "file:///repo",
            "mode": "codex",
            "kind": "search",
            "limit": 4,
        },
        "agent_run": {"workspace_uri": "file:///repo", "agent": "codex", "limit": 4},
    }


def test_compare_agents_cli_records_failed_agent_run(monkeypatch, tmp_path, capsys) -> None:
    captured: list[dict[str, object]] = []
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Compare one prompt.", encoding="utf-8")

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_run_agent_compare(*args, **kwargs):  # noqa: ANN001, ANN202
        raise OSError("agy not found")

    def fake_save_agent_run_benchmark(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.append(kwargs)
        return "run-1"

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "run_agent_compare", fake_run_agent_compare)
    monkeypatch.setattr(cli, "save_agent_run_benchmark", fake_save_agent_run_benchmark)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "compare-agents",
            "--prompt-file",
            str(prompt_file),
            "--agent",
            "antigravity",
            "--workspace-uri",
            "file:///repo",
            "--label",
            "smoke",
            "--timeout-seconds",
            "1",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert output["runs"] == [
        {
            "agent": "antigravity",
            "benchmark_run_id": "run-1",
            "wall_time_ms": None,
            "status": "error",
        }
    ]
    assert captured[0]["agent"] == "antigravity"
    assert captured[0]["workspace_uri"] == "file:///repo"
    assert captured[0]["label"] == "smoke"
    assert captured[0]["prompt_text"] == "Compare one prompt."
    assert captured[0]["prompt_label"] == "prompt.txt"
    assert captured[0]["final_output"] == "agy not found"
    assert captured[0]["metadata"] == {
        "source": "compare-agents",
        "status": "error",
        "error_type": "OSError",
    }


def test_resolve_antigravity_run_transcript_accepts_updated_latest_path(
    monkeypatch,
    tmp_path,
) -> None:
    transcript = tmp_path / "agy-session-new" / ".system_generated" / "logs" / "transcript.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(cli, "latest_antigravity_transcript_path", lambda: transcript)

    resolved = cli.resolve_antigravity_run_transcript(
        before_transcript=None,
        started_epoch=transcript.stat().st_mtime + 100,
    )

    assert resolved == transcript


def test_run_agent_compare_codex_prefers_latest_session_transcript(
    monkeypatch,
    tmp_path,
) -> None:
    transcript = tmp_path / "codex-session.jsonl"
    output_paths: list[str] = []

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        output_paths.append(command[-1])
        transcript.write_text(
            '{"type":"session_meta","payload":{"id":"codex-session-1"}}\n',
            encoding="utf-8",
        )
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(cli, "latest_codex_session_path", lambda: transcript)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = cli.run_agent_compare(
        "codex",
        "say hello",
        timeout_seconds=30,
        workspace_root=tmp_path,
    )

    assert result["transcript_paths"] == [str(transcript)]
    assert result["metadata"]["transcript_path"] == str(transcript)
    assert result["metadata"]["final_output_path"] == output_paths[0]


def test_testbed_antigravity_cli_runs_import_search_mcp_and_benchmarks(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    captured: dict[str, object] = {}
    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text("{}", encoding="utf-8")
    session = SimpleNamespace(
        session_id="agy-session-1",
        session_path=transcript_path,
    )

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_run_agent_compare(*args, **kwargs):  # noqa: ANN001, ANN202
        captured["run_agent_compare"] = {"args": args, "kwargs": kwargs}
        return {
            "command": "agy --print",
            "wall_time_ms": 12.3,
            "final_output": "GEOND_MARKER",
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "log_paths": ["agy.log"],
            "metadata": {"returncode": 0},
        }

    def fake_parse_antigravity_storage(*args, **kwargs):  # noqa: ANN001, ANN202
        captured["parse_antigravity_storage"] = {"args": args, "kwargs": kwargs}
        return [session]

    def fake_upsert_workspace(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["upsert_workspace"] = kwargs
        return "workspace-1"

    def fake_store_antigravity_session(conn, workspace_id, parsed_session):  # noqa: ANN001
        captured["store_antigravity_session"] = {
            "workspace_id": workspace_id,
            "session_id": parsed_session.session_id,
        }
        return "session-row-1"

    def fake_search_dev_memory(conn, query, **kwargs):  # noqa: ANN001, ANN202
        captured["search_dev_memory"] = {"query": query, **kwargs}
        return [{"message_id": "message-1"}]

    def fake_run_stdio_smoke(**kwargs):  # noqa: ANN202
        captured["run_stdio_smoke"] = kwargs
        return {
            "status": "ok",
            "checks": [
                {
                    "name": "call_search_dev_memory",
                    "metadata": {"result_count": 1},
                }
            ],
        }

    def fake_save_agent_run_benchmark(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["save_agent_run_benchmark"] = kwargs
        return "agent-run-1"

    def fake_benchmark_search(conn, queries, **kwargs):  # noqa: ANN001, ANN202
        captured["benchmark_search"] = {"queries": queries, **kwargs}
        return {"mode": "keyword", "repeat": 1, "queries": []}

    def fake_save_benchmark_run(conn, result, **kwargs):  # noqa: ANN001, ANN202
        captured["save_benchmark_run"] = {"result": result, **kwargs}
        return "search-run-1"

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "run_agent_compare", fake_run_agent_compare)
    monkeypatch.setattr(cli, "parse_antigravity_storage", fake_parse_antigravity_storage)
    monkeypatch.setattr(cli, "upsert_workspace", fake_upsert_workspace)
    monkeypatch.setattr(cli, "store_antigravity_session", fake_store_antigravity_session)
    monkeypatch.setattr(cli, "search_dev_memory", fake_search_dev_memory)
    monkeypatch.setattr(cli, "run_stdio_smoke", fake_run_stdio_smoke)
    monkeypatch.setattr(cli, "save_agent_run_benchmark", fake_save_agent_run_benchmark)
    monkeypatch.setattr(cli, "benchmark_search", fake_benchmark_search)
    monkeypatch.setattr(cli, "save_benchmark_run", fake_save_benchmark_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond",
            "testbed-antigravity",
            "--workspace-uri",
            "file:///repo",
            "--workspace-name",
            "repo",
            "--marker",
            "GEOND_MARKER",
            "--prompt",
            "Use GEOND_MARKER",
            "--save-benchmark",
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert output["marker"] == "GEOND_MARKER"
    assert output["cli_search_result_count"] == 1
    assert output["imported_sessions"][0]["external_id"] == "agy-session-1"
    assert output["benchmark_run_ids"] == {
        "agent_run": "agent-run-1",
        "search": "search-run-1",
    }
    assert captured["search_dev_memory"] == {
        "query": "GEOND_MARKER",
        "limit": 5,
        "workspace_uri": "file:///repo",
        "source": "antigravity",
    }
    assert captured["save_agent_run_benchmark"]["transcript_paths"] == [str(transcript_path)]
    assert captured["save_agent_run_benchmark"]["transcript_session_id"] == "agy-session-1"
    assert captured["save_agent_run_benchmark"]["geond_session_id"] == "session-row-1"
    assert captured["benchmark_search"]["queries"] == ["GEOND_MARKER"]
