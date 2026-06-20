from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import geond.cli as geond_cli
from geond import orchestrator_cli
from geond_orchestrator import orchestrator_cli as canonical_orchestrator_cli


class FakeConnection:
    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def test_legacy_orchestrator_cli_module_aliases_canonical_package() -> None:
    assert orchestrator_cli is canonical_orchestrator_cli


def test_geond_orch_delegates_to_orchestrator_cli(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_main(*, prog: str = "geond-orchestrator") -> None:
        captured["argv"] = list(sys.argv)
        captured["prog"] = prog

    monkeypatch.setattr(canonical_orchestrator_cli, "main", fake_main)
    monkeypatch.setattr(
        sys,
        "argv",
        ["geond", "orch", "status", "run-1", "--format", "json"],
    )

    geond_cli.main()

    assert captured["argv"] == ["geond-orchestrator", "status", "run-1", "--format", "json"]
    assert captured["prog"] == "geond orch"


def test_orchestrator_worker_alias_delegates_to_protocol_cli(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_main() -> None:
        captured["argv"] = list(sys.argv)

    monkeypatch.setattr(geond_cli, "main", fake_main)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "worker",
            "claim",
            "--run",
            "run-1",
            "--agent",
            "codex",
        ],
    )

    orchestrator_cli.main()

    assert captured["argv"] == ["geond", "worker", "claim", "--run", "run-1", "--agent", "codex"]


