from __future__ import annotations

from pathlib import Path

from geond import orchestrator_action_queue


def action_payload(
    action_id: str = "action-1",
    *,
    action_type: str = "dispatch_spawn",
    reason: str = "Task is claimable.",
) -> dict[str, object]:
    return {
        "action_id": action_id,
        "label": "Dispatch Spawn",
        "action_type": action_type,
        "severity": "info",
        "status": "ready",
        "reason": reason,
        "blocks_execution": False,
        "suggested_cli_command": "touch should-not-run",
        "related_ids": {"run_id": "run-1", "task_id": "task-1"},
        "run_id": "run-1",
        "task_id": "task-1",
        "artifact_refs": ["tmp/geond-runs/run-1/control/TRACE.jsonl"],
    }


def bundle_payload(action: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "geond.orchestrator_action_bundle.v1",
        "status": "ok",
        "code": None,
        "bundle_id": "bundle-1",
        "actions": [action],
    }


def test_queue_actions_append_dedupe_and_mark_stale(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    current_action = action_payload()

    monkeypatch.setattr(
        orchestrator_action_queue.orchestrator_action_bundle,
        "build_action_bundle",
        lambda *args, **kwargs: bundle_payload(current_action),
    )

    first = orchestrator_action_queue.queue_actions_from_bundle(
        object(),
        workspace_id_or_uri="file:///repo",
        run_id="run-1",
        base_dir=tmp_path,
    )
    second = orchestrator_action_queue.queue_actions_from_bundle(
        object(),
        workspace_id_or_uri="file:///repo",
        run_id="run-1",
        base_dir=tmp_path,
    )

    assert first["schema"] == "geond.orchestrator_action_queue.v1"
    assert first["queued_count"] == 1
    assert second["queued_count"] == 0
    assert len(orchestrator_action_queue.read_queue_events("run-1", tmp_path)) == 1

    current_action = action_payload(reason="Task command changed.")
    listed = orchestrator_action_queue.list_action_queue(
        object(),
        workspace_id_or_uri="file:///repo",
        run_id="run-1",
        base_dir=tmp_path,
    )

    assert listed["items"][0]["status"] == "stale"


def test_queue_approve_reject_and_replay_status(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        orchestrator_action_queue.orchestrator_action_bundle,
        "build_action_bundle",
        lambda *args, **kwargs: bundle_payload(action_payload()),
    )
    orchestrator_action_queue.queue_actions_from_bundle(
        object(),
        workspace_id_or_uri="file:///repo",
        run_id="run-1",
        base_dir=tmp_path,
    )

    approved = orchestrator_action_queue.approve_action(
        run_id="run-1",
        action_id="action-1",
        approved_by="human",
        reason="looks safe",
        base_dir=tmp_path,
    )
    rejected = orchestrator_action_queue.reject_action(
        run_id="run-1",
        action_id="action-1",
        rejected_by="human",
        reason="changed mind",
        base_dir=tmp_path,
    )

    assert approved["queue_item"]["status"] == "approved"
    assert rejected["queue_item"]["status"] == "rejected"
    assert (
        orchestrator_action_queue.get_queue_item(
            "run-1",
            "action-1",
            base_dir=tmp_path,
        )["status"]
        == "rejected"
    )


def test_execute_requires_approval_and_uses_typed_dispatcher(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    called: dict[str, object] = {}
    monkeypatch.setattr(
        orchestrator_action_queue.orchestrator_action_bundle,
        "build_action_bundle",
        lambda *args, **kwargs: bundle_payload(action_payload()),
    )

    def fake_execute_action(conn, action, **kwargs):  # noqa: ANN001, ANN202
        called["action"] = action
        called["kwargs"] = kwargs
        return {"schema": "fake.result.v1", "status": "ok", "code": None}

    monkeypatch.setattr(
        orchestrator_action_queue.orchestrator_control,
        "execute_action",
        fake_execute_action,
    )
    orchestrator_action_queue.queue_actions_from_bundle(
        object(),
        workspace_id_or_uri="file:///repo",
        run_id="run-1",
        base_dir=tmp_path,
    )

    preview = orchestrator_action_queue.execute_queued_action(
        object(),
        run_id="run-1",
        action_id="action-1",
        base_dir=tmp_path,
    )
    blocked = orchestrator_action_queue.execute_queued_action(
        object(),
        run_id="run-1",
        action_id="action-1",
        execute=True,
        base_dir=tmp_path,
    )
    assert preview["execution_status"] == "preview"
    assert "action" not in called
    assert blocked["code"] == "ACTION_APPROVAL_REQUIRED"

    orchestrator_action_queue.approve_action(
        run_id="run-1",
        action_id="action-1",
        approved_by="human",
        base_dir=tmp_path,
    )
    executed = orchestrator_action_queue.execute_queued_action(
        object(),
        run_id="run-1",
        action_id="action-1",
        execute=True,
        agents=["codex", "claude"],
        max_workers=2,
        base_dir=tmp_path,
    )

    assert executed["execution_status"] == "executed"
    assert called["action"]["suggested_cli_command"] == "touch should-not-run"
    assert called["kwargs"]["agents"] == ["codex", "claude"]
    assert called["kwargs"]["max_workers"] == 2


def test_execute_blocks_manual_and_unapproved_graph_review(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    manual_action = action_payload(action_type="resolve_finding")
    graph_action = {
        **action_payload("graph-1", action_type="materialize_task_graph"),
        "task_graph_proposal": {"schema": "geond.task_graph_proposal.v1", "planner": "llm"},
        "task_graph_review": {"schema": "geond.task_graph_review.v1", "decision": "blocked"},
    }
    current_actions = [manual_action, graph_action]
    monkeypatch.setattr(
        orchestrator_action_queue.orchestrator_action_bundle,
        "build_action_bundle",
        lambda *args, **kwargs: {
            "schema": "geond.orchestrator_action_bundle.v1",
            "status": "ok",
            "code": None,
            "bundle_id": "bundle-1",
            "actions": current_actions,
        },
    )
    orchestrator_action_queue.queue_actions_from_bundle(
        object(),
        workspace_id_or_uri="file:///repo",
        run_id="run-1",
        base_dir=tmp_path,
    )
    for action_id in ("action-1", "graph-1"):
        orchestrator_action_queue.approve_action(
            run_id="run-1",
            action_id=action_id,
            approved_by="human",
            base_dir=tmp_path,
        )

    manual = orchestrator_action_queue.execute_queued_action(
        object(),
        run_id="run-1",
        action_id="action-1",
        execute=True,
        base_dir=tmp_path,
    )
    graph = orchestrator_action_queue.execute_queued_action(
        object(),
        run_id="run-1",
        action_id="graph-1",
        execute=True,
        base_dir=tmp_path,
    )

    assert manual["code"] == "HUMAN_ACTION_REQUIRED"
    assert graph["code"] == "TASK_GRAPH_REVIEW_BLOCKED"
