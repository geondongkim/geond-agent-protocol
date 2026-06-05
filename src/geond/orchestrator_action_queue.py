from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import Connection

from geond import (
    orchestrator,
    orchestrator_action_bundle,
    orchestrator_control,
    orchestrator_planner,
)

ACTION_QUEUE_SCHEMA = "geond.orchestrator_action_queue.v1"
ACTION_EVENT_SCHEMA = "geond.orchestrator_action_event.v1"
ACTION_EXECUTION_SCHEMA = "geond.orchestrator_action_execution.v1"
ACTION_QUEUE_FILENAME = "ACTION_QUEUE.jsonl"
AUTO_EXECUTABLE_ACTIONS = {
    "ledger_reconcile",
    "materialize_task_graph",
    "dispatch_spawn",
    "finalize_ready_run",
}
MANUAL_ACTIONS = {"resolve_approval", "resolve_finding", "create_task_needed", "dispatch_claim"}
TERMINAL_STATUSES = {"executed", "failed", "blocked", "rejected"}


def queue_actions_from_bundle(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    run_id: str | None = None,
    agents: list[str] | None = None,
    limit: int = 50,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    write_bundle: bool = False,
) -> dict[str, Any]:
    bundle = orchestrator_action_bundle.build_action_bundle(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        run_id=run_id,
        agents=agents,
        limit=limit,
        base_dir=base_dir,
        write_bundle=write_bundle,
    )
    if bundle.get("status") != "ok":
        return bundle

    latest_actions = latest_actions_by_id(bundle.get("actions") or [])
    queued_events = []
    skipped_actions = []
    for action in bundle.get("actions") or []:
        action_run_id = action.get("run_id")
        action_id = action.get("action_id")
        if not action_run_id or not action_id:
            skipped_actions.append({"action_id": action_id, "reason": "missing run_id"})
            continue
        existing = replay_run_queue(str(action_run_id), base_dir=base_dir).get(action_id)
        signature = action_signature(action)
        if existing and existing.get("action_signature") == signature:
            skipped_actions.append({"action_id": action_id, "reason": "already queued"})
            continue
        queued_events.append(
            append_queue_event(
                run_id=str(action_run_id),
                base_dir=base_dir,
                event_type="queued",
                action_id=str(action_id),
                action=action,
                source_command="geond-orchestrator action queue",
            )
        )

    payload = list_action_queue(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        run_id=run_id,
        agents=agents,
        limit=limit,
        base_dir=base_dir,
        action_bundle=bundle,
    )
    payload.update(
        {
            "queued_count": len(queued_events),
            "skipped_count": len(skipped_actions),
            "queued_events": compact_events(queued_events),
            "skipped_actions": skipped_actions,
            "source_bundle_id": bundle.get("bundle_id"),
            "queue_paths": queue_paths_for_actions(latest_actions.values(), base_dir),
            "markdown": format_queue_markdown(payload),
        }
    )
    return payload


