from __future__ import annotations

from pathlib import Path

from geond import orchestrator_scheduler


def queue_item(
    action_id: str,
    *,
    action_type: str = "dispatch_spawn",
    status: str = "approved",
    run_id: str = "run-1",
) -> dict[str, object]:
    return {
        "schema": "geond.orchestrator_action_queue.v1",
        "run_id": run_id,
        "action_id": action_id,
        "status": status,
        "action_type": action_type,
        "label": action_type.replace("_", " ").title(),
        "reason": f"{action_type} reason",
        "suggested_cli_command": "touch should-not-run",
        "related_ids": {"run_id": run_id},
        "artifact_refs": ["tmp/geond-runs/run-1/control/TRACE.jsonl"],
        "approved_by": "human",
        "approved_at": "2026-06-06T00:00:00+00:00",
        "queued_at": "2026-06-06T00:00:00+00:00",
    }


def queue_payload(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "geond.orchestrator_action_queue.v1",
        "status": "ok",
        "code": None,
        "items": items,
        "item_count": len(items),
        "total_item_count": len(items),
        "queued_count": sum(1 for item in items if item.get("status") == "queued"),
        "approved_count": sum(1 for item in items if item.get("status") == "approved"),
        "blocked_count": sum(1 for item in items if item.get("status") == "blocked"),
        "stale_count": sum(1 for item in items if item.get("status") == "stale"),
    }


def test_scheduler_selects_only_approved_auto_actions(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    items = [
        queue_item("auto-1", action_type="dispatch_spawn"),
        queue_item("manual-1", action_type="resolve_finding"),
        queue_item("stale-1", action_type="finalize_ready_run", status="stale"),
        queue_item("done-1", action_type="ledger_reconcile", status="executed"),
    ]
    monkeypatch.setattr(
        orchestrator_scheduler.orchestrator_action_queue,
        "list_action_queue",
        lambda *args, **kwargs: queue_payload(items),
    )

    payload = orchestrator_scheduler.plan_scheduler(
        object(),
        workspace_id_or_uri="file:///repo",
        agents=["codex", "claude"],
        max_actions=5,
        base_dir=tmp_path,
    )

    assert payload["schema"] == "geond.orchestrator_scheduler.v1"
    assert [item["action_id"] for item in payload["selected_actions"]] == ["auto-1"]
    assert {item["action_id"]: item["skip_reason"] for item in payload["skipped_actions"]} == {
        "done-1": "status:executed",
        "manual-1": "manual:resolve_finding",
        "stale-1": "status:stale",
    }


def test_scheduler_preview_and_budget_do_not_execute(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    called: list[str] = []
    items = [
        queue_item("auto-1", action_type="dispatch_spawn"),
        queue_item("auto-2", action_type="finalize_ready_run"),
    ]
    monkeypatch.setattr(
        orchestrator_scheduler.orchestrator_action_queue,
        "list_action_queue",
        lambda *args, **kwargs: queue_payload(items),
    )
    monkeypatch.setattr(
        orchestrator_scheduler.orchestrator_action_queue,
        "execute_queued_action",
        lambda *args, **kwargs: called.append("execute"),
    )

    preview = orchestrator_scheduler.drain_scheduler(
        object(),
        workspace_id_or_uri="file:///repo",
        max_actions=2,
        base_dir=tmp_path,
    )
    blocked = orchestrator_scheduler.drain_scheduler(
        object(),
        workspace_id_or_uri="file:///repo",
        execute=True,
        max_actions=2,
        budget_actions=1,
        base_dir=tmp_path,
    )

    assert preview["execution_status"] == "preview"
    assert blocked["code"] == "SCHEDULER_BUDGET_EXCEEDED"
    assert called == []


def test_scheduler_execute_stops_after_failed_action_and_writes_trace(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    items = [
        queue_item("auto-1", action_type="dispatch_spawn"),
        queue_item("auto-2", action_type="finalize_ready_run"),
    ]
    calls: list[str] = []
    monkeypatch.setattr(
        orchestrator_scheduler.orchestrator_action_queue,
        "list_action_queue",
        lambda *args, **kwargs: queue_payload(items),
    )

    def fake_execute(conn, **kwargs):  # noqa: ANN001, ANN202
        calls.append(kwargs["action_id"])
        return {
            "schema": "geond.orchestrator_action_execution.v1",
            "status": "blocked",
            "code": "FAKE_FAILED",
            "execution_status": "blocked",
            "run_id": kwargs["run_id"],
            "action_id": kwargs["action_id"],
        }

    monkeypatch.setattr(
        orchestrator_scheduler.orchestrator_action_queue,
        "execute_queued_action",
        fake_execute,
    )

    payload = orchestrator_scheduler.drain_scheduler(
        object(),
        workspace_id_or_uri="file:///repo",
        execute=True,
        max_actions=2,
        base_dir=tmp_path,
    )

    assert calls == ["auto-1"]
    assert payload["status"] == "blocked"
    assert payload["stop_reason"] == "FAKE_FAILED"
    assert payload["trace_steps"][0]["result_code"] == "FAKE_FAILED"
    assert Path(payload["bundle"]["trace_path"]).exists()


def test_scheduler_execute_stops_when_queue_refresh_finds_stale_item(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    initial_items = [queue_item("auto-1", action_type="finalize_ready_run")]
    stale_items = [queue_item("stale-1", action_type="dispatch_spawn", status="stale")]
    list_calls = 0

    def fake_list(*args, **kwargs):  # noqa: ANN001, ANN202
        nonlocal list_calls
        list_calls += 1
        return queue_payload(initial_items if list_calls == 1 else stale_items)

    monkeypatch.setattr(
        orchestrator_scheduler.orchestrator_action_queue,
        "list_action_queue",
        fake_list,
    )
    monkeypatch.setattr(
        orchestrator_scheduler.orchestrator_action_queue,
        "execute_queued_action",
        lambda *args, **kwargs: {
            "schema": "geond.orchestrator_action_execution.v1",
            "status": "ok",
            "code": None,
            "execution_status": "executed",
            "run_id": kwargs["run_id"],
            "action_id": kwargs["action_id"],
        },
    )

    payload = orchestrator_scheduler.drain_scheduler(
        object(),
        workspace_id_or_uri="file:///repo",
        execute=True,
        max_actions=2,
        base_dir=tmp_path,
    )

    assert payload["status"] == "blocked"
    assert payload["stop_reason"] == "QUEUE_STATUS_BLOCKED"
    assert payload["trace_steps"][-1]["result_code"] == "QUEUE_STATUS_BLOCKED"
