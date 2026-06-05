from __future__ import annotations

from pathlib import Path

import pytest

from geond import orchestrator_control

SUGGESTED_GRAPH_APPLY = "geond-orchestrator agent run-1 --execute --allow-task-graph-create"


@pytest.fixture(autouse=True)
def disable_task_graph_proposal(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        orchestrator_control.orchestrator_task_planner,
        "propose_task_graph",
        lambda *args, **kwargs: {
            "schema": "geond.task_graph_proposal.v1",
            "status": "ok",
            "code": None,
            "eligible_for_materialization": False,
            "eligibility_reason": "not needed",
            "tasks": [],
        },
    )


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


def test_plan_mode_can_prioritize_task_graph_materialization(
    monkeypatch,
    tmp_path: Path,
) -> None:
    proposal = {
        "schema": "geond.task_graph_proposal.v1",
        "status": "ok",
        "code": None,
        "run_id": "run-1",
        "proposal_id": "proposal-1",
        "template": "implementation",
        "eligible_for_materialization": True,
        "eligibility_reason": "placeholder only",
        "planning_placeholder_task": {"task_id": "task-1"},
        "tasks": [{"key": "design", "title": "Design", "depends_on": []}],
        "suggested_apply_command": SUGGESTED_GRAPH_APPLY,
    }
    monkeypatch.setattr(
        orchestrator_control.orchestrator_planner,
        "create_plan",
        lambda *args, **kwargs: plan_payload(action("dispatch_spawn", priority=55)),
    )
    monkeypatch.setattr(
        orchestrator_control.orchestrator_task_planner,
        "propose_task_graph",
        lambda *args, **kwargs: proposal,
    )

    payload = orchestrator_control.run_plan_mode(
        object(),
        workspace_id_or_uri="file:///repo",
        run_id="run-1",
        base_dir=tmp_path,
        propose_task_graph=True,
    )

    assert payload["next_action"] == "materialize_task_graph"
    assert payload["proposal_id"] == "proposal-1"
    assert payload["plan"]["recommended_actions"][0]["action_type"] == "materialize_task_graph"


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


def test_agent_preview_surfaces_task_graph_without_writes(monkeypatch, tmp_path: Path) -> None:
    proposal = {
        "schema": "geond.task_graph_proposal.v1",
        "status": "ok",
        "code": None,
        "run_id": "run-1",
        "proposal_id": "proposal-1",
        "template": "implementation",
        "eligible_for_materialization": True,
        "eligibility_reason": "placeholder only",
        "planning_placeholder_task": {"task_id": "task-1"},
        "tasks": [{"key": "design", "title": "Design", "depends_on": []}],
        "suggested_apply_command": SUGGESTED_GRAPH_APPLY,
    }

    monkeypatch.setattr(
        orchestrator_control.orchestrator_planner,
        "doctor_run",
        lambda *args, **kwargs: plan_payload(action("dispatch_spawn", priority=55)),
    )
    monkeypatch.setattr(
        orchestrator_control.orchestrator_task_planner,
        "propose_task_graph",
        lambda *args, **kwargs: proposal,
    )
    monkeypatch.setattr(
        orchestrator_control.orchestrator_task_planner,
        "apply_task_graph_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("preview must not apply")),
    )

    payload = orchestrator_control.run_agent_mode(object(), "run-1", base_dir=tmp_path)

    assert payload["next_action"] == "materialize_task_graph"
    assert payload["proposal_id"] == "proposal-1"
    assert payload["execution_status"] == "preview"


