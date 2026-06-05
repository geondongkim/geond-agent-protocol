from __future__ import annotations

from pathlib import Path

from geond import orchestrator_control


def action(action_type: str, *, priority: int = 50, blocks: bool = False) -> dict[str, object]:
    return {
        "action_type": action_type,
        "priority": priority,
        "severity": "info",
        "reason": f"{action_type} reason",
        "suggested_cli_command": f"run {action_type}",
        "related_ids": {"run_id": "run-1"},
        "run_id": "run-1",
        "task_id": "task-1",
        "blocks_execution": blocks,
    }


def plan_payload(*actions: dict[str, object], readiness: str = "not_ready") -> dict[str, object]:
    return {
        "schema": "geond.orchestrator_plan.v1",
        "status": "ok",
        "code": None,
        "plan_id": "plan-1",
        "agents": ["codex", "claude"],
        "active_runs": [
            {
                "run_id": "run-1",
                "title": "Fix checkout",
                "status": "active",
                "risk_level": "medium",
                "readiness_status": readiness,
            }
        ],
        "run_plans": [],
        "blockers": [],
        "recommended_actions": list(actions),
        "runnable_dispatch_commands": [],
        "recovery_commands": [],
        "evidence_refs": [],
        "summary": {
            "run_count": 1,
            "blocking_action_count": sum(1 for item in actions if item.get("blocks_execution")),
            "dispatch_action_count": sum(
                1 for item in actions if item.get("action_type") == "dispatch_spawn"
            ),
            "recovery_command_count": 0,
        },
    }


