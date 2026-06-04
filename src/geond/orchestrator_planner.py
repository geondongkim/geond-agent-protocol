from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import Connection

from geond import orchestrator
from geond.storage import orchestration as orchestration_store

PLAN_SCHEMA = "geond.orchestrator_plan.v1"
DEFAULT_AGENT = "codex"
ACTIVE_RUN_STATUSES = {"active", "planned", "verifying", "review", "blocked", "paused"}
BLOCKING_FINDING_SEVERITIES = {"P0", "P1"}
HIGH_RISK_LEVELS = {"high", "critical"}


def create_plan(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    run_id: str | None = None,
    agents: list[str] | None = None,
    limit: int = 50,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    write_bundle: bool = False,
) -> dict[str, Any]:
    agent_pool = normalize_agents(agents)
    status_payloads = collect_status_payloads(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        run_id=run_id,
        agent_name=agent_pool[0],
        limit=limit,
        base_dir=base_dir,
    )
    if status_payloads.get("status") != "ok":
        return status_payloads

    run_plans = [
        build_run_plan(status_payload, agents=agent_pool, base_dir=base_dir)
        for status_payload in status_payloads["status_payloads"]
    ]
    actions = sorted(
        [action for run_plan in run_plans for action in run_plan["recommended_actions"]],
        key=lambda item: (
            item["priority"],
            item.get("run_id") or "",
            item.get("action_type") or "",
        ),
    )[:limit]
    payload = {
        "schema": PLAN_SCHEMA,
        "status": "ok",
        "code": None,
        "workspace_id_or_uri": workspace_id_or_uri,
        "run_id": run_id,
        "agents": agent_pool,
        "active_runs": [run_plan["run"] for run_plan in run_plans],
        "run_plans": run_plans,
        "blockers": [blocker for run_plan in run_plans for blocker in run_plan["blockers"]],
        "recommended_actions": actions,
        "runnable_dispatch_commands": [
            action["suggested_cli_command"]
            for action in actions
            if action["action_type"] in {"dispatch_claim", "dispatch_spawn"}
            and action.get("suggested_cli_command")
            and not action.get("blocks_execution")
        ],
        "recovery_commands": [
            command for run_plan in run_plans for command in run_plan.get("recovery_commands", [])
        ],
        "evidence_refs": [
            ref for run_plan in run_plans for ref in run_plan.get("evidence_refs", [])
        ],
        "summary": plan_summary(run_plans, actions),
    }
    payload["plan_id"] = stable_plan_id(payload)
    payload["markdown"] = format_plan_markdown(payload)
    if write_bundle:
        payload["bundle"] = write_plan_bundle(payload, base_dir=base_dir)
    return payload


def doctor_run(
    conn: Connection,
    run_id: str,
    *,
    agents: list[str] | None = None,
    limit: int = 50,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
) -> dict[str, Any]:
    status_payload = orchestrator.get_status(
        conn,
        run_id,
        agent_name=normalize_agents(agents)[0],
        manifest_base_dir=base_dir,
        limit=limit,
    )
    if status_payload.get("status") != "ok":
        return status_payload
    workspace_id = (status_payload.get("run") or {}).get("workspace_id") or ""
    return create_plan(
        conn,
        workspace_id_or_uri=workspace_id,
        run_id=run_id,
        agents=agents,
        limit=limit,
        base_dir=base_dir,
        write_bundle=False,
    )


