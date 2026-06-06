from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import Connection

from geond import orchestrator, orchestrator_action_queue, orchestrator_budget, orchestrator_planner

SCHEDULER_SCHEMA = "geond.orchestrator_scheduler.v1"
SCHEDULER_TRACE_SCHEMA = "geond.orchestrator_scheduler_trace.v1"
SCHEDULER_PLAN_JSON = "SCHEDULER_PLAN.json"
SCHEDULER_TRACE_JSONL = "SCHEDULER_TRACE.jsonl"
SCHEDULER_SUMMARY_MD = "SUMMARY.md"
AUTO_ACTIONS = {
    "ledger_reconcile",
    "materialize_task_graph",
    "dispatch_spawn",
    "finalize_ready_run",
}


def plan_scheduler(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    run_id: str | None = None,
    agents: list[str] | None = None,
    max_actions: int = 5,
    max_workers: int = 1,
    model: str | None = None,
    sandbox: str = "workspace-write",
    timeout_seconds: int = 3600,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    budget_actions: int | None = None,
    budget_spawn_actions: int | None = None,
    budget_tokens: int | None = None,
    budget_cost_usd: float | str | None = None,
    budget_window_hours: int = 24,
    estimate_spawn_tokens: int = 0,
    estimate_spawn_cost_usd: float | str | None = None,
    limit: int = 50,
    write_bundle: bool = False,
) -> dict[str, Any]:
    payload = build_scheduler_payload(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        run_id=run_id,
        agents=agents,
        max_actions=max_actions,
        max_workers=max_workers,
        model=model,
        sandbox=sandbox,
        timeout_seconds=timeout_seconds,
        base_dir=base_dir,
        budget_actions=budget_actions,
        budget_spawn_actions=budget_spawn_actions,
        budget_tokens=budget_tokens,
        budget_cost_usd=budget_cost_usd,
        budget_window_hours=budget_window_hours,
        estimate_spawn_tokens=estimate_spawn_tokens,
        estimate_spawn_cost_usd=estimate_spawn_cost_usd,
        limit=limit,
        execute=False,
    )
    if write_bundle:
        payload["bundle"] = write_scheduler_bundle(payload, base_dir=base_dir, trace_steps=[])
    return payload


