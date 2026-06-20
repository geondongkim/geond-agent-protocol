from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from psycopg import Connection

from geond.storage.repository import resolve_workspace_id
from geond_orchestrator import orchestrator, orchestrator_action_queue, orchestrator_planner

BUDGET_SCHEMA = "geond.orchestrator_budget_report.v1"
AUTO_ACTIONS = {
    "ledger_reconcile",
    "materialize_task_graph",
    "dispatch_spawn",
    "finalize_ready_run",
}


def build_budget_report(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    run_id: str | None = None,
    agents: list[str] | None = None,
    max_actions: int = 5,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    budget_actions: int | None = None,
    budget_spawn_actions: int | None = None,
    budget_tokens: int | None = None,
    budget_cost_usd: Decimal | float | str | None = None,
    budget_window_hours: int = 24,
    estimate_spawn_tokens: int = 0,
    estimate_spawn_cost_usd: Decimal | float | str | None = None,
    selected_actions: list[dict[str, Any]] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    selected = selected_actions
    queue_summary: dict[str, Any] | None = None
    if selected is None:
        queue = orchestrator_action_queue.list_action_queue(
            conn,
            workspace_id_or_uri=workspace_id_or_uri,
            run_id=run_id,
            agents=orchestrator_planner.normalize_agents(agents),
            limit=limit,
            base_dir=base_dir,
        )
        if queue.get("status") != "ok":
            return queue
        queue_summary = compact_queue_summary(queue)
        selected = select_budget_actions(queue.get("items") or [], max_actions=max_actions)

    spawn_count = sum(1 for item in selected if item.get("action_type") == "dispatch_spawn")
    usage = usage_totals(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        budget_window_hours=budget_window_hours,
    )
    forecast_tokens = max(0, int(estimate_spawn_tokens or 0)) * spawn_count
    forecast_cost = decimal_or_zero(estimate_spawn_cost_usd) * Decimal(spawn_count)
    actual_tokens = int(usage.get("total_tokens") or 0)
    actual_cost = decimal_or_zero(usage.get("estimated_cost_usd"))
    token_projection = actual_tokens + forecast_tokens
    cost_projection = actual_cost + forecast_cost

    warnings: list[dict[str, Any]] = []
    blocking_reasons: list[dict[str, Any]] = []
    usage_budget_requested = budget_tokens is not None or budget_cost_usd is not None
    if usage_budget_requested and not usage.get("data_available"):
        reason = {
            "code": "USAGE_DATA_UNAVAILABLE",
            "message": "Usage data is unavailable for conservative budget enforcement.",
        }
        warnings.append(reason)
        blocking_reasons.append(reason)

    if budget_actions is not None and len(selected) > int(budget_actions):
        blocking_reasons.append(
            budget_reason(
                "ACTION_BUDGET_EXCEEDED",
                "Selected actions exceed the action budget.",
                len(selected),
                budget_actions,
            )
        )
    if budget_spawn_actions is not None and spawn_count > int(budget_spawn_actions):
        blocking_reasons.append(
            budget_reason(
                "SPAWN_ACTION_BUDGET_EXCEEDED",
                "Selected spawn actions exceed the spawn action budget.",
                spawn_count,
                budget_spawn_actions,
            )
        )
    if (
        budget_tokens is not None
        and usage.get("data_available")
        and token_projection > budget_tokens
    ):
        blocking_reasons.append(
            budget_reason(
                "TOKEN_BUDGET_EXCEEDED",
                "Projected token usage exceeds the token budget.",
                token_projection,
                budget_tokens,
            )
        )
    cost_limit = decimal_or_none(budget_cost_usd)
    if cost_limit is not None and usage.get("data_available") and cost_projection > cost_limit:
        blocking_reasons.append(
            budget_reason(
                "COST_BUDGET_EXCEEDED",
                "Projected cost exceeds the cost budget.",
                str(cost_projection),
                str(cost_limit),
            )
        )

    decision = "blocked" if blocking_reasons else "allow"
    payload = {
        "schema": BUDGET_SCHEMA,
        "status": "blocked" if blocking_reasons else "ok",
        "code": "ORCHESTRATOR_BUDGET_EXCEEDED" if blocking_reasons else None,
        "decision": decision,
        "workspace_id_or_uri": workspace_id_or_uri,
        "run_id": run_id,
        "budget_id": budget_id(
            workspace_id_or_uri=workspace_id_or_uri,
            run_id=run_id,
            selected_actions=selected,
            budget_window_hours=budget_window_hours,
        ),
        "budget_window_hours": budget_window_hours,
        "actual_usage": usage,
        "forecast": {
            "selected_actions": len(selected),
            "selected_spawn_actions": spawn_count,
            "estimated_spawn_tokens": forecast_tokens,
            "estimated_spawn_cost_usd": str(forecast_cost),
            "projected_tokens": token_projection,
            "projected_cost_usd": str(cost_projection),
        },
        "limits": {
            "budget_actions": budget_actions,
            "budget_spawn_actions": budget_spawn_actions,
            "budget_tokens": budget_tokens,
            "budget_cost_usd": str(cost_limit) if cost_limit is not None else None,
        },
        "remaining": {
            "actions": remaining_or_none(budget_actions, len(selected)),
            "spawn_actions": remaining_or_none(budget_spawn_actions, spawn_count),
            "tokens": remaining_or_none(budget_tokens, token_projection),
            "cost_usd": (
                str(cost_limit - cost_projection)
                if cost_limit is not None and usage.get("data_available")
                else None
            ),
        },
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "selected_action_refs": [
            {
                "run_id": item.get("run_id"),
                "action_id": item.get("action_id"),
                "action_type": item.get("action_type"),
            }
            for item in selected
        ],
        "queue_summary": queue_summary,
        "suggested_next_action": suggested_next_action(blocking_reasons),
    }
    payload["markdown"] = format_budget_markdown(payload)
    return payload


def build_dashboard_budget(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    limit: int = 50,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
) -> dict[str, Any]:
    return build_budget_report(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        max_actions=limit,
        base_dir=base_dir,
        limit=limit,
    )


def usage_totals(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    budget_window_hours: int,
) -> dict[str, Any]:
    window_hours = max(1, int(budget_window_hours or 24))
    window_start = datetime.now(UTC) - timedelta(hours=window_hours)
    try:
        workspace_id = resolve_workspace_id(conn, workspace_id_or_uri)
        if not workspace_id or not usage_table_exists(conn):
            return unavailable_usage(window_start, workspace_id=workspace_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    count(*) AS event_count,
                    coalesce(sum(total_tokens), 0) AS total_tokens,
                    sum(estimated_cost_usd) AS estimated_cost_usd
                FROM llm_usage_events
                WHERE workspace_id = %s
                  AND created_at >= %s
                """,
                (workspace_id, window_start),
            )
            row = cur.fetchone()
    except Exception:
        return unavailable_usage(window_start, workspace_id=None)

    event_count = int(row[0] or 0)
    return {
        "workspace_id": workspace_id,
        "window_start": window_start.isoformat(),
        "event_count": event_count,
        "total_tokens": int(row[1] or 0),
        "estimated_cost_usd": str(decimal_or_zero(row[2])),
        "data_available": event_count > 0,
    }


def usage_table_exists(conn: Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.llm_usage_events') IS NOT NULL")
        return bool(cur.fetchone()[0])


def unavailable_usage(window_start: datetime, *, workspace_id: str | None) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "window_start": window_start.isoformat(),
        "event_count": 0,
        "total_tokens": 0,
        "estimated_cost_usd": "0",
        "data_available": False,
    }


def select_budget_actions(items: list[dict[str, Any]], *, max_actions: int) -> list[dict[str, Any]]:
    selected = [
        item
        for item in items
        if item.get("status") == "approved" and item.get("action_type") in AUTO_ACTIONS
    ]
    selected = sorted(
        selected,
        key=lambda item: (item.get("run_id") or "", item.get("queued_at") or ""),
    )
    return selected[: max(1, max_actions)]


def compact_queue_summary(queue: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": queue.get("schema"),
        "status": queue.get("status"),
        "item_count": queue.get("item_count"),
        "total_item_count": queue.get("total_item_count"),
        "approved_count": queue.get("approved_count"),
        "blocked_count": queue.get("blocked_count"),
        "stale_count": queue.get("stale_count"),
    }


def budget_reason(code: str, message: str, actual: object, limit: object) -> dict[str, Any]:
    return {"code": code, "message": message, "actual": actual, "limit": limit}


def suggested_next_action(blocking_reasons: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not blocking_reasons:
        return None
    first = blocking_reasons[0]
    code = first.get("code")
    if code == "USAGE_DATA_UNAVAILABLE":
        return {
            "action_type": "review_usage_data",
            "suggested_cli_command": "geond dashboard-usage WORKSPACE_ID_OR_URI",
        }
    return {
        "action_type": "reduce_scheduler_scope",
        "suggested_cli_command": "geond-orchestrator scheduler plan --max-actions 1",
    }


def budget_id(
    *,
    workspace_id_or_uri: str,
    run_id: str | None,
    selected_actions: list[dict[str, Any]],
    budget_window_hours: int,
) -> str:
    raw = json.dumps(
        {
            "workspace_id_or_uri": workspace_id_or_uri,
            "run_id": run_id,
            "budget_window_hours": budget_window_hours,
            "selected": [
                {
                    "run_id": item.get("run_id"),
                    "action_id": item.get("action_id"),
                    "action_type": item.get("action_type"),
                }
                for item in selected_actions
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def remaining_or_none(limit: int | None, actual: int) -> int | None:
    if limit is None:
        return None
    return int(limit) - int(actual)


def decimal_or_none(value: Decimal | float | str | int | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def decimal_or_zero(value: Decimal | float | str | int | None) -> Decimal:
    resolved = decimal_or_none(value)
    return resolved if resolved is not None else Decimal("0")


def format_budget_markdown(payload: dict[str, Any]) -> str:
    forecast = payload.get("forecast") or {}
    limits = payload.get("limits") or {}
    actual = payload.get("actual_usage") or {}
    lines = [
        "# Orchestrator Budget",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Decision: `{payload.get('decision')}`",
        f"- Window hours: `{payload.get('budget_window_hours')}`",
        f"- Actual tokens: `{actual.get('total_tokens', 0)}`",
        f"- Actual cost USD: `{actual.get('estimated_cost_usd', '0')}`",
        f"- Projected tokens: `{forecast.get('projected_tokens', 0)}`",
        f"- Projected cost USD: `{forecast.get('projected_cost_usd', '0')}`",
        f"- Token limit: `{limits.get('budget_tokens')}`",
        f"- Cost limit USD: `{limits.get('budget_cost_usd')}`",
        "",
        "## Blocking Reasons",
    ]
    reasons = payload.get("blocking_reasons") or []
    if not reasons:
        lines.append("- none")
    for reason in reasons:
        lines.append(f"- `{reason.get('code')}` {reason.get('message')}")
    return "\n".join(lines).rstrip() + "\n"