def collect_status_payloads(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    run_id: str | None,
    agent_name: str,
    limit: int,
    base_dir: Path,
) -> dict[str, Any]:
    if run_id:
        status_payload = orchestrator.get_status(
            conn,
            run_id,
            agent_name=agent_name,
            manifest_base_dir=base_dir,
            limit=limit,
        )
        if status_payload.get("status") != "ok":
            return status_payload
        return {"status": "ok", "code": None, "status_payloads": [status_payload]}

    runs_result = orchestration_store.list_runs(conn, workspace_id_or_uri, limit=limit)
    if runs_result.get("status") != "ok":
        return runs_result
    runs = [run for run in runs_result.get("runs", []) if run.get("status") in ACTIVE_RUN_STATUSES][
        :limit
    ]
    status_payloads = []
    for run in runs:
        status_payload = orchestrator.get_status(
            conn,
            run["run_id"],
            agent_name=agent_name,
            manifest_base_dir=base_dir,
            limit=limit,
        )
        if status_payload.get("status") == "ok":
            status_payloads.append(status_payload)
    return {"status": "ok", "code": None, "status_payloads": status_payloads}


def build_run_plan(
    status_payload: dict[str, Any],
    *,
    agents: list[str],
    base_dir: Path,
) -> dict[str, Any]:
    run = status_payload.get("run") or {}
    run_id = str(run.get("run_id") or "")
    readiness = status_payload.get("readiness") or {}
    claimable_tasks = status_payload.get("claimable_tasks") or []
    ledger = status_payload.get("degraded_ledger") or {}
    actions: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    recovery_commands: list[str] = []
    evidence_refs: list[dict[str, str | None]] = []

    if ledger.get("pending_count"):
        action = planner_action(
            action_type="ledger_reconcile",
            priority=10,
            severity="critical",
            reason="Pending degraded ledger events must be reconciled before execution.",
            command=f"geond ledger reconcile {run_id}",
            run_id=run_id,
            blocks_execution=True,
        )
        actions.append(action)
        blockers.append(blocker_from_action(action))
        recovery_commands.append(action["suggested_cli_command"])

    add_approval_actions(
        actions,
        blockers,
        recovery_commands,
        evidence_refs,
        run=run,
        approvals=status_payload.get("pending_approvals") or [],
    )
    add_finding_actions(
        actions,
        blockers,
        recovery_commands,
        evidence_refs,
        run_id=run_id,
        findings=status_payload.get("open_findings") or [],
    )
    add_lease_recovery(
        blockers,
        recovery_commands,
        run_id=run_id,
        leases=status_payload.get("active_leases") or [],
    )

    blocking_actions = [action for action in actions if action.get("blocks_execution")]
    if not blocking_actions:
        if readiness.get("status") == "ready":
            actions.append(
                planner_action(
                    action_type="finalize_ready_run",
                    priority=70,
                    severity="info",
                    reason="Run is ready; finalize can produce manifest and git dry-run evidence.",
                    command=(
                        f"geond-orchestrator finalize {run_id} "
                        "--write-manifest --git-checkpoint --dry-run"
                    ),
                    run_id=run_id,
                    blocks_execution=False,
                )
            )
        elif claimable_tasks:
            actions.extend(dispatch_actions(run_id, claimable_tasks, agents))
        else:
            actions.append(
                planner_action(
                    action_type="create_task_needed",
                    priority=90,
                    severity="warning",
                    reason="No claimable task is available for this run.",
                    command=f'geond task create {run_id} --title "Describe next task"',
                    run_id=run_id,
                    blocks_execution=False,
                )
            )

    actions = sorted(actions, key=lambda item: (item["priority"], item["action_type"]))
    return {
        "run": {
            "run_id": run_id,
            "title": run.get("title"),
            "risk_level": run.get("risk_level"),
            "status": run.get("status"),
            "readiness_status": readiness.get("status"),
            "manifest_dir": str(base_dir / run_id),
        },
        "blockers": blockers,
        "recommended_actions": actions,
        "recovery_commands": recovery_commands,
        "evidence_refs": evidence_refs,
    }