def drain_scheduler(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    run_id: str | None = None,
    execute: bool = False,
    agents: list[str] | None = None,
    max_actions: int = 5,
    max_workers: int = 1,
    model: str | None = None,
    sandbox: str = "workspace-write",
    timeout_seconds: int = 3600,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    budget_actions: int | None = None,
    budget_spawn_actions: int | None = None,
    budget_tokens: int | None = None,
    budget_cost_usd: float | str | None = None,
    budget_window_hours: int = 24,
    estimate_spawn_tokens: int = 0,
    estimate_spawn_cost_usd: float | str | None = None,
    limit: int = 50,
    write_bundle: bool = False,
) -> dict[str, Any]:
    payload = build_scheduler_payload(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        run_id=run_id,
        agents=agents,
        max_actions=max_actions,
        max_workers=max_workers,
        model=model,
        sandbox=sandbox,
        timeout_seconds=timeout_seconds,
        base_dir=base_dir,
        budget_actions=budget_actions,
        budget_spawn_actions=budget_spawn_actions,
        budget_tokens=budget_tokens,
        budget_cost_usd=budget_cost_usd,
        budget_window_hours=budget_window_hours,
        estimate_spawn_tokens=estimate_spawn_tokens,
        estimate_spawn_cost_usd=estimate_spawn_cost_usd,
        limit=limit,
        execute=execute,
    )
    if payload.get("status") != "ok":
        if write_bundle or execute:
            payload["bundle"] = write_scheduler_bundle(payload, base_dir=base_dir, trace_steps=[])
        return payload
    if budget_exceeded(payload):
        payload.update(
            {
                "status": "blocked",
                "code": "ORCHESTRATOR_BUDGET_EXCEEDED",
                "execution_status": "blocked",
                "message": "Scheduler budget would be exceeded.",
                "selected_actions": [],
            }
        )
        payload["markdown"] = format_scheduler_markdown(payload)
        if write_bundle or execute:
            payload["bundle"] = write_scheduler_bundle(payload, base_dir=base_dir, trace_steps=[])
        return payload
    if not execute:
        if write_bundle:
            payload["bundle"] = write_scheduler_bundle(payload, base_dir=base_dir, trace_steps=[])
        return payload

    trace_steps: list[dict[str, Any]] = []
    executed_count = 0
    for index, selection in enumerate(payload.get("selected_actions") or []):
        result = orchestrator_action_queue.execute_queued_action(
            conn,
            run_id=str(selection["run_id"]),
            action_id=str(selection["action_id"]),
            execute=True,
            agents=payload.get("agents") or [],
            max_workers=max_workers,
            model=model,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            base_dir=base_dir,
        )
        step = {
            "schema": SCHEDULER_TRACE_SCHEMA,
            "step_index": index,
            "run_id": selection.get("run_id"),
            "action_id": selection.get("action_id"),
            "action_type": selection.get("action_type"),
            "result_status": result.get("execution_status") or result.get("status"),
            "result_code": result.get("code"),
            "result": compact_execution_result(result),
            "created_at": datetime.now(UTC).isoformat(),
        }
        trace_steps.append(step)
        if result.get("execution_status") == "executed":
            executed_count += 1
        if result.get("execution_status") in {"blocked", "failed"} or result.get("status") in {
            "blocked",
            "degraded",
            "failed",
            "partial",
            "error",
        }:
            payload["stop_reason"] = result.get("code") or result.get("execution_status")
            break

        refreshed = orchestrator_action_queue.list_action_queue(
            conn,
            workspace_id_or_uri=workspace_id_or_uri,
            run_id=run_id,
            agents=payload.get("agents") or [],
            limit=limit,
            base_dir=base_dir,
        )
        if queue_has_blocking_status(refreshed):
            payload["stop_reason"] = "QUEUE_STATUS_BLOCKED"
            trace_steps.append(
                {
                    "schema": SCHEDULER_TRACE_SCHEMA,
                    "step_index": index,
                    "run_id": selection.get("run_id"),
                    "action_id": selection.get("action_id"),
                    "action_type": "queue_refresh",
                    "result_status": "blocked",
                    "result_code": "QUEUE_STATUS_BLOCKED",
                    "result": queue_blocking_summary(refreshed),
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            break

    payload["trace_steps"] = trace_steps
    payload["executed_count"] = executed_count
    payload["execution_status"] = "completed" if not payload.get("stop_reason") else "blocked"
    payload["status"] = "ok" if payload["execution_status"] == "completed" else "blocked"
    payload["code"] = None if payload["status"] == "ok" else payload.get("stop_reason")
    payload["markdown"] = format_scheduler_markdown(payload)
    payload["bundle"] = write_scheduler_bundle(payload, base_dir=base_dir, trace_steps=trace_steps)
    return payload


def build_dashboard_scheduler(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    limit: int = 50,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
) -> dict[str, Any]:
    return plan_scheduler(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        max_actions=limit,
        limit=limit,
        base_dir=base_dir,
    )


def build_scheduler_payload(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    run_id: str | None,
    agents: list[str] | None,
    max_actions: int,
    max_workers: int,
    model: str | None,
    sandbox: str,
    timeout_seconds: int,
    base_dir: Path,
    budget_actions: int | None,
    budget_spawn_actions: int | None,
    budget_tokens: int | None,
    budget_cost_usd: float | str | None,
    budget_window_hours: int,
    estimate_spawn_tokens: int,
    estimate_spawn_cost_usd: float | str | None,
    limit: int,
    execute: bool,
) -> dict[str, Any]:
    agent_pool = orchestrator_planner.normalize_agents(agents)
    max_actions = max(1, max_actions)
    max_workers = max(1, max_workers)
    queue = orchestrator_action_queue.list_action_queue(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        run_id=run_id,
        agents=agent_pool,
        limit=limit,
        base_dir=base_dir,
    )
    if queue.get("status") != "ok":
        return queue
    decisions = classify_queue_items(queue.get("items") or [])
    selected = decisions["selected"][:max_actions]
    payload = {
        "schema": SCHEDULER_SCHEMA,
        "status": "ok",
        "code": None,
        "execution_status": "preview" if not execute else "ready",
        "workspace_id_or_uri": workspace_id_or_uri,
        "run_id": run_id,
        "scheduler_id": scheduler_id(
            workspace_id_or_uri=workspace_id_or_uri,
            run_id=run_id,
            selected=selected,
            execute=execute,
        ),
        "execute": execute,
        "agents": agent_pool,
        "max_actions": max_actions,
        "max_workers": max_workers,
        "model": model,
        "sandbox": sandbox,
        "timeout_seconds": timeout_seconds,
        "budget": {
            "budget_actions": budget_actions,
            "budget_spawn_actions": budget_spawn_actions,
            "budget_tokens": budget_tokens,
            "budget_cost_usd": str(budget_cost_usd) if budget_cost_usd is not None else None,
            "budget_window_hours": budget_window_hours,
            "estimate_spawn_tokens": estimate_spawn_tokens,
            "estimate_spawn_cost_usd": (
                str(estimate_spawn_cost_usd) if estimate_spawn_cost_usd is not None else None
            ),
            "selected_actions": len(selected),
            "selected_spawn_actions": sum(
                1 for item in selected if item.get("action_type") == "dispatch_spawn"
            ),
        },
        "selected_actions": selected,
        "skipped_actions": decisions["skipped"],
        "queue_summary": compact_queue_summary(queue),
        "trace_steps": [],
        "executed_count": 0,
        "stop_reason": None,
    }
    payload["budget_report"] = orchestrator_budget.build_budget_report(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        run_id=run_id,
        agents=agent_pool,
        max_actions=max_actions,
        base_dir=base_dir,
        budget_actions=budget_actions,
        budget_spawn_actions=budget_spawn_actions,
        budget_tokens=budget_tokens,
        budget_cost_usd=budget_cost_usd,
        budget_window_hours=budget_window_hours,
        estimate_spawn_tokens=estimate_spawn_tokens,
        estimate_spawn_cost_usd=estimate_spawn_cost_usd,
        selected_actions=selected,
        limit=limit,
    )
    if payload["budget_report"].get("decision") == "blocked":
        payload.update(
            {
                "status": "blocked",
                "code": "ORCHESTRATOR_BUDGET_EXCEEDED",
                "execution_status": "blocked",
                "message": "Scheduler budget would be exceeded.",
            }
        )
    payload["markdown"] = format_scheduler_markdown(payload)
    return payload


def classify_queue_items(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in items:
        reason = skip_reason(item)
        if reason:
            skipped.append(scheduler_item(item, skip_reason=reason))
            continue
        selected.append(scheduler_item(item))
    selected = sorted(
        selected,
        key=lambda item: (item.get("run_id") or "", item.get("queued_at") or ""),
    )
    skipped = sorted(
        skipped,
        key=lambda item: (item.get("run_id") or "", item.get("action_id") or ""),
    )
    return {"selected": selected, "skipped": skipped}


def skip_reason(item: dict[str, Any]) -> str | None:
    status = item.get("status")
    action_type = item.get("action_type")
    if status != "approved":
        return f"status:{status or 'unknown'}"
    if action_type not in AUTO_ACTIONS:
        return f"manual:{action_type or 'unknown'}"
    return None


def scheduler_item(item: dict[str, Any], *, skip_reason: str | None = None) -> dict[str, Any]:
    return {
        "run_id": item.get("run_id"),
        "action_id": item.get("action_id"),
        "action_type": item.get("action_type"),
        "label": item.get("label"),
        "status": item.get("status"),
        "reason": item.get("reason"),
        "skip_reason": skip_reason,
        "suggested_cli_command": item.get("suggested_cli_command"),
        "related_ids": item.get("related_ids") or {},
        "artifact_refs": item.get("artifact_refs") or [],
        "approved_by": item.get("approved_by"),
        "approved_at": item.get("approved_at"),
        "queued_at": item.get("queued_at"),
    }


def budget_exceeded(payload: dict[str, Any]) -> bool:
    budget = payload.get("budget") or {}
    budget_actions = budget.get("budget_actions")
    budget_spawn_actions = budget.get("budget_spawn_actions")
    if budget_actions is not None and budget["selected_actions"] > int(budget_actions):
        return True
    if budget_spawn_actions is not None and budget["selected_spawn_actions"] > int(
        budget_spawn_actions
    ):
        return True
    budget_report = payload.get("budget_report") or {}
    return budget_report.get("decision") == "blocked"


def queue_has_blocking_status(queue: dict[str, Any]) -> bool:
    return any(
        item.get("status") in {"stale", "blocked", "failed"} for item in queue.get("items") or []
    )


def queue_blocking_summary(queue: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": queue.get("schema"),
        "status": queue.get("status"),
        "blocked_items": [
            {
                "run_id": item.get("run_id"),
                "action_id": item.get("action_id"),
                "status": item.get("status"),
                "action_type": item.get("action_type"),
            }
            for item in queue.get("items") or []
            if item.get("status") in {"stale", "blocked", "failed"}
        ],
    }


def compact_queue_summary(queue: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": queue.get("schema"),
        "status": queue.get("status"),
        "item_count": queue.get("item_count"),
        "total_item_count": queue.get("total_item_count"),
        "queued_count": queue.get("queued_count"),
        "approved_count": queue.get("approved_count"),
        "blocked_count": queue.get("blocked_count"),
        "stale_count": queue.get("stale_count"),
    }


def compact_execution_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": result.get("schema"),
        "status": result.get("status"),
        "code": result.get("code"),
        "message": result.get("message"),
        "execution_status": result.get("execution_status"),
        "run_id": result.get("run_id"),
        "action_id": result.get("action_id"),
    }


def scheduler_id(
    *,
    workspace_id_or_uri: str,
    run_id: str | None,
    selected: list[dict[str, Any]],
    execute: bool,
) -> str:
    raw = json.dumps(
        {
            "workspace_id_or_uri": workspace_id_or_uri,
            "run_id": run_id,
            "selected": [
                {
                    "run_id": item.get("run_id"),
                    "action_id": item.get("action_id"),
                    "status": item.get("status"),
                }
                for item in selected
            ],
            "execute": execute,
            "created_at": datetime.now(UTC).isoformat() if execute else None,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def write_scheduler_bundle(
    payload: dict[str, Any],
    *,
    base_dir: Path,
    trace_steps: list[dict[str, Any]],
) -> dict[str, str]:
    bundle_dir = scheduler_bundle_dir(payload, base_dir=base_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    plan_path = bundle_dir / SCHEDULER_PLAN_JSON
    trace_path = bundle_dir / SCHEDULER_TRACE_JSONL
    summary_path = bundle_dir / SCHEDULER_SUMMARY_MD
    plan_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    with trace_path.open("w", encoding="utf-8") as handle:
        for step in trace_steps:
            handle.write(json.dumps(step, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    summary_path.write_text(payload.get("markdown", ""), encoding="utf-8")
    return {
        "scheduler_dir": str(bundle_dir),
        "plan_path": str(plan_path),
        "trace_path": str(trace_path),
        "summary_path": str(summary_path),
    }


def scheduler_bundle_dir(payload: dict[str, Any], *, base_dir: Path) -> Path:
    target = payload.get("run_id")
    if not target:
        digest = hashlib.sha256(str(payload.get("workspace_id_or_uri")).encode("utf-8")).hexdigest()
        target = f"workspace-{digest[:12]}"
    return base_dir / str(target) / "scheduler" / str(payload["scheduler_id"])


def format_scheduler_markdown(payload: dict[str, Any]) -> str:
    budget = payload.get("budget") or {}
    lines = [
        "# Orchestrator Scheduler",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Execution: `{payload.get('execution_status')}`",
        f"- Selected: `{len(payload.get('selected_actions') or [])}`",
        f"- Skipped: `{len(payload.get('skipped_actions') or [])}`",
        f"- Executed: `{payload.get('executed_count', 0)}`",
        f"- Budget actions: `{budget.get('selected_actions')}/{budget.get('budget_actions')}`",
        (
            "- Budget spawn actions: "
            f"`{budget.get('selected_spawn_actions')}/{budget.get('budget_spawn_actions')}`"
        ),
        (
            "- Budget tokens: "
            f"`{(payload.get('budget_report') or {}).get('forecast', {}).get('projected_tokens')}"
            f"/{budget.get('budget_tokens')}`"
        ),
        (
            "- Budget cost USD: "
            f"`{(payload.get('budget_report') or {}).get('forecast', {}).get('projected_cost_usd')}"
            f"/{budget.get('budget_cost_usd')}`"
        ),
        "",
        "## Selected Actions",
    ]
    selected = payload.get("selected_actions") or []
    if not selected:
        lines.append("- none")
    for item in selected:
        lines.append(
            f"- {item.get('action_id')} `{item.get('action_type')}` run `{item.get('run_id')}`"
        )
    lines.extend(["", "## Skipped Actions"])
    skipped = payload.get("skipped_actions") or []
    if not skipped:
        lines.append("- none")
    for item in skipped[:20]:
        lines.append(
            f"- {item.get('action_id')} `{item.get('action_type')}` {item.get('skip_reason')}"
        )
    return "\n".join(lines).rstrip() + "\n"
