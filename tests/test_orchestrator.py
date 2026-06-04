from __future__ import annotations

from pathlib import Path

from geond import orchestrator


def sample_package(run_id: str = "run-1", *, claimable: bool = True) -> dict[str, object]:
    task = {
        "task_id": "task-1",
        "title": "Implement task",
        "status": "ready" if claimable else "done",
    }
    return {
        "schema": "geond.run_handoff_package.v1",
        "status": "ok",
        "run": {
            "run_id": run_id,
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "title": "Fix checkout flow",
            "risk_level": "medium",
            "status": "active",
        },
        "tasks": [task],
        "workers": [{"worker_session_id": "worker-1", "status": "active"}],
        "review_findings": [{"finding_id": "finding-1", "status": "open", "summary": "Fix me"}],
        "approval_requests": [{"approval_id": "approval-1", "status": "requested"}],
        "decisions": [{"decision_id": "decision-1", "decision": "Use claim mode"}],
    }


def ready_report() -> dict[str, object]:
    return {
        "schema": "geond.readiness_report.v1",
        "status": "ready",
        "blocking_reasons": [],
    }


def test_start_run_creates_goal_run_task_and_status(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_create_goal(conn, workspace, title, **kwargs):  # noqa: ANN001, ANN202
        calls.append(("goal", {"workspace": workspace, "title": title, **kwargs}))
        return {"status": "ok", "goal": {"goal_id": "goal-1", "title": title}}

    def fake_create_run(conn, workspace, title, **kwargs):  # noqa: ANN001, ANN202
        calls.append(("run", {"workspace": workspace, "title": title, **kwargs}))
        return {
            "status": "ok",
            "run": {"run_id": "run-1", "title": title, "risk_level": kwargs["risk_level"]},
        }

    def fake_create_task(conn, run_id, title, **kwargs):  # noqa: ANN001, ANN202
        calls.append(("task", {"run_id": run_id, "title": title, **kwargs}))
        return {"status": "ok", "task": {"task_id": "task-1", "title": title}}

    monkeypatch.setattr(orchestrator.orchestration_store, "create_goal", fake_create_goal)
    monkeypatch.setattr(orchestrator.orchestration_store, "create_run", fake_create_run)
    monkeypatch.setattr(orchestrator.orchestration_store, "create_task", fake_create_task)
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_run_handoff_package",
        lambda conn, run_id, limit=100: sample_package(run_id),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_readiness_report",
        lambda conn, run_id: ready_report(),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_claimable_tasks",
        lambda conn, **kwargs: {"status": "ok", "tasks": [{"task_id": "task-1", "title": "Task"}]},
    )

    payload = orchestrator.start_run(
        object(),
        goal="Fix checkout flow",
        workspace_id_or_uri="file:///repo",
        risk_level="medium",
    )

    assert payload["schema"] == "geond.orchestrator_run.v1"
    assert [name for name, _ in calls] == ["goal", "run", "task"]
    assert calls[1][1]["goal_id"] == "goal-1"
    assert payload["orchestrator_status"]["schema"] == "geond.orchestrator_status.v1"


def test_start_run_can_seed_task_graph(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    graph_path = tmp_path / "graph.md"
    graph_path.write_text("- [ ] repro | Reproduce issue | priority=100\n", encoding="utf-8")

    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "create_goal",
        lambda conn, workspace, title, **kwargs: {
            "status": "ok",
            "goal": {"goal_id": "goal-1", "title": title},
        },
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "create_run",
        lambda conn, workspace, title, **kwargs: {
            "status": "ok",
            "run": {"run_id": "run-1", "title": title, "risk_level": kwargs["risk_level"]},
        },
    )

    def fake_create_task(conn, run_id, title, **kwargs):  # noqa: ANN001, ANN202
        calls.append(("planning_task", {"run_id": run_id, "title": title, **kwargs}))
        return {"status": "ok", "task": {"task_id": "planning-task", "title": title}}

    def fake_create_graph(conn, run_id, tasks, **kwargs):  # noqa: ANN001, ANN202
        calls.append(("graph", {"run_id": run_id, "tasks": tasks, **kwargs}))
        return {"status": "ok", "tasks": [{"task_id": "task-1"}], "edges": []}

    monkeypatch.setattr(orchestrator.orchestration_store, "create_task", fake_create_task)
    monkeypatch.setattr(orchestrator.orchestration_store, "create_task_graph", fake_create_graph)
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_run_handoff_package",
        lambda conn, run_id, limit=100: sample_package(run_id),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_readiness_report",
        lambda conn, run_id: ready_report(),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_claimable_tasks",
        lambda conn, **kwargs: {"status": "ok", "tasks": [{"task_id": "task-1", "title": "Task"}]},
    )

    payload = orchestrator.start_run(
        object(),
        goal="Fix checkout flow",
        workspace_id_or_uri="file:///repo",
        task_graph_path=graph_path,
    )

    assert payload["task_graph"]["status"] == "ok"
    assert calls[0][1]["status"] == "done"
    assert calls[1][1]["tasks"][0]["key"] == "repro"