def add_approval_actions(
    actions: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    recovery_commands: list[str],
    evidence_refs: list[dict[str, str | None]],
    *,
    run: dict[str, Any],
    approvals: list[dict[str, Any]],
) -> None:
    risk_level = str(run.get("risk_level") or "").lower()
    if risk_level not in HIGH_RISK_LEVELS:
        return
    for approval in approvals:
        approval_id = approval.get("approval_id")
        evidence_refs.append({"type": "approval_request", "id": approval_id})
        action = planner_action(
            action_type="resolve_approval",
            priority=20,
            severity="high",
            reason=(
                "High-risk run has pending approval: "
                f"{approval.get('reason') or 'approval required'}."
            ),
            command=(
                f"geond approval resolve {approval_id} --status approved --resolved-by <name>"
            ),
            run_id=run.get("run_id"),
            approval_id=approval_id,
            blocks_execution=True,
        )
        actions.append(action)
        blockers.append(blocker_from_action(action))
        recovery_commands.append(action["suggested_cli_command"])


def add_finding_actions(
    actions: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    recovery_commands: list[str],
    evidence_refs: list[dict[str, str | None]],
    *,
    run_id: str,
    findings: list[dict[str, Any]],
) -> None:
    for finding in findings:
        severity = str(finding.get("severity") or "")
        finding_id = finding.get("finding_id")
        evidence_refs.append({"type": "review_finding", "id": finding_id})
        if severity not in BLOCKING_FINDING_SEVERITIES:
            continue
        action = planner_action(
            action_type="resolve_finding",
            priority=30 if severity == "P1" else 25,
            severity="critical" if severity == "P0" else "high",
            reason=(
                f"{severity} review finding is open: {finding.get('summary') or 'review required'}."
            ),
            command=(f'geond review resolve {finding_id} --status fixed --reason "<reason>"'),
            run_id=run_id,
            task_id=finding.get("task_id"),
            finding_id=finding_id,
            blocks_execution=True,
        )
        actions.append(action)
        blockers.append(blocker_from_action(action))
        recovery_commands.append(action["suggested_cli_command"])


def add_lease_recovery(
    blockers: list[dict[str, Any]],
    recovery_commands: list[str],
    *,
    run_id: str,
    leases: list[dict[str, Any]],
) -> None:
    for lease in leases:
        if not lease_is_stale(lease):
            continue
        lease_id = lease.get("lease_id")
        reason = "Active lease has no heartbeat or has expired."
        blockers.append(
            {
                "severity": "warning",
                "reason": reason,
                "run_id": run_id,
                "task_id": lease.get("task_id"),
                "lease_id": lease_id,
            }
        )
        recovery_commands.append(
            f"geond worker release {lease_id} --reason stale "
            f"--worker-session-id {lease.get('worker_session_id') or '<worker_session_id>'}"
        )


def dispatch_actions(
    run_id: str,
    claimable_tasks: list[dict[str, Any]],
    agents: list[str],
) -> list[dict[str, Any]]:
    agent_csv = ",".join(agents)
    max_workers = max(1, min(len(claimable_tasks), len(agents)))
    first_task = claimable_tasks[0]
    return [
        planner_action(
            action_type="dispatch_claim",
            priority=50,
            severity="info",
            reason=f"Task is claimable: {first_task.get('title') or first_task.get('task_id')}.",
            command=f"geond-orchestrator dispatch --run {run_id} --mode claim --agent {agents[0]}",
            run_id=run_id,
            task_id=first_task.get("task_id"),
            blocks_execution=False,
        ),
        planner_action(
            action_type="dispatch_spawn",
            priority=55,
            severity="info",
            reason=f"{len(claimable_tasks)} task(s) are claimable for bounded spawn preview.",
            command=(
                f"geond-orchestrator dispatch --run {run_id} --mode spawn "
                f"--agents {agent_csv} --max-workers {max_workers}"
            ),
            run_id=run_id,
            task_id=first_task.get("task_id"),
            blocks_execution=False,
        ),
    ]