def test_plan_mode_wraps_existing_planner_without_mutating(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_plan(conn, **kwargs):  # noqa: ANN001, ANN202
        calls.append("plan")
        assert kwargs["write_bundle"] is False
        return plan_payload(action("dispatch_spawn"))

    monkeypatch.setattr(orchestrator_control.orchestrator_planner, "create_plan", fake_plan)
    monkeypatch.setattr(
        orchestrator_control.orchestrator,
        "dispatch_spawn",
        lambda *args, **kwargs: calls.append("spawn"),
    )

    payload = orchestrator_control.run_plan_mode(
        object(),
        workspace_id_or_uri="file:///repo",
        run_id="run-1",
        agents=["codex"],
        base_dir=tmp_path,
        write_bundle=True,
    )

    assert payload["schema"] == "geond.orchestrator_control.v1"
    assert payload["mode"] == "plan"
    assert payload["plan"]["schema"] == "geond.orchestrator_plan.v1"
    assert payload["next_action"] == "dispatch_spawn"
    assert Path(payload["bundle"]["control_plan_path"]).exists()
    assert calls == ["plan"]


def test_agent_preview_selects_spawn_without_executing(monkeypatch, tmp_path: Path) -> None:
    def fail_write(*args, **kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("preview must not execute")

    monkeypatch.setattr(
        orchestrator_control.orchestrator_planner,
        "doctor_run",
        lambda *args, **kwargs: plan_payload(action("dispatch_claim"), action("dispatch_spawn")),
    )
    monkeypatch.setattr(orchestrator_control.orchestrator, "dispatch_spawn", fail_write)
    monkeypatch.setattr(orchestrator_control.degraded_ledger, "reconcile", fail_write)

    payload = orchestrator_control.run_agent_mode(
        object(),
        "run-1",
        agents=["codex", "claude"],
        max_workers=2,
        model="gpt-5",
        timeout_seconds=12,
        base_dir=tmp_path,
    )

    assert payload["mode"] == "agent"
    assert payload["execution_status"] == "preview"
    assert payload["selected_action"]["action_type"] == "dispatch_spawn"
    assert "--agents codex,claude" in payload["delegated_command"]
    assert "--execute" not in payload["delegated_command"]


def test_agent_execute_reconciles_degraded_ledger_first(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        orchestrator_control.orchestrator_planner,
        "doctor_run",
        lambda *args, **kwargs: plan_payload(action("ledger_reconcile", priority=10, blocks=True)),
    )

    def fake_reconcile(conn, **kwargs):  # noqa: ANN001, ANN202
        calls.append(("reconcile", kwargs))
        return {"schema": "geond.degraded_ledger_reconcile.v1", "status": "ok", "code": None}

    monkeypatch.setattr(orchestrator_control.degraded_ledger, "reconcile", fake_reconcile)

    payload = orchestrator_control.run_agent_mode(
        object(),
        "run-1",
        execute=True,
        base_dir=tmp_path,
    )

    assert payload["execution_status"] == "completed"
    assert calls == [("reconcile", {"run_id": "run-1", "base_dir": tmp_path, "dry_run": False})]
    assert Path(payload["bundle"]["trace_path"]).exists()


def test_agent_execute_stops_for_human_blocker(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        orchestrator_control.orchestrator_planner,
        "doctor_run",
        lambda *args, **kwargs: plan_payload(action("resolve_finding", priority=25, blocks=True)),
    )

    payload = orchestrator_control.run_agent_mode(
        object(),
        "run-1",
        execute=True,
        base_dir=tmp_path,
    )

    assert payload["status"] == "blocked"
    assert payload["code"] == "HUMAN_ACTION_REQUIRED"
    assert payload["steps"][0]["step_status"] == "manual_required"


def test_agent_execute_dispatches_spawn_with_agent_options(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        orchestrator_control.orchestrator_planner,
        "doctor_run",
        lambda *args, **kwargs: plan_payload(action("dispatch_spawn")),
    )

    def fake_spawn(conn, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return {
            "schema": "geond.orchestrator_dispatch.v1",
            "status": "ok",
            "code": None,
            "overall_execution_status": "completed",
        }

    monkeypatch.setattr(orchestrator_control.orchestrator, "dispatch_spawn", fake_spawn)

    payload = orchestrator_control.run_agent_mode(
        object(),
        "run-1",
        execute=True,
        agents=["codex", "claude"],
        max_workers=2,
        model="gpt-5",
        sandbox="workspace-write",
        timeout_seconds=12,
        write_bundle=True,
        base_dir=tmp_path,
    )

    assert payload["execution_status"] == "completed"
    assert captured == {
        "run_id": "run-1",
        "agent_name": "codex",
        "execute": True,
        "agent_names": ["codex", "claude"],
        "max_workers": 2,
        "model": "gpt-5",
        "sandbox": "workspace-write",
        "timeout_seconds": 12,
        "write_bundle": True,
        "manifest_base_dir": tmp_path,
    }


def test_agent_execute_stops_on_partial_spawn(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        orchestrator_control.orchestrator_planner,
        "doctor_run",
        lambda *args, **kwargs: plan_payload(action("dispatch_spawn")),
    )
    monkeypatch.setattr(
        orchestrator_control.orchestrator,
        "dispatch_spawn",
        lambda *args, **kwargs: {
            "schema": "geond.orchestrator_dispatch.v1",
            "status": "partial",
            "code": "PARTIAL_SPAWN_FAILURE",
            "overall_execution_status": "partial",
        },
    )

    payload = orchestrator_control.run_agent_mode(
        object(),
        "run-1",
        execute=True,
        base_dir=tmp_path,
    )

    assert payload["status"] == "partial"
    assert payload["execution_status"] == "partial"
    assert payload["code"] == "PARTIAL_SPAWN_FAILURE"


def test_agent_execute_finalizes_ready_run_dry_run_only(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        orchestrator_control.orchestrator_planner,
        "doctor_run",
        lambda *args, **kwargs: plan_payload(
            action("finalize_ready_run", priority=70),
            readiness="ready",
        ),
    )

    def fake_finalize(conn, run_id, **kwargs):  # noqa: ANN001, ANN202
        captured.update({"run_id": run_id, **kwargs})
        return {"schema": "geond.orchestrator_finalize.v1", "status": "ok", "code": None}

    monkeypatch.setattr(orchestrator_control.orchestrator, "finalize_run", fake_finalize)

    payload = orchestrator_control.run_agent_mode(
        object(),
        "run-1",
        execute=True,
        base_dir=tmp_path,
    )

    assert payload["execution_status"] == "completed"
    assert captured["write_manifest"] is True
    assert captured["git_checkpoint"] is True
    assert captured["dry_run"] is True