def list_action_queue(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    run_id: str | None = None,
    agents: list[str] | None = None,
    limit: int = 50,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    action_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bundle = action_bundle or orchestrator_action_bundle.build_action_bundle(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        run_id=run_id,
        agents=agents,
        limit=limit,
        base_dir=base_dir,
        write_bundle=False,
    )
    if bundle.get("status") != "ok":
        return bundle

    latest_actions = latest_actions_by_id(bundle.get("actions") or [])
    run_ids = sorted(
        {
            str(value)
            for value in [run_id, *[action.get("run_id") for action in latest_actions.values()]]
            if value
        }
    )
    items: list[dict[str, Any]] = []
    for current_run_id in run_ids:
        run_items = replay_run_queue(current_run_id, base_dir=base_dir)
        mark_stale_items(run_items, latest_actions)
        items.extend(run_items.values())
    items = sorted(items, key=lambda item: (item.get("run_id") or "", item.get("action_id") or ""))

    payload = {
        "schema": ACTION_QUEUE_SCHEMA,
        "status": "ok",
        "code": None,
        "workspace_id_or_uri": workspace_id_or_uri,
        "run_id": run_id,
        "source_bundle_id": bundle.get("bundle_id"),
        "items": items[:limit],
        "item_count": min(len(items), limit),
        "total_item_count": len(items),
        "queued_count": sum(1 for item in items if item.get("status") == "queued"),
        "approved_count": sum(1 for item in items if item.get("status") == "approved"),
        "blocked_count": sum(1 for item in items if item.get("status") == "blocked"),
        "stale_count": sum(1 for item in items if item.get("status") == "stale"),
    }
    payload["markdown"] = format_queue_markdown(payload)
    return payload


def approve_action(
    *,
    run_id: str,
    action_id: str,
    approved_by: str,
    reason: str = "",
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
) -> dict[str, Any]:
    item = get_queue_item(run_id, action_id, base_dir=base_dir)
    if not item:
        return action_error("ACTION_NOT_FOUND", f"Action was not found in queue: {action_id}")
    event = append_queue_event(
        run_id=run_id,
        base_dir=base_dir,
        event_type="approved",
        action_id=action_id,
        actor=approved_by,
        reason=reason,
        source_command="geond-orchestrator action approve",
    )
    item = get_queue_item(run_id, action_id, base_dir=base_dir)
    return action_event_payload(run_id=run_id, event=event, item=item)


def reject_action(
    *,
    run_id: str,
    action_id: str,
    rejected_by: str,
    reason: str,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
) -> dict[str, Any]:
    item = get_queue_item(run_id, action_id, base_dir=base_dir)
    if not item:
        return action_error("ACTION_NOT_FOUND", f"Action was not found in queue: {action_id}")
    event = append_queue_event(
        run_id=run_id,
        base_dir=base_dir,
        event_type="rejected",
        action_id=action_id,
        actor=rejected_by,
        reason=reason,
        source_command="geond-orchestrator action reject",
    )
    item = get_queue_item(run_id, action_id, base_dir=base_dir)
    return action_event_payload(run_id=run_id, event=event, item=item)


def execute_queued_action(
    conn: Connection,
    *,
    run_id: str,
    action_id: str,
    execute: bool = False,
    agents: list[str] | None = None,
    max_workers: int = 1,
    model: str | None = None,
    sandbox: str = "workspace-write",
    timeout_seconds: int = 3600,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
) -> dict[str, Any]:
    item = get_queue_item(run_id, action_id, base_dir=base_dir)
    if not item:
        return execution_payload(
            run_id=run_id,
            action_id=action_id,
            execute=execute,
            execution_status="failed",
            status="error",
            code="ACTION_NOT_FOUND",
            message=f"Action was not found in queue: {action_id}",
        )
    action = item.get("action") or {}
    preview = execution_payload(
        run_id=run_id,
        action_id=action_id,
        execute=execute,
        action=action,
        item=item,
        execution_status="preview",
        status="ok",
        code=None,
        message="Action execution preview.",
    )
    if not execute:
        return preview
    if item.get("status") != "approved":
        return record_execution_result(
            run_id=run_id,
            action_id=action_id,
            action=action,
            base_dir=base_dir,
            result={
                "status": "blocked",
                "code": "ACTION_APPROVAL_REQUIRED",
                "message": "Queued action must be approved before execution.",
            },
        )
    action_type = str(action.get("action_type") or "")
    if action_type not in AUTO_EXECUTABLE_ACTIONS or action_type in MANUAL_ACTIONS:
        return record_execution_result(
            run_id=run_id,
            action_id=action_id,
            action=action,
            base_dir=base_dir,
            result={
                "status": "blocked",
                "code": "HUMAN_ACTION_REQUIRED",
                "message": "Action requires a human or a dedicated CLI command.",
            },
        )
    if action_type == "materialize_task_graph":
        review = action.get("task_graph_review") or {}
        if review.get("decision") != "approved":
            return record_execution_result(
                run_id=run_id,
                action_id=action_id,
                action=action,
                base_dir=base_dir,
                result={
                    "status": "blocked",
                    "code": "TASK_GRAPH_REVIEW_BLOCKED",
                    "message": "Task graph materialization requires an approved review.",
                    "task_graph_review": review,
                },
            )

    agent_pool = orchestrator_planner.normalize_agents(agents)
    result = orchestrator_control.execute_action(
        conn,
        action,
        run_id=run_id,
        agents=agent_pool,
        max_workers=max(1, max_workers),
        model=model,
        sandbox=sandbox,
        timeout_seconds=timeout_seconds,
        write_bundle=False,
        base_dir=base_dir,
        allow_task_graph_create=True,
    )
    return record_execution_result(
        run_id=run_id,
        action_id=action_id,
        action=action,
        base_dir=base_dir,
        result=result,
    )


def build_dashboard_action_queue(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    limit: int = 50,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
) -> dict[str, Any]:
    return list_action_queue(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        limit=limit,
        base_dir=base_dir,
    )


def queue_path(run_id: str, base_dir: Path) -> Path:
    return base_dir / run_id / "actions" / ACTION_QUEUE_FILENAME


def append_queue_event(
    *,
    run_id: str,
    base_dir: Path,
    event_type: str,
    action_id: str,
    action: dict[str, Any] | None = None,
    actor: str | None = None,
    reason: str = "",
    result: dict[str, Any] | None = None,
    source_command: str = "",
) -> dict[str, Any]:
    created_at = datetime.now(UTC).isoformat()
    event = {
        "schema": ACTION_EVENT_SCHEMA,
        "event_id": event_id(
            run_id=run_id,
            event_type=event_type,
            action_id=action_id,
            created_at=created_at,
        ),
        "run_id": run_id,
        "event_type": event_type,
        "action_id": action_id,
        "action_signature": action_signature(action or {}),
        "action": action,
        "actor": actor,
        "reason": reason,
        "result": result,
        "created_at": created_at,
        "source_command": source_command,
    }
    path = queue_path(run_id, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return event


def read_queue_events(run_id: str, base_dir: Path) -> list[dict[str, Any]]:
    path = queue_path(run_id, base_dir)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            events.append(
                {
                    "schema": ACTION_EVENT_SCHEMA,
                    "event_id": "",
                    "run_id": run_id,
                    "event_type": "corrupt_line",
                    "action_id": "",
                    "action": None,
                    "action_signature": "",
                    "actor": None,
                    "reason": "",
                    "result": {"line": line},
                    "created_at": "",
                    "source_command": "",
                }
            )
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def replay_run_queue(run_id: str, *, base_dir: Path) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for event in read_queue_events(run_id, base_dir):
        action_id = str(event.get("action_id") or "")
        if not action_id:
            continue
        event_type = event.get("event_type")
        if event_type == "queued":
            action = event.get("action") or {}
            items[action_id] = {
                "schema": ACTION_QUEUE_SCHEMA,
                "run_id": run_id,
                "action_id": action_id,
                "status": "queued",
                "action_type": action.get("action_type"),
                "label": action.get("label"),
                "reason": action.get("reason"),
                "suggested_cli_command": action.get("suggested_cli_command"),
                "related_ids": action.get("related_ids") or {},
                "artifact_refs": action.get("artifact_refs") or [],
                "blocks_execution": bool(action.get("blocks_execution")),
                "action_signature": event.get("action_signature") or action_signature(action),
                "action": action,
                "queued_at": event.get("created_at"),
                "approved_by": None,
                "approved_at": None,
                "rejected_by": None,
                "rejected_at": None,
                "executed_at": None,
                "execution_result": None,
            }
        elif event_type == "approved" and action_id in items:
            items[action_id]["status"] = "approved"
            items[action_id]["approved_by"] = event.get("actor")
            items[action_id]["approved_reason"] = event.get("reason")
            items[action_id]["approved_at"] = event.get("created_at")
        elif event_type == "rejected" and action_id in items:
            items[action_id]["status"] = "rejected"
            items[action_id]["rejected_by"] = event.get("actor")
            items[action_id]["rejected_reason"] = event.get("reason")
            items[action_id]["rejected_at"] = event.get("created_at")
        elif event_type == "executed" and action_id in items:
            result = event.get("result") or {}
            items[action_id]["status"] = status_from_result(result)
            items[action_id]["executed_at"] = event.get("created_at")
            items[action_id]["execution_result"] = compact_execution_result(result)
    return items


def get_queue_item(
    run_id: str,
    action_id: str,
    *,
    base_dir: Path,
) -> dict[str, Any] | None:
    return replay_run_queue(run_id, base_dir=base_dir).get(action_id)


def latest_actions_by_id(actions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(action.get("action_id")): action
        for action in actions
        if action.get("action_id") and action.get("run_id")
    }


def mark_stale_items(
    items: dict[str, dict[str, Any]],
    latest_actions: dict[str, dict[str, Any]],
) -> None:
    for action_id, item in items.items():
        if item.get("status") in TERMINAL_STATUSES:
            continue
        latest = latest_actions.get(action_id)
        if not latest or item.get("action_signature") != action_signature(latest):
            item["status"] = "stale"


def record_execution_result(
    *,
    run_id: str,
    action_id: str,
    action: dict[str, Any],
    base_dir: Path,
    result: dict[str, Any],
) -> dict[str, Any]:
    event = append_queue_event(
        run_id=run_id,
        base_dir=base_dir,
        event_type="executed",
        action_id=action_id,
        action=action,
        result=result,
        source_command="geond-orchestrator action execute",
    )
    return execution_payload(
        run_id=run_id,
        action_id=action_id,
        execute=True,
        action=action,
        item=get_queue_item(run_id, action_id, base_dir=base_dir),
        result=result,
        event=event,
        execution_status=status_from_result(result),
        status="ok" if result.get("status") == "ok" else result.get("status", "blocked"),
        code=result.get("code"),
        message=result.get("message"),
    )


def execution_payload(
    *,
    run_id: str,
    action_id: str,
    execute: bool,
    execution_status: str,
    status: str,
    code: str | None,
    message: str | None,
    action: dict[str, Any] | None = None,
    item: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": ACTION_EXECUTION_SCHEMA,
        "status": status,
        "code": code,
        "message": message,
        "run_id": run_id,
        "action_id": action_id,
        "execute": execute,
        "execution_status": execution_status,
        "action_type": (action or {}).get("action_type"),
        "action": action,
        "queue_item": item,
        "result": result,
        "event": compact_event(event) if event else None,
    }
    payload["markdown"] = format_execution_markdown(payload)
    return payload


def action_event_payload(
    *,
    run_id: str,
    event: dict[str, Any],
    item: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = {
        "schema": ACTION_EVENT_SCHEMA,
        "status": "ok",
        "code": None,
        "run_id": run_id,
        "event": compact_event(event),
        "queue_item": item,
    }
    payload["markdown"] = format_event_markdown(payload)
    return payload


def action_signature(action: dict[str, Any]) -> str:
    stable = {
        "action_id": action.get("action_id"),
        "action_type": action.get("action_type"),
        "reason": action.get("reason"),
        "suggested_cli_command": action.get("suggested_cli_command"),
        "related_ids": action.get("related_ids") or {},
        "task_graph_proposal": compact_graph_payload(action.get("task_graph_proposal")),
        "task_graph_review": compact_graph_payload(action.get("task_graph_review")),
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def event_id(*, run_id: str, event_type: str, action_id: str, created_at: str) -> str:
    raw = json.dumps(
        {
            "run_id": run_id,
            "event_type": event_type,
            "action_id": action_id,
            "created_at": created_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def status_from_result(result: dict[str, Any]) -> str:
    status = result.get("status")
    if status == "ok":
        return "executed"
    if status == "partial":
        return "failed"
    if status in {"blocked", "degraded"}:
        return "blocked"
    return "failed"


def compact_graph_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return None
    return {
        key: payload.get(key)
        for key in (
            "schema",
            "proposal_id",
            "review_id",
            "planner",
            "decision",
            "review_score",
            "suggested_apply_command",
            "tasks",
            "findings",
        )
        if key in payload
    }


def compact_execution_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result:
        return None
    return {
        "schema": result.get("schema"),
        "status": result.get("status"),
        "code": result.get("code"),
        "message": result.get("message"),
        "execution_status": result.get("execution_status"),
    }


def compact_event(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if not event:
        return None
    return {
        "schema": event.get("schema"),
        "event_id": event.get("event_id"),
        "run_id": event.get("run_id"),
        "event_type": event.get("event_type"),
        "action_id": event.get("action_id"),
        "actor": event.get("actor"),
        "reason": event.get("reason"),
        "created_at": event.get("created_at"),
    }


def compact_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in (compact_event(event) for event in events) if event]


def queue_paths_for_actions(actions: Any, base_dir: Path) -> list[str]:
    return sorted(
        {
            str(queue_path(str(action.get("run_id")), base_dir))
            for action in actions
            if isinstance(action, dict) and action.get("run_id")
        }
    )


def action_error(code: str, message: str) -> dict[str, Any]:
    return {
        "schema": ACTION_EVENT_SCHEMA,
        "status": "error",
        "code": code,
        "message": message,
    }


def format_queue_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Orchestrator Action Queue",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Items: `{payload.get('total_item_count', payload.get('item_count', 0))}`",
        f"- Queued: `{payload.get('queued_count', 0)}`",
        f"- Approved: `{payload.get('approved_count', 0)}`",
        f"- Blocked: `{payload.get('blocked_count', 0)}`",
        f"- Stale: `{payload.get('stale_count', 0)}`",
        "",
        "## Items",
    ]
    items = payload.get("items") or []
    if not items:
        lines.append("- none")
    for item in items:
        lines.append(
            f"- {item.get('action_id')} `{item.get('status')}` "
            f"{item.get('label') or item.get('action_type')}: "
            f"`{item.get('suggested_cli_command')}`"
        )
    return "\n".join(lines).rstrip() + "\n"


def format_execution_markdown(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Orchestrator Action Execution",
            "",
            f"- Run: `{payload.get('run_id')}`",
            f"- Action: `{payload.get('action_id')}`",
            f"- Execute: `{payload.get('execute')}`",
            f"- Status: `{payload.get('execution_status')}`",
            f"- Code: `{payload.get('code')}`",
            f"- Message: {payload.get('message') or ''}",
            "",
        ]
    )


def format_event_markdown(payload: dict[str, Any]) -> str:
    event = payload.get("event") or {}
    item = payload.get("queue_item") or {}
    return "\n".join(
        [
            "# Orchestrator Action Event",
            "",
            f"- Run: `{payload.get('run_id')}`",
            f"- Action: `{event.get('action_id')}`",
            f"- Event: `{event.get('event_type')}`",
            f"- Status: `{item.get('status')}`",
            "",
        ]
    )