def planner_action(
    *,
    action_type: str,
    priority: int,
    severity: str,
    reason: str,
    command: str,
    run_id: str | None,
    blocks_execution: bool,
    task_id: str | None = None,
    finding_id: str | None = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    return {
        "action_type": action_type,
        "priority": priority,
        "severity": severity,
        "reason": reason,
        "suggested_cli_command": command,
        "related_ids": {
            key: value
            for key, value in {
                "run_id": run_id,
                "task_id": task_id,
                "finding_id": finding_id,
                "approval_id": approval_id,
            }.items()
            if value
        },
        "run_id": run_id,
        "task_id": task_id,
        "blocks_execution": blocks_execution,
    }


def blocker_from_action(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "severity": action["severity"],
        "reason": action["reason"],
        "run_id": action.get("run_id"),
        "task_id": action.get("task_id"),
        "action_type": action["action_type"],
        "related_ids": action.get("related_ids") or {},
    }


def lease_is_stale(lease: dict[str, Any]) -> bool:
    expires_at = parse_iso(lease.get("expires_at"))
    if expires_at and expires_at <= datetime.now(UTC):
        return True
    return not lease.get("last_heartbeat_at")


def normalize_agents(agents: list[str] | None) -> list[str]:
    normalized = [agent.strip() for agent in agents or [] if agent and agent.strip()]
    return normalized or [DEFAULT_AGENT]


def plan_summary(run_plans: list[dict[str, Any]], actions: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "run_count": len(run_plans),
        "blocking_action_count": sum(1 for action in actions if action.get("blocks_execution")),
        "dispatch_action_count": sum(
            1
            for action in actions
            if action.get("action_type") in {"dispatch_claim", "dispatch_spawn"}
        ),
        "recovery_command_count": sum(
            len(run_plan.get("recovery_commands", [])) for run_plan in run_plans
        ),
    }


def stable_plan_id(payload: dict[str, Any]) -> str:
    stable = {
        key: value for key, value in payload.items() if key not in {"plan_id", "markdown", "bundle"}
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def write_plan_bundle(payload: dict[str, Any], *, base_dir: Path) -> dict[str, str]:
    run_id = payload.get("run_id") or single_run_id(payload) or workspace_plan_dir(payload)
    plan_dir = base_dir / str(run_id) / "plans" / payload["plan_id"]
    plan_dir.mkdir(parents=True, exist_ok=True)
    json_path = plan_dir / "PLAN.json"
    markdown_path = plan_dir / "PLAN.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(payload.get("markdown", ""), encoding="utf-8")
    return {
        "plan_dir": str(plan_dir),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }


def single_run_id(payload: dict[str, Any]) -> str | None:
    runs = payload.get("active_runs") or []
    if len(runs) == 1:
        return runs[0].get("run_id")
    return None


def workspace_plan_dir(payload: dict[str, Any]) -> str:
    raw = str(payload.get("workspace_id_or_uri") or "workspace")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"workspace-{digest}"


def format_plan_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Geond Orchestrator Plan",
        "",
        f"- Plan: `{payload.get('plan_id')}`",
        f"- Runs: `{payload.get('summary', {}).get('run_count', 0)}`",
        f"- Blocking actions: `{payload.get('summary', {}).get('blocking_action_count', 0)}`",
        f"- Agents: `{','.join(payload.get('agents') or [])}`",
        "",
        "## Recommended Actions",
    ]
    lines.extend(
        markdown_list(
            (
                f"{action['priority']} {action['action_type']} "
                f"[{action['severity']}]: {action['reason']} "
                f"`{action['suggested_cli_command']}`"
            )
            for action in payload.get("recommended_actions") or []
        )
    )
    lines.extend(["", "## Recovery Commands"])
    lines.extend(
        markdown_list(f"`{command}`" for command in payload.get("recovery_commands") or [])
    )
    lines.extend(["", "## Dispatch Commands"])
    lines.extend(
        markdown_list(f"`{command}`" for command in payload.get("runnable_dispatch_commands") or [])
    )
    return "\n".join(lines).rstrip() + "\n"


def markdown_list(items: Any) -> list[str]:
    values = list(items)
    return [f"- {value}" for value in values] if values else ["- none"]


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