def test_status_and_dispatch_return_claim_mode_commands(monkeypatch) -> None:
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_run_handoff_package",
        lambda conn, run_id, limit=100: sample_package(run_id),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_readiness_report",
        lambda conn, run_id: ready_report(),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_claimable_tasks",
        lambda conn, **kwargs: {"status": "ok", "tasks": [{"task_id": "task-1", "title": "Task"}]},
    )

    status = orchestrator.get_status(object(), "run-1", agent_name="claude")
    dispatch = orchestrator.dispatch_claim(object(), run_id="run-1", agent_name="claude")

    assert status["schema"] == "geond.orchestrator_status.v1"
    assert status["active_workers"][0]["worker_session_id"] == "worker-1"
    assert status["open_findings"][0]["finding_id"] == "finding-1"
    assert (
        "geond worker register run-1 --agent claude" in status["next_worker_commands"][0]["command"]
    )
    assert any(
        "geond worker claim --task-id task-1" in item["command"]
        for item in dispatch["next_worker_commands"]
    )


def test_status_without_claimable_tasks_suggests_create_task(monkeypatch) -> None:
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_run_handoff_package",
        lambda conn, run_id, limit=100: sample_package(run_id, claimable=False),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_readiness_report",
        lambda conn, run_id: {"schema": "geond.readiness_report.v1", "status": "not_ready"},
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_claimable_tasks",
        lambda conn, **kwargs: {"status": "ok", "tasks": []},
    )

    status = orchestrator.get_status(object(), "run-1")

    assert status["status"] == "ok"
    assert status["next_action"] == "create or release a task before dispatch"
    assert status["next_worker_commands"][1]["command"].startswith("geond task create run-1")


def test_finalize_gates_manifest_on_readiness(monkeypatch, tmp_path: Path) -> None:
    writes: list[dict[str, object]] = []
    readiness = {
        "schema": "geond.readiness_report.v1",
        "status": "not_ready",
        "blocking_reasons": ["test"],
    }

    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_run_handoff_package",
        lambda conn, run_id, limit=100: sample_package(run_id),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_readiness_report",
        lambda conn, run_id: readiness,
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_claimable_tasks",
        lambda conn, **kwargs: {"status": "ok", "tasks": []},
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "summarize_run",
        lambda conn, run_id: {"status": "ok", "markdown": "# Summary\n"},
    )

    def fake_write_manifest(package, markdown, **kwargs):  # noqa: ANN001, ANN202
        writes.append({"package": package, "markdown": markdown, **kwargs})
        return {"status": "ok", "run_dir": str(tmp_path / "run-1")}

    monkeypatch.setattr(orchestrator, "write_run_manifest", fake_write_manifest)

    blocked = orchestrator.finalize_run(
        object(),
        "run-1",
        write_manifest=True,
        manifest_base_dir=tmp_path,
    )
    assert blocked["status"] == "not_ready"
    assert writes == []

    readiness["status"] = "ready"
    readiness["blocking_reasons"] = []
    ready = orchestrator.finalize_run(
        object(),
        "run-1",
        write_manifest=True,
        manifest_base_dir=tmp_path,
    )
    assert ready["status"] == "ok"
    assert writes[0]["base_dir"] == tmp_path


def patch_ready_finalize_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_run_handoff_package",
        lambda conn, run_id, limit=100: sample_package(run_id),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_readiness_report",
        lambda conn, run_id: ready_report(),
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "get_claimable_tasks",
        lambda conn, **kwargs: {"status": "ok", "tasks": []},
    )
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "summarize_run",
        lambda conn, run_id: {"status": "ok", "markdown": "# Summary\n"},
    )
    monkeypatch.setattr(
        orchestrator.orchestrator_spawn,
        "get_workspace_root_uri",
        lambda conn, workspace_id: tmp_path.as_uri(),
    )