def test_agent_execute_requires_task_graph_approval(monkeypatch, tmp_path: Path) -> None:
    proposal = {
        "schema": "geond.task_graph_proposal.v1",
        "status": "ok",
        "code": None,
        "run_id": "run-1",
        "proposal_id": "proposal-1",
        "template": "implementation",
        "eligible_for_materialization": True,
        "eligibility_reason": "placeholder only",
        "planning_placeholder_task": {"task_id": "task-1"},
        "tasks": [{"key": "design", "title": "Design", "depends_on": []}],
        "suggested_apply_command": SUGGESTED_GRAPH_APPLY,
    }
    monkeypatch.setattr(
        orchestrator_control.orchestrator_planner,
        "doctor_run",
        lambda *args, **kwargs: plan_payload(action("dispatch_spawn", priority=55)),
    )
    monkeypatch.setattr(
        orchestrator_control.orchestrator_task_planner,
        "propose_task_graph",
        lambda *args, **kwargs: proposal,
    )

    payload = orchestrator_control.run_agent_mode(
        object(),
        "run-1",
        execute=True,
        base_dir=tmp_path,
    )

    assert payload["status"] == "blocked"
    assert payload["code"] == "TASK_GRAPH_APPROVAL_REQUIRED"
    assert payload["steps"][0]["step_status"] == "manual_required"


def test_agent_execute_requires_llm_planner_approval(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        orchestrator_control.orchestrator_planner,
        "doctor_run",
        lambda *args, **kwargs: plan_payload(action("dispatch_spawn", priority=55)),
    )

    payload = orchestrator_control.run_agent_mode(
        object(),
        "run-1",
        execute=True,
        planner="llm",
        execute_planner=True,
        base_dir=tmp_path,
    )

    assert payload["status"] == "blocked"
    assert payload["code"] == "LLM_PLANNER_APPROVAL_REQUIRED"
    assert payload["steps"][0]["step_status"] == "manual_required"


def test_agent_execute_applies_allowed_task_graph(monkeypatch, tmp_path: Path) -> None:
    proposal = {
        "schema": "geond.task_graph_proposal.v1",
        "status": "ok",
        "code": None,
        "run_id": "run-1",
        "proposal_id": "proposal-1",
        "template": "implementation",
        "eligible_for_materialization": True,
        "eligibility_reason": "placeholder only",
        "planning_placeholder_task": {"task_id": "task-1"},
        "tasks": [{"key": "design", "title": "Design", "depends_on": []}],
        "suggested_apply_command": SUGGESTED_GRAPH_APPLY,
    }
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        orchestrator_control.orchestrator_planner,
        "doctor_run",
        lambda *args, **kwargs: plan_payload(action("dispatch_spawn", priority=55)),
    )
    monkeypatch.setattr(
        orchestrator_control.orchestrator_task_planner,
        "propose_task_graph",
        lambda *args, **kwargs: proposal,
    )

    def fake_apply(conn, run_id, graph_payload, **kwargs):  # noqa: ANN001, ANN202
        calls.append({"run_id": run_id, "graph_payload": graph_payload, **kwargs})
        return {
            "schema": "geond.task_graph_materialization.v1",
            "status": "ok",
            "code": None,
        }

    monkeypatch.setattr(
        orchestrator_control.orchestrator_task_planner,
        "apply_task_graph_payload",
        fake_apply,
    )

    payload = orchestrator_control.run_agent_mode(
        object(),
        "run-1",
        execute=True,
        allow_task_graph_create=True,
        base_dir=tmp_path,
    )

    assert payload["execution_status"] == "completed"
    assert calls == [{"run_id": "run-1", "graph_payload": proposal, "execute": True}]