def test_orchestrator_ci_watch_alias_delegates_to_gh(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, *, check):  # noqa: ANN001, ANN202
        captured["command"] = command
        captured["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(orchestrator_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "ci",
            "watch",
            "123456",
            "--repo",
            "geondongkim/geond-agent-protocol",
            "--exit-status",
            "--compact",
            "--interval",
            "5",
        ],
    )

    orchestrator_cli.main()

    assert captured["command"] == [
        "gh",
        "run",
        "watch",
        "123456",
        "--repo",
        "geondongkim/geond-agent-protocol",
        "--exit-status",
        "--compact",
        "--interval",
        "5",
    ]
    assert captured["check"] is False


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
            "schema": "geond.orchestrator_control.v1",
            "status": "ok",
            "markdown": "# Plan\n",
        }

    def fake_agent(conn, run_id, **kwargs):  # noqa: ANN001, ANN202
        captured["agent"] = {"run_id": run_id, **kwargs}
        return {
            "schema": "geond.orchestrator_control.v1",
            "status": "ok",
            "markdown": "# Agent\n",
        }

    def fake_doctor(conn, run_id, **kwargs):  # noqa: ANN001, ANN202
        captured["doctor"] = {"run_id": run_id, **kwargs}
        return {
            "schema": "geond.orchestrator_plan.v1",
            "status": "ok",
            "markdown": "# Doctor\n",
        }

    def fake_actions(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["actions"] = kwargs
        return {
            "schema": "geond.orchestrator_action_bundle.v1",
            "status": "ok",
            "markdown": "# Actions\n",
        }

    monkeypatch.setattr(orchestrator_cli, "connect", fake_connect)
    monkeypatch.setattr(orchestrator_cli.orchestrator, "get_status", fake_status)
    monkeypatch.setattr(orchestrator_cli.orchestrator, "dispatch_claim", fake_dispatch)
    monkeypatch.setattr(orchestrator_cli.orchestrator, "resume_run", fake_resume)
    monkeypatch.setattr(orchestrator_cli.orchestrator, "finalize_run", fake_finalize)
    monkeypatch.setattr(orchestrator_cli.orchestrator_control, "run_plan_mode", fake_plan)
    monkeypatch.setattr(orchestrator_cli.orchestrator_control, "run_agent_mode", fake_agent)
    monkeypatch.setattr(orchestrator_cli.orchestrator_planner, "doctor_run", fake_doctor)
    monkeypatch.setattr(
        orchestrator_cli.orchestrator_action_bundle,
        "build_action_bundle",
        fake_actions,
    )

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
            "--propose-task-graph",
            "--planner",
            "llm",
            "--template",
            "bugfix",
            "--planner-agent",
            "claude",
            "--write-bundle",
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Plan\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "actions",
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
    assert capsys.readouterr().out == "# Actions\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "agent",
            "run-1",
            "--execute",
            "--allow-task-graph-create",
            "--allow-llm-planner",
            "--execute-planner",
            "--max-steps",
            "2",
            "--agents",
            "codex,claude",
            "--planner",
            "llm",
            "--template",
            "implementation",
            "--planner-agent",
            "claude",
            "--max-workers",
            "2",
            "--model",
            "gpt-5",
            "--sandbox",
            "workspace-write",
            "--timeout-seconds",
            "12",
            "--write-bundle",
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Agent\n"

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
    assert captured["plan"]["propose_task_graph"] is True
    assert captured["plan"]["planner"] == "llm"
    assert captured["plan"]["template"] == "bugfix"
    assert captured["plan"]["planner_agent"] == "claude"
    assert captured["plan"]["write_bundle"] is True
    assert captured["actions"]["workspace_id_or_uri"] == "file:///repo"
    assert captured["actions"]["run_id"] == "run-1"
    assert captured["actions"]["agents"] == ["codex", "claude"]
    assert captured["actions"]["write_bundle"] is True
    assert captured["agent"]["run_id"] == "run-1"
    assert captured["agent"]["execute"] is True
    assert captured["agent"]["allow_task_graph_create"] is True
    assert captured["agent"]["allow_llm_planner"] is True
    assert captured["agent"]["execute_planner"] is True
    assert captured["agent"]["max_steps"] == 2
    assert captured["agent"]["agents"] == ["codex", "claude"]
    assert captured["agent"]["planner"] == "llm"
    assert captured["agent"]["template"] == "implementation"
    assert captured["agent"]["planner_agent"] == "claude"
    assert captured["agent"]["max_workers"] == 2
    assert captured["agent"]["model"] == "gpt-5"
    assert captured["agent"]["timeout_seconds"] == 12
    assert captured["agent"]["write_bundle"] is True
    assert captured["doctor"]["run_id"] == "run-1"
    assert captured["doctor"]["agents"] == ["claude"]
    assert captured["dispatch"]["run_id"] == "run-1"
    assert captured["dispatch"]["agent_name"] == "claude"
    assert captured["resume"]["run_id"] == "run-1"
    assert captured["finalize"]["write_manifest"] is True
    assert captured["finalize"]["git_checkpoint"] is True
    assert captured["finalize"]["dry_run"] is True


def test_orchestrator_action_cli_wires_queue_service(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_queue(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["queue"] = kwargs
        return {
            "schema": "geond.orchestrator_action_queue.v1",
            "status": "ok",
            "markdown": "# Queue\n",
        }

    def fake_list(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["list"] = kwargs
        return {
            "schema": "geond.orchestrator_action_queue.v1",
            "status": "ok",
            "markdown": "# List\n",
        }

    def fake_approve(**kwargs):  # noqa: ANN001, ANN202
        captured["approve"] = kwargs
        return {
            "schema": "geond.orchestrator_action_event.v1",
            "status": "ok",
            "markdown": "# Approve\n",
        }

    def fake_reject(**kwargs):  # noqa: ANN001, ANN202
        captured["reject"] = kwargs
        return {
            "schema": "geond.orchestrator_action_event.v1",
            "status": "ok",
            "markdown": "# Reject\n",
        }

    def fake_execute(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["execute"] = kwargs
        return {
            "schema": "geond.orchestrator_action_execution.v1",
            "status": "ok",
            "markdown": "# Execute\n",
        }

    monkeypatch.setattr(orchestrator_cli, "connect", fake_connect)
    monkeypatch.setattr(
        orchestrator_cli.orchestrator_action_queue,
        "queue_actions_from_bundle",
        fake_queue,
    )
    monkeypatch.setattr(orchestrator_cli.orchestrator_action_queue, "list_action_queue", fake_list)
    monkeypatch.setattr(orchestrator_cli.orchestrator_action_queue, "approve_action", fake_approve)
    monkeypatch.setattr(orchestrator_cli.orchestrator_action_queue, "reject_action", fake_reject)
    monkeypatch.setattr(
        orchestrator_cli.orchestrator_action_queue,
        "execute_queued_action",
        fake_execute,
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "action",
            "queue",
            "--workspace",
            "file:///repo",
            "--run",
            "run-1",
            "--agents",
            "codex,claude",
            "--write-bundle",
            "--base-dir",
            str(tmp_path),
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Queue\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "action",
            "list",
            "--workspace",
            "file:///repo",
            "--run",
            "run-1",
            "--agents",
            "claude",
            "--base-dir",
            str(tmp_path),
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# List\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "action",
            "approve",
            "run-1",
            "action-1",
            "--approved-by",
            "human",
            "--reason",
            "safe",
            "--base-dir",
            str(tmp_path),
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Approve\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "action",
            "reject",
            "run-1",
            "action-1",
            "--rejected-by",
            "human",
            "--reason",
            "not now",
            "--base-dir",
            str(tmp_path),
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Reject\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "action",
            "execute",
            "run-1",
            "action-1",
            "--execute",
            "--agents",
            "codex,claude",
            "--max-workers",
            "2",
            "--model",
            "gpt-5",
            "--sandbox",
            "workspace-write",
            "--timeout-seconds",
            "12",
            "--base-dir",
            str(tmp_path),
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Execute\n"

    assert captured["queue"]["workspace_id_or_uri"] == "file:///repo"
    assert captured["queue"]["run_id"] == "run-1"
    assert captured["queue"]["agents"] == ["codex", "claude"]
    assert captured["queue"]["write_bundle"] is True
    assert captured["list"]["agents"] == ["claude"]
    assert captured["approve"]["approved_by"] == "human"
    assert captured["approve"]["reason"] == "safe"
    assert captured["reject"]["rejected_by"] == "human"
    assert captured["reject"]["reason"] == "not now"
    assert captured["execute"]["execute"] is True
    assert captured["execute"]["agents"] == ["codex", "claude"]
    assert captured["execute"]["max_workers"] == 2
    assert captured["execute"]["model"] == "gpt-5"
    assert captured["execute"]["timeout_seconds"] == 12


def test_orchestrator_scheduler_cli_wires_service(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_plan(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["plan"] = kwargs
        return {
            "schema": "geond.orchestrator_scheduler.v1",
            "status": "ok",
            "markdown": "# Scheduler Plan\n",
        }

    def fake_drain(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["drain"] = kwargs
        return {
            "schema": "geond.orchestrator_scheduler.v1",
            "status": "ok",
            "markdown": "# Scheduler Drain\n",
        }

    monkeypatch.setattr(orchestrator_cli, "connect", fake_connect)
    monkeypatch.setattr(orchestrator_cli.orchestrator_scheduler, "plan_scheduler", fake_plan)
    monkeypatch.setattr(orchestrator_cli.orchestrator_scheduler, "drain_scheduler", fake_drain)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "scheduler",
            "plan",
            "--workspace",
            "file:///repo",
            "--run",
            "run-1",
            "--agents",
            "codex,claude",
            "--max-actions",
            "3",
            "--max-workers",
            "2",
            "--model",
            "gpt-5",
            "--budget-actions",
            "5",
            "--budget-spawn-actions",
            "1",
            "--budget-tokens",
            "1000",
            "--budget-cost-usd",
            "3.50",
            "--budget-window-hours",
            "12",
            "--estimate-spawn-tokens",
            "250",
            "--estimate-spawn-cost-usd",
            "0.25",
            "--write-bundle",
            "--base-dir",
            str(tmp_path),
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Scheduler Plan\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "scheduler",
            "drain",
            "--workspace",
            "file:///repo",
            "--run",
            "run-1",
            "--execute",
            "--agents",
            "claude",
            "--max-actions",
            "2",
            "--max-workers",
            "1",
            "--sandbox",
            "workspace-write",
            "--timeout-seconds",
            "12",
            "--base-dir",
            str(tmp_path),
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Scheduler Drain\n"

    assert captured["plan"]["workspace_id_or_uri"] == "file:///repo"
    assert captured["plan"]["run_id"] == "run-1"
    assert captured["plan"]["agents"] == ["codex", "claude"]
    assert captured["plan"]["max_actions"] == 3
    assert captured["plan"]["max_workers"] == 2
    assert captured["plan"]["model"] == "gpt-5"
    assert captured["plan"]["budget_actions"] == 5
    assert captured["plan"]["budget_spawn_actions"] == 1
    assert captured["plan"]["budget_tokens"] == 1000
    assert captured["plan"]["budget_cost_usd"] == "3.50"
    assert captured["plan"]["budget_window_hours"] == 12
    assert captured["plan"]["estimate_spawn_tokens"] == 250
    assert captured["plan"]["estimate_spawn_cost_usd"] == "0.25"
    assert captured["plan"]["write_bundle"] is True
    assert captured["drain"]["execute"] is True
    assert captured["drain"]["agents"] == ["claude"]
    assert captured["drain"]["max_actions"] == 2
    assert captured["drain"]["timeout_seconds"] == 12


def test_orchestrator_budget_and_daemon_cli_wires_services(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_budget(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["budget"] = kwargs
        return {
            "schema": "geond.orchestrator_budget_report.v1",
            "status": "ok",
            "markdown": "# Budget\n",
        }

    def fake_once(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["once"] = kwargs
        return {
            "schema": "geond.orchestrator_daemon.v1",
            "status": "ok",
            "markdown": "# Daemon Once\n",
        }

    def fake_loop(conn, **kwargs):  # noqa: ANN001, ANN202
        captured["loop"] = kwargs
        return {
            "schema": "geond.orchestrator_daemon.v1",
            "status": "ok",
            "markdown": "# Daemon Loop\n",
        }

    monkeypatch.setattr(orchestrator_cli, "connect", fake_connect)
    monkeypatch.setattr(orchestrator_cli.orchestrator_budget, "build_budget_report", fake_budget)
    monkeypatch.setattr(orchestrator_cli.orchestrator_daemon, "run_daemon_once", fake_once)
    monkeypatch.setattr(orchestrator_cli.orchestrator_daemon, "run_daemon_loop", fake_loop)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "budget",
            "report",
            "--workspace",
            "file:///repo",
            "--run",
            "run-1",
            "--budget-tokens",
            "100",
            "--budget-cost-usd",
            "1.25",
            "--estimate-spawn-tokens",
            "20",
            "--base-dir",
            str(tmp_path),
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Budget\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "daemon",
            "once",
            "--workspace",
            "file:///repo",
            "--run",
            "run-1",
            "--execute",
            "--interval-seconds",
            "5",
            "--base-dir",
            str(tmp_path),
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Daemon Once\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "daemon",
            "run",
            "--workspace",
            "file:///repo",
            "--max-cycles",
            "2",
            "--forever",
            "--base-dir",
            str(tmp_path),
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Daemon Loop\n"

    assert captured["budget"]["workspace_id_or_uri"] == "file:///repo"
    assert captured["budget"]["budget_tokens"] == 100
    assert captured["budget"]["budget_cost_usd"] == "1.25"
    assert captured["budget"]["estimate_spawn_tokens"] == 20
    assert captured["once"]["execute"] is True
    assert captured["once"]["interval_seconds"] == 5
    assert captured["loop"]["max_cycles"] == 2
    assert captured["loop"]["forever"] is True


def test_orchestrator_graph_cli_wires_task_planner(monkeypatch, capsys, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    source_path = tmp_path / "proposal.json"
    output_path = tmp_path / "out.json"
    source_path.write_text('{"tasks":[{"key":"design","title":"Design"}]}', encoding="utf-8")

    def fake_connect(settings) -> FakeConnection:  # noqa: ANN001
        return FakeConnection()

    def fake_propose(conn, run_id, **kwargs):  # noqa: ANN001, ANN202
        captured["propose"] = {"run_id": run_id, **kwargs}
        return {
            "schema": "geond.task_graph_proposal.v1",
            "status": "ok",
            "proposal_id": "proposal-1",
            "tasks": [],
            "markdown": "# Proposal\n",
        }

    def fake_write(payload, path):  # noqa: ANN001, ANN202
        captured["write"] = {"payload": payload, "path": path}
        path.write_text("{}", encoding="utf-8")
        return {"proposal_path": str(path)}

    def fake_apply(conn, run_id, path, **kwargs):  # noqa: ANN001, ANN202
        captured["apply"] = {"run_id": run_id, "path": path, **kwargs}
        return {
            "schema": "geond.task_graph_materialization.v1",
            "status": "ok",
            "markdown": "# Apply\n",
        }

    def fake_review_file(conn, run_id, path, **kwargs):  # noqa: ANN001, ANN202
        captured["review_file"] = {"run_id": run_id, "path": path, **kwargs}
        return {
            "schema": "geond.task_graph_review.v1",
            "status": "ok",
            "markdown": "# Review\n",
        }

    def fake_review_latest(conn, run_id, **kwargs):  # noqa: ANN001, ANN202
        captured["review_latest"] = {"run_id": run_id, **kwargs}
        return {
            "schema": "geond.task_graph_review.v1",
            "status": "ok",
            "markdown": "# Review Latest\n",
        }

    monkeypatch.setattr(orchestrator_cli, "connect", fake_connect)
    monkeypatch.setattr(
        orchestrator_cli.orchestrator_task_planner,
        "propose_task_graph",
        fake_propose,
    )
    monkeypatch.setattr(orchestrator_cli.orchestrator_task_planner, "write_proposal", fake_write)
    monkeypatch.setattr(
        orchestrator_cli.orchestrator_task_planner,
        "apply_task_graph_file",
        fake_apply,
    )
    monkeypatch.setattr(
        orchestrator_cli.orchestrator_graph_review,
        "review_task_graph_file",
        fake_review_file,
    )
    monkeypatch.setattr(
        orchestrator_cli.orchestrator_graph_review,
        "review_latest_planner_result",
        fake_review_latest,
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "graph",
            "propose",
            "run-1",
            "--planner",
            "llm",
            "--agent",
            "claude",
            "--execute-planner",
            "--template",
            "docs",
            "--base-dir",
            str(tmp_path / "runs"),
            "--model",
            "opus",
            "--sandbox",
            "workspace-write",
            "--timeout-seconds",
            "22",
            "--output",
            str(output_path),
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Proposal\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "graph",
            "apply",
            "run-1",
            "--from",
            str(source_path),
            "--execute",
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Apply\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "graph",
            "review",
            "run-1",
            "--from",
            str(source_path),
            "--write-bundle",
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Review\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "geond-orchestrator",
            "graph",
            "review",
            "run-1",
            "--latest-planner",
            "--base-dir",
            str(tmp_path / "runs"),
        ],
    )
    orchestrator_cli.main()
    assert capsys.readouterr().out == "# Review Latest\n"

    assert captured["propose"] == {
        "run_id": "run-1",
        "planner": "llm",
        "template": "docs",
        "agent_name": "claude",
        "execute_planner": True,
        "base_dir": tmp_path / "runs",
        "model": "opus",
        "sandbox": "workspace-write",
        "timeout_seconds": 22,
    }
    assert captured["write"]["path"] == output_path
    assert captured["apply"] == {"run_id": "run-1", "path": source_path, "execute": True}
    assert captured["review_file"] == {
        "run_id": "run-1",
        "path": source_path,
        "base_dir": orchestrator_cli.orchestrator.DEFAULT_MANIFEST_BASE_DIR,
        "write_bundle": True,
    }
    assert captured["review_latest"] == {
        "run_id": "run-1",
        "base_dir": tmp_path / "runs",
        "write_bundle": False,
    }


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
