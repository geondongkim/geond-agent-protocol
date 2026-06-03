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
    assert "geond worker register run-1 --agent claude" in status["next_worker_commands"][0]["command"]
    assert any("geond worker claim --task-id task-1" in item["command"] for item in dispatch["next_worker_commands"])


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
    readiness = {"schema": "geond.readiness_report.v1", "status": "not_ready", "blocking_reasons": ["test"]}

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

    blocked = orchestrator.finalize_run(object(), "run-1", write_manifest=True, manifest_base_dir=tmp_path)
    assert blocked["status"] == "not_ready"
    assert writes == []

    readiness["status"] = "ready"
    readiness["blocking_reasons"] = []
    ready = orchestrator.finalize_run(object(), "run-1", write_manifest=True, manifest_base_dir=tmp_path)
    assert ready["status"] == "ok"
    assert writes[0]["base_dir"] == tmp_path