def test_agent_execute_materializes_allowed_llm_planner_proposal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    proposal = {
        "schema": "geond.task_graph_proposal.v1",
        "status": "ok",
        "code": None,
        "run_id": "run-1",
        "proposal_id": "llm-proposal-1",
        "planner": "llm",
        "planner_agent": "claude",
        "eligible_for_materialization": True,
        "eligibility_reason": "placeholder only",
        "planning_placeholder_task": {"task_id": "task-1"},
        "tasks": [{"key": "inspect", "title": "Inspect", "depends_on": []}],
        "suggested_apply_command": SUGGESTED_GRAPH_APPLY,
    }
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        orchestrator_control.orchestrator_planner,
        "doctor_run",
        lambda *args, **kwargs: plan_payload(action("dispatch_spawn", priority=55)),
    )

    def fake_propose(conn, run_id, **kwargs):  # noqa: ANN001, ANN202
        calls.append({"phase": "propose", "run_id": run_id, **kwargs})
        return {
            "schema": "geond.llm_task_graph_planner.v1",
            "status": "ok",
            "code": None,
            "task_graph_proposal": proposal,
        }

    def fake_apply(conn, run_id, graph_payload, **kwargs):  # noqa: ANN001, ANN202
        calls.append({"phase": "apply", "run_id": run_id, "graph_payload": graph_payload, **kwargs})
        return {
            "schema": "geond.task_graph_materialization.v1",
            "status": "ok",
            "code": None,
        }

    monkeypatch.setattr(
        orchestrator_control.orchestrator_task_planner,
        "propose_task_graph",
        fake_propose,
    )
    monkeypatch.setattr(
        orchestrator_control.orchestrator_graph_review,
        "review_task_graph_proposal",
        lambda *args, **kwargs: {
            "schema": "geond.task_graph_review.v1",
            "status": "ok",
            "decision": "approved",
            "review_id": "review-1",
        },
    )
    monkeypatch.setattr(
        orchestrator_control.orchestrator_task_planner,
        "apply_task_graph_payload",
        fake_apply,
    )

    payload = orchestrator_control.run_agent_mode(
        object(),
        "run-1",
        execute=True,
        planner="llm",
        planner_agent="claude",
        allow_llm_planner=True,
        execute_planner=True,
        allow_task_graph_create=True,
        base_dir=tmp_path,
    )

    assert payload["execution_status"] == "completed"
    assert calls[0]["planner"] == "llm"
    assert calls[0]["agent_name"] == "claude"
    assert calls[0]["execute_planner"] is True
    assert calls[1] == {
        "phase": "apply",
        "run_id": "run-1",
        "graph_payload": proposal,
        "execute": True,
    }


def test_agent_execute_blocks_llm_materialization_when_review_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    proposal = {
        "schema": "geond.task_graph_proposal.v1",
        "status": "ok",
        "code": None,
        "run_id": "run-1",
        "proposal_id": "llm-proposal-1",
        "planner": "llm",
        "planner_agent": "codex",
        "eligible_for_materialization": True,
        "eligibility_reason": "placeholder only",
        "planning_placeholder_task": {"task_id": "task-1"},
        "tasks": [{"key": "inspect", "title": "Inspect", "depends_on": []}],
        "suggested_apply_command": SUGGESTED_GRAPH_APPLY,
    }
    monkeypatch.setattr(
        orchestrator_control.orchestrator_planner,
        "doctor_run",
        lambda *args, **kwargs: plan_payload(action("dispatch_spawn", priority=55)),
    )
    monkeypatch.setattr(
        orchestrator_control.orchestrator_task_planner,
        "propose_task_graph",
        lambda *args, **kwargs: {
            "schema": "geond.llm_task_graph_planner.v1",
            "status": "ok",
            "task_graph_proposal": proposal,
        },
    )
    monkeypatch.setattr(
        orchestrator_control.orchestrator_graph_review,
        "review_task_graph_proposal",
        lambda *args, **kwargs: {
            "schema": "geond.task_graph_review.v1",
            "status": "ok",
            "decision": "blocked",
            "review_id": "review-1",
        },
    )
    monkeypatch.setattr(
        orchestrator_control.orchestrator_task_planner,
        "apply_task_graph_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not apply")),
    )

    payload = orchestrator_control.run_agent_mode(
        object(),
        "run-1",
        execute=True,
        planner="llm",
        allow_llm_planner=True,
        execute_planner=True,
        allow_task_graph_create=True,
        base_dir=tmp_path,
    )

    assert payload["status"] == "blocked"
    assert payload["code"] == "TASK_GRAPH_REVIEW_BLOCKED"
    assert payload["steps"][0]["step_status"] == "blocked"


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
