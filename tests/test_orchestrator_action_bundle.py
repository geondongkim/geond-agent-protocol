from __future__ import annotations

from pathlib import Path

from geond import orchestrator_action_bundle


def plan_payload() -> dict[str, object]:
    return {
        "schema": "geond.orchestrator_plan.v1",
        "status": "ok",
        "code": None,
        "plan_id": "plan-1",
        "agents": ["codex"],
        "active_runs": [
            {
                "run_id": "run-1",
                "title": "Implement checkout",
                "readiness_status": "not_ready",
                "manifest_dir": "tmp/geond-runs/run-1",
            }
        ],
        "recommended_actions": [
            {
                "action_type": "dispatch_spawn",
                "priority": 55,
                "severity": "info",
                "reason": "Task is claimable.",
                "suggested_cli_command": "geond-orchestrator dispatch --run run-1 --mode spawn",
                "related_ids": {"run_id": "run-1", "task_id": "task-1"},
                "run_id": "run-1",
                "task_id": "task-1",
                "blocks_execution": False,
            },
            {
                "action_type": "resolve_finding",
                "priority": 30,
                "severity": "high",
                "reason": "P1 finding is open.",
                "suggested_cli_command": "geond review resolve finding-1 --status fixed",
                "related_ids": {"run_id": "run-1", "finding_id": "finding-1"},
                "run_id": "run-1",
                "task_id": None,
                "blocks_execution": True,
            },
        ],
        "summary": {},
    }


def test_action_bundle_normalizes_plan_preview_and_trace_refs(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        orchestrator_action_bundle.orchestrator_planner,
        "create_plan",
        lambda *args, **kwargs: plan_payload(),
    )
    monkeypatch.setattr(
        orchestrator_action_bundle.orchestrator_control,
        "preview_agent_step",
        lambda *args, **kwargs: {
            "schema": "geond.orchestrator_control.v1",
            "status": "ok",
            "control_id": "control-1",
            "next_action": "dispatch_spawn",
            "delegated_command": "geond-orchestrator dispatch --run run-1 --mode spawn",
            "execution_status": "preview",
        },
    )
    monkeypatch.setattr(
        orchestrator_action_bundle.dashboard_store,
        "read_run_trace_artifacts",
        lambda *args, **kwargs: {
            "latest_control_bundle": {
                "artifact_paths": {"control_plan_path": "tmp/run-1/control/CONTROL_PLAN.json"}
            },
            "latest_control_trace": {
                "artifact_paths": {"trace_path": "tmp/run-1/control/CONTROL_TRACE.jsonl"}
            },
            "latest_planner_invocation": None,
        },
    )

    payload = orchestrator_action_bundle.build_action_bundle(
        object(),
        workspace_id_or_uri="file:///repo",
        run_id="run-1",
        agents=["codex"],
        base_dir=tmp_path,
        write_bundle=True,
    )

    assert payload["schema"] == "geond.orchestrator_action_bundle.v1"
    assert payload["action_count"] == 2
    assert payload["blocking_count"] == 1
    assert payload["actions"][0]["label"] == "Dispatch Spawn"
    assert payload["actions"][0]["status"] == "ready"
    assert payload["actions"][1]["status"] == "blocked"
    assert "tmp/run-1/control/CONTROL_TRACE.jsonl" in payload["actions"][0]["artifact_refs"]
    assert payload["control_preview"]["control_id"] == "control-1"
    assert Path(payload["bundle"]["json_path"]).exists()
    assert Path(payload["bundle"]["markdown_path"]).exists()