def test_finalize_git_checkpoint_validates_safety_gates(monkeypatch, tmp_path: Path) -> None:
    patch_ready_finalize_context(monkeypatch, tmp_path)

    missing_target = orchestrator.finalize_run(
        object(),
        "run-1",
        commit=True,
        manifest_base_dir=tmp_path,
    )
    dry_run = orchestrator.finalize_run(
        object(),
        "run-1",
        git_checkpoint=True,
        commit=True,
        stage_all=True,
        push=True,
        create_pr=True,
        dry_run=True,
        manifest_base_dir=tmp_path,
    )

    assert missing_target["status"] == "error"
    assert missing_target["code"] == "GIT_STAGE_TARGET_REQUIRED"
    assert dry_run["status"] == "ok"
    assert dry_run["git_result"]["dry_run"] is True
    planned = [item["command"] for item in dry_run["git_result"]["planned_commands"]]
    assert any(command.startswith("git commit") for command in planned)
    assert any(command.startswith("git push") for command in planned)
    assert any(command.startswith("gh pr create") for command in planned)


def test_finalize_git_mutation_blocks_on_pending_degraded_ledger(
    monkeypatch,
    tmp_path: Path,
) -> None:
    status_payload = {
        "schema": "geond.orchestrator_status.v1",
        "status": "ok",
        "run": sample_package("run-1")["run"],
        "readiness": ready_report(),
        "degraded_ledger": {"pending_count": 1},
    }
    monkeypatch.setattr(orchestrator, "get_status", lambda *args, **kwargs: status_payload)
    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "summarize_run",
        lambda conn, run_id: {"status": "ok", "markdown": "# Summary\n"},
    )

    payload = orchestrator.finalize_run(
        object(),
        "run-1",
        commit=True,
        stage_all=True,
        manifest_base_dir=tmp_path,
    )

    assert payload["status"] == "error"
    assert payload["code"] == "DEGRADED_LEDGER_PENDING"


def test_finalize_git_records_command_evidence_and_decision(monkeypatch, tmp_path: Path) -> None:
    patch_ready_finalize_context(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator.orchestrator_finalize, "find_gh_binary", lambda: "/usr/bin/gh")
    evidence_commands: list[str] = []
    decisions: list[dict[str, object]] = []

    def fake_record_evidence(conn, run_id, command, **kwargs):  # noqa: ANN001, ANN202
        evidence_commands.append(command)
        return {"status": "ok", "command_evidence": {"command_evidence_id": command}}

    def fake_record_decision(conn, run_id, decision, **kwargs):  # noqa: ANN001, ANN202
        decisions.append({"decision": decision, **kwargs})
        return {"status": "ok", "decision": {"decision_id": "decision-1"}}

    def fake_runner(command, *, cwd, timeout_seconds):  # noqa: ANN001, ANN202
        stdout = ""
        if command[:2] == ["git", "rev-parse"]:
            stdout = "abc123\n"
        elif command[:2] == ["git", "branch"]:
            stdout = "codex/test\n"
        elif command[:3] == ["gh", "pr", "create"]:
            stdout = "https://github.com/example/repo/pull/1\n"
        return {"command": command, "exit_code": 0, "stdout": stdout, "stderr": ""}

    monkeypatch.setattr(
        orchestrator.orchestration_store,
        "record_command_evidence",
        fake_record_evidence,
    )
    monkeypatch.setattr(orchestrator.orchestration_store, "record_decision", fake_record_decision)

    payload = orchestrator.finalize_run(
        object(),
        "run-1",
        git_checkpoint=True,
        commit=True,
        stage_all=True,
        commit_message="Finalize run",
        push=True,
        create_pr=True,
        pr_title="Finalize run",
        manifest_base_dir=tmp_path,
        command_runner=fake_runner,
    )

    assert payload["status"] == "ok"
    assert payload["git_result"]["commit_sha"] == "abc123"
    assert payload["git_result"]["branch"] == "codex/test"
    assert payload["git_result"]["pr_url"] == "https://github.com/example/repo/pull/1"
    assert any(command.startswith("git commit") for command in evidence_commands)
    assert decisions[0]["metadata"]["git"]["commit_sha"] == "abc123"
    assert decisions[0]["metadata"]["git"]["pr_url"] == "https://github.com/example/repo/pull/1"
