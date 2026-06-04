from __future__ import annotations

from pathlib import Path

from geond import orchestrator_planner


def status_payload(
    *,
    run_id: str = "run-1",
    readiness: str = "not_ready",
    risk_level: str = "medium",
    claimable: bool = True,
) -> dict[str, object]:
    return {
        "schema": "geond.orchestrator_status.v1",
        "status": "ok",
        "run": {
            "run_id": run_id,
            "workspace_id": "workspace-1",
            "title": "Fix checkout flow",
            "risk_level": risk_level,
            "status": "active",
        },
        "readiness": {"status": readiness, "blocking_reasons": []},
        "claimable_tasks": [{"task_id": "task-1", "title": "Implement task", "status": "ready"}]
        if claimable
        else [],
        "active_leases": [],
        "open_findings": [],
        "pending_approvals": [],
        "latest_decisions": [],
        "degraded_ledger": {"pending_count": 0, "pending_events": []},
    }


def test_planner_prioritizes_degraded_ledger(monkeypatch, tmp_path: Path) -> None:
    payload = status_payload()
    payload["degraded_ledger"] = {"pending_count": 1, "pending_events": [{"event_id": "e1"}]}
    monkeypatch.setattr(
        orchestrator_planner.orchestrator,
        "get_status",
        lambda *args, **kwargs: payload,
    )

    plan = orchestrator_planner.doctor_run(object(), "run-1", base_dir=tmp_path)

    assert plan["schema"] == "geond.orchestrator_plan.v1"
    assert plan["recommended_actions"][0]["action_type"] == "ledger_reconcile"
    assert plan["recommended_actions"][0]["blocks_execution"] is True
    assert plan["runnable_dispatch_commands"] == []
    assert plan["recovery_commands"][0] == "geond ledger reconcile run-1"


def test_planner_prioritizes_high_risk_approval_before_dispatch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    payload = status_payload(risk_level="high")
    payload["pending_approvals"] = [
        {"approval_id": "approval-1", "risk_level": "high", "reason": "Release gate"}
    ]
    monkeypatch.setattr(
        orchestrator_planner.orchestrator,
        "get_status",
        lambda *args, **kwargs: payload,
    )

    plan = orchestrator_planner.doctor_run(object(), "run-1", base_dir=tmp_path)

    assert plan["recommended_actions"][0]["action_type"] == "resolve_approval"
    assert (
        "geond approval resolve approval-1"
        in plan["recommended_actions"][0]["suggested_cli_command"]
    )
    assert all(
        action["action_type"] not in {"dispatch_claim", "dispatch_spawn"}
        for action in plan["recommended_actions"]
    )


def test_planner_prioritizes_p1_finding_before_finalize(monkeypatch, tmp_path: Path) -> None:
    payload = status_payload(readiness="ready", claimable=False)
    payload["open_findings"] = [
        {"finding_id": "finding-1", "severity": "P1", "summary": "Regression risk"}
    ]
    monkeypatch.setattr(
        orchestrator_planner.orchestrator,
        "get_status",
        lambda *args, **kwargs: payload,
    )

    plan = orchestrator_planner.doctor_run(object(), "run-1", base_dir=tmp_path)

    assert plan["recommended_actions"][0]["action_type"] == "resolve_finding"
    assert (
        "geond review resolve finding-1" in plan["recommended_actions"][0]["suggested_cli_command"]
    )
    assert not any(
        action["action_type"] == "finalize_ready_run" for action in plan["recommended_actions"]
    )


def test_planner_recommends_dispatch_and_finalize(monkeypatch, tmp_path: Path) -> None:
    claimable_payload = status_payload(claimable=True)
    ready_payload = status_payload(readiness="ready", claimable=False)
    monkeypatch.setattr(
        orchestrator_planner.orchestrator,
        "get_status",
        lambda conn, run_id, **kwargs: claimable_payload if run_id == "run-1" else ready_payload,
    )

    dispatch_plan = orchestrator_planner.doctor_run(
        object(),
        "run-1",
        agents=["codex", "claude"],
        base_dir=tmp_path,
    )
    finalize_plan = orchestrator_planner.doctor_run(object(), "run-2", base_dir=tmp_path)

    assert [action["action_type"] for action in dispatch_plan["recommended_actions"]] == [
        "dispatch_claim",
        "dispatch_spawn",
    ]
    assert (
        "--agents codex,claude" in dispatch_plan["recommended_actions"][1]["suggested_cli_command"]
    )
    assert finalize_plan["recommended_actions"][0]["action_type"] == "finalize_ready_run"
    assert (
        "--git-checkpoint --dry-run"
        in finalize_plan["recommended_actions"][0]["suggested_cli_command"]
    )


def test_planner_recommends_task_creation_and_writes_bundle(monkeypatch, tmp_path: Path) -> None:
    payload = status_payload(claimable=False)
    monkeypatch.setattr(
        orchestrator_planner.orchestrator,
        "get_status",
        lambda *args, **kwargs: payload,
    )

    plan = orchestrator_planner.create_plan(
        object(),
        workspace_id_or_uri="workspace-1",
        run_id="run-1",
        base_dir=tmp_path,
        write_bundle=True,
    )

    assert plan["recommended_actions"][0]["action_type"] == "create_task_needed"
    assert Path(plan["bundle"]["json_path"]).exists()
    assert Path(plan["bundle"]["markdown_path"]).exists()
    replay = orchestrator_planner.create_plan(
        object(),
        workspace_id_or_uri="workspace-1",
        run_id="run-1",
        base_dir=tmp_path,
        write_bundle=True,
    )
    assert replay["plan_id"] == plan["plan_id"]


def test_workspace_plan_filters_active_runs(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        orchestrator_planner.orchestration_store,
        "list_runs",
        lambda conn, workspace, limit=50: {
            "status": "ok",
            "runs": [
                {"run_id": "run-active", "status": "active"},
                {"run_id": "run-done", "status": "done"},
            ],
        },
    )

    def fake_status(conn, run_id, **kwargs):  # noqa: ANN001, ANN202
        calls.append(run_id)
        return status_payload(run_id=run_id)

    monkeypatch.setattr(orchestrator_planner.orchestrator, "get_status", fake_status)

    plan = orchestrator_planner.create_plan(
        object(),
        workspace_id_or_uri="workspace-1",
        base_dir=tmp_path,
    )

    assert calls == ["run-active"]
    assert plan["summary"]["run_count"] == 1
    assert plan["active_runs"][0]["run_id"] == "run-active"


def test_planner_surfaces_stale_lease_recovery(monkeypatch, tmp_path: Path) -> None:
    payload = status_payload(claimable=False)
    payload["active_leases"] = [
        {
            "lease_id": "lease-1",
            "task_id": "task-1",
            "worker_session_id": "worker-1",
            "status": "active",
            "last_heartbeat_at": None,
            "expires_at": None,
        }
    ]
    monkeypatch.setattr(
        orchestrator_planner.orchestrator,
        "get_status",
        lambda *args, **kwargs: payload,
    )

    plan = orchestrator_planner.doctor_run(object(), "run-1", base_dir=tmp_path)

    assert plan["blockers"][0]["lease_id"] == "lease-1"
    assert plan["recovery_commands"][0].startswith("geond worker release lease-1")
