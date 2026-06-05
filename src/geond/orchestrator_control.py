from __future__ import annotations

import hashlib
import json
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import Connection

from geond import degraded_ledger, orchestrator, orchestrator_planner, orchestrator_task_planner

CONTROL_SCHEMA = "geond.orchestrator_control.v1"
AUTO_ACTIONS = {
    "ledger_reconcile",
    "dispatch_spawn",
    "finalize_ready_run",
    "materialize_task_graph",
}
MANUAL_ACTIONS = {"resolve_approval", "resolve_finding", "create_task_needed", "dispatch_claim"}
ACTION_PREFERENCE = [
    "ledger_reconcile",
    "resolve_approval",
    "resolve_finding",
    "materialize_task_graph",
    "dispatch_spawn",
    "finalize_ready_run",
    "create_task_needed",
    "dispatch_claim",
]


def run_plan_mode(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    run_id: str | None = None,
    agents: list[str] | None = None,
    limit: int = 50,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    write_bundle: bool = False,
    propose_task_graph: bool = False,
    template: str = "auto",
) -> dict[str, Any]:
    plan = orchestrator_planner.create_plan(
        conn,
        workspace_id_or_uri=workspace_id_or_uri,
        run_id=run_id,
        agents=agents,
        limit=limit,
        base_dir=base_dir,
        write_bundle=False,
    )
    if plan.get("status") != "ok":
        return plan
    task_graph_proposal = None
    if propose_task_graph and run_id:
        task_graph_proposal = orchestrator_task_planner.propose_task_graph(
            conn,
            run_id,
            template=template,
        )
        if task_graph_proposal.get("status") == "ok":
            plan = inject_task_graph_action(plan, task_graph_proposal)
    selected_action = select_agent_action(plan)
    payload = control_payload(
        mode="plan",
        run_id=run_id or infer_single_run_id(plan),
        execute=False,
        plan=plan,
        selected_action=selected_action,
        steps=[],
        agents=plan.get("agents") or orchestrator_planner.normalize_agents(agents),
        max_steps=0,
        max_workers=1,
    )
    payload["task_graph_proposal"] = task_graph_proposal
    payload["proposal_id"] = (task_graph_proposal or {}).get("proposal_id")
    payload["suggested_apply_command"] = (task_graph_proposal or {}).get("suggested_apply_command")
    payload["markdown"] = format_control_markdown(payload)
    if write_bundle:
        payload["bundle"] = write_control_bundle(payload, base_dir=base_dir)
    return payload


def preview_agent_step(
    conn: Connection,
    run_id: str,
    *,
    agents: list[str] | None = None,
    max_workers: int = 1,
    model: str | None = None,
    sandbox: str = "workspace-write",
    timeout_seconds: int = 3600,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    limit: int = 50,
) -> dict[str, Any]:
    return run_agent_mode(
        conn,
        run_id,
        execute=False,
        max_steps=1,
        agents=agents,
        max_workers=max_workers,
        model=model,
        sandbox=sandbox,
        timeout_seconds=timeout_seconds,
        write_bundle=False,
        base_dir=base_dir,
        limit=limit,
    )


def run_agent_mode(
    conn: Connection,
    run_id: str,
    *,
    execute: bool = False,
    max_steps: int = 1,
    agents: list[str] | None = None,
    max_workers: int = 1,
    model: str | None = None,
    sandbox: str = "workspace-write",
    timeout_seconds: int = 3600,
    write_bundle: bool = False,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    limit: int = 50,
    allow_task_graph_create: bool = False,
    template: str = "auto",
) -> dict[str, Any]:
    if max_steps < 1:
        return error_payload("VALIDATION_ERROR", "--max-steps must be at least 1.")
    agent_pool = orchestrator_planner.normalize_agents(agents)
    max_workers = max(1, max_workers)
    control_id = new_control_id(
        run_id=run_id,
        mode="agent",
        execute=execute,
        seed={
            "agents": agent_pool,
            "max_steps": max_steps,
            "max_workers": max_workers,
            "model": model,
            "sandbox": sandbox,
            "timeout_seconds": timeout_seconds,
            "allow_task_graph_create": allow_task_graph_create,
            "template": template,
        },
    )
    control_dir = base_dir / run_id / "control" / control_id
    steps: list[dict[str, Any]] = []
    last_plan: dict[str, Any] | None = None
    selected_action: dict[str, Any] | None = None
    execution_status = "preview"
    status = "ok"
    code = None

    if execute:
        control_dir.mkdir(parents=True, exist_ok=True)

    for step_index in range(max_steps):
        plan = orchestrator_planner.doctor_run(
            conn,
            run_id,
            agents=agent_pool,
            limit=limit,
            base_dir=base_dir,
        )
        last_plan = plan
        if plan.get("status") != "ok":
            status = "error"
            code = plan.get("code")
            execution_status = "failed"
            break
        task_graph_proposal = orchestrator_task_planner.propose_task_graph(
            conn,
            run_id,
            template=template,
        )
        if task_graph_proposal.get("status") == "ok":
            plan = inject_task_graph_action(plan, task_graph_proposal)
            last_plan = plan

        selected_action = select_agent_action(plan)
        if not selected_action:
            steps.append(no_action_step(step_index, plan))
            execution_status = "completed" if execute else "preview"
            break

        step = build_step_preview(
            step_index,
            selected_action,
            plan,
            agent_pool=agent_pool,
            max_workers=max_workers,
            model=model,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execute=execute,
        )
        if not execute:
            steps.append(step)
            break

        if selected_action.get("action_type") == "materialize_task_graph" and not (
            allow_task_graph_create
        ):
            step["step_status"] = "manual_required"
            step["blocks_execution"] = True
            step["result"] = {
                "status": "blocked",
                "code": "TASK_GRAPH_APPROVAL_REQUIRED",
                "message": (
                    "Agent Mode requires --allow-task-graph-create before materializing "
                    "a proposed task graph."
                ),
            }
            steps.append(step)
            append_trace_step(control_dir, step)
            execution_status = "blocked"
            status = "blocked"
            code = "TASK_GRAPH_APPROVAL_REQUIRED"
            break

        if not action_is_auto_executable(selected_action):
            step["step_status"] = "manual_required"
            step["blocks_execution"] = True
            step["result"] = {
                "status": "blocked",
                "code": "HUMAN_ACTION_REQUIRED",
                "message": (
                    "Agent Mode stops before human approval, finding resolution, or task creation."
                ),
            }
            steps.append(step)
            append_trace_step(control_dir, step)
            execution_status = "blocked"
            status = "blocked"
            code = "HUMAN_ACTION_REQUIRED"
            break

        result = execute_action(
            conn,
            selected_action,
            run_id=run_id,
            agents=agent_pool,
            max_workers=max_workers,
            model=model,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            write_bundle=write_bundle,
            base_dir=base_dir,
            allow_task_graph_create=allow_task_graph_create,
        )
        step["result"] = compact_result(result)
        step["step_status"] = step_status_from_result(result)
        after_plan = orchestrator_planner.doctor_run(
            conn,
            run_id,
            agents=agent_pool,
            limit=limit,
            base_dir=base_dir,
        )
        if after_plan.get("status") == "ok":
            after_proposal = orchestrator_task_planner.propose_task_graph(
                conn,
                run_id,
                template=template,
            )
            if after_proposal.get("status") == "ok":
                after_plan = inject_task_graph_action(after_plan, after_proposal)
            step["after_readiness"] = readiness_from_plan(after_plan)
            last_plan = after_plan
        steps.append(step)
        append_trace_step(control_dir, step)

        if step["step_status"] in {"failed", "blocked", "degraded", "partial"}:
            execution_status = step["step_status"]
            if step["step_status"] == "degraded":
                status = "degraded"
            elif step["step_status"] == "partial":
                status = "partial"
            else:
                status = "error"
            code = result.get("code")
            break
        execution_status = "completed"

    payload = control_payload(
        mode="agent",
        run_id=run_id,
        execute=execute,
        plan=last_plan,
        selected_action=selected_action,
        steps=steps,
        agents=agent_pool,
        max_steps=max_steps,
        max_workers=max_workers,
        control_id=control_id,
        execution_status=execution_status,
        status=status,
        code=code,
    )
    payload["control_dir"] = str(control_dir) if execute or write_bundle else None
    payload["task_graph_proposal"] = (selected_action or {}).get("task_graph_proposal")
    payload["proposal_id"] = (payload["task_graph_proposal"] or {}).get("proposal_id")
    payload["suggested_apply_command"] = (payload["task_graph_proposal"] or {}).get(
        "suggested_apply_command"
    )
    payload["markdown"] = format_control_markdown(payload)
    if execute or write_bundle:
        payload["bundle"] = write_control_bundle(
            payload,
            base_dir=base_dir,
            control_dir=control_dir,
        )
    return payload


def select_agent_action(plan: dict[str, Any]) -> dict[str, Any] | None:
    actions = plan.get("recommended_actions") or []
    for action_type in ACTION_PREFERENCE:
        for action in actions:
            if action.get("action_type") == action_type:
                return action
    return actions[0] if actions else None


def action_is_auto_executable(action: dict[str, Any]) -> bool:
    return str(action.get("action_type") or "") in AUTO_ACTIONS


def execute_action(
    conn: Connection,
    action: dict[str, Any],
    *,
    run_id: str,
    agents: list[str],
    max_workers: int,
    model: str | None,
    sandbox: str,
    timeout_seconds: int,
    write_bundle: bool,
    base_dir: Path,
    allow_task_graph_create: bool,
) -> dict[str, Any]:
    action_type = action.get("action_type")
    if action_type == "ledger_reconcile":
        return degraded_ledger.reconcile(conn, run_id=run_id, base_dir=base_dir, dry_run=False)
    if action_type == "dispatch_spawn":
        return orchestrator.dispatch_spawn(
            conn,
            run_id=run_id,
            agent_name=agents[0],
            execute=True,
            agent_names=agents,
            max_workers=max_workers,
            model=model,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            write_bundle=write_bundle,
            manifest_base_dir=base_dir,
        )
    if action_type == "finalize_ready_run":
        return orchestrator.finalize_run(
            conn,
            run_id,
            write_manifest=True,
            manifest_base_dir=base_dir,
            git_checkpoint=True,
            dry_run=True,
        )
    if action_type == "materialize_task_graph":
        if not allow_task_graph_create:
            return {
                "status": "blocked",
                "code": "TASK_GRAPH_APPROVAL_REQUIRED",
                "message": "Task graph materialization requires explicit approval.",
            }
        proposal = action.get("task_graph_proposal") or {}
        return orchestrator_task_planner.apply_task_graph_payload(
            conn,
            run_id,
            proposal,
            execute=True,
        )
    return {
        "status": "blocked",
        "code": "HUMAN_ACTION_REQUIRED",
        "message": "Action requires a human or a dedicated CLI command.",
    }


def build_step_preview(
    step_index: int,
    action: dict[str, Any],
    plan: dict[str, Any],
    *,
    agent_pool: list[str],
    max_workers: int,
    model: str | None,
    sandbox: str,
    timeout_seconds: int,
    execute: bool,
) -> dict[str, Any]:
    run_id = str(action.get("run_id") or infer_single_run_id(plan) or "")
    return {
        "step_index": step_index,
        "step_status": "preview",
        "action_type": action.get("action_type"),
        "selected_action": action,
        "before_readiness": readiness_from_plan(plan),
        "after_readiness": None,
        "delegated_command": delegated_command(
            action,
            run_id=run_id,
            agents=agent_pool,
            max_workers=max_workers,
            model=model,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execute=execute,
        ),
        "blocks_execution": bool(action.get("blocks_execution"))
        or str(action.get("action_type")) in MANUAL_ACTIONS,
        "result": None,
    }


def no_action_step(step_index: int, plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_index": step_index,
        "step_status": "no_action",
        "action_type": None,
        "selected_action": None,
        "before_readiness": readiness_from_plan(plan),
        "after_readiness": readiness_from_plan(plan),
        "delegated_command": None,
        "blocks_execution": False,
        "result": {
            "status": "ok",
            "code": None,
            "message": "No recommended action is available.",
        },
    }


def delegated_command(
    action: dict[str, Any],
    *,
    run_id: str,
    agents: list[str],
    max_workers: int,
    model: str | None,
    sandbox: str,
    timeout_seconds: int,
    execute: bool,
) -> str | None:
    action_type = action.get("action_type")
    if action_type == "dispatch_spawn":
        parts = [
            "geond-orchestrator",
            "dispatch",
            "--run",
            run_id,
            "--mode",
            "spawn",
            "--agents",
            ",".join(agents),
            "--max-workers",
            str(max_workers),
            "--sandbox",
            sandbox,
            "--timeout-seconds",
            str(timeout_seconds),
        ]
        if model:
            parts.extend(["--model", model])
        if execute:
            parts.append("--execute")
        return shlex.join(parts)
    if action_type == "finalize_ready_run":
        return shlex.join(
            [
                "geond-orchestrator",
                "finalize",
                run_id,
                "--write-manifest",
                "--git-checkpoint",
                "--dry-run",
            ]
        )
    if action_type == "ledger_reconcile":
        return shlex.join(["geond", "ledger", "reconcile", run_id])
    if action_type == "materialize_task_graph":
        if action.get("suggested_cli_command"):
            return action["suggested_cli_command"]
        parts = ["geond-orchestrator", "agent", run_id, "--template"]
        proposal = action.get("task_graph_proposal") or {}
        parts.append(str(proposal.get("template") or "auto"))
        if execute:
            parts.extend(["--execute", "--allow-task-graph-create"])
        return shlex.join(parts)
    return action.get("suggested_cli_command")


def control_payload(
    *,
    mode: str,
    run_id: str | None,
    execute: bool,
    plan: dict[str, Any] | None,
    selected_action: dict[str, Any] | None,
    steps: list[dict[str, Any]],
    agents: list[str],
    max_steps: int,
    max_workers: int,
    control_id: str | None = None,
    execution_status: str = "preview",
    status: str = "ok",
    code: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": CONTROL_SCHEMA,
        "status": status,
        "code": code,
        "mode": mode,
        "execute": execute,
        "control_id": control_id
        or new_control_id(
            run_id=run_id or "workspace",
            mode=mode,
            execute=False,
            seed={"plan_id": (plan or {}).get("plan_id"), "agents": agents},
        ),
        "run_id": run_id,
        "agents": agents,
        "max_steps": max_steps,
        "max_workers": max_workers,
        "execution_status": execution_status,
        "plan": plan,
        "selected_action": selected_action,
        "steps": steps,
        "step_count": len(steps),
    }
    payload["next_action"] = (selected_action or {}).get("action_type")
    payload["delegated_command"] = steps[0].get("delegated_command") if steps else None
    return payload


def error_payload(code: str, message: str) -> dict[str, Any]:
    payload = {
        "schema": CONTROL_SCHEMA,
        "status": "error",
        "code": code,
        "mode": "agent",
        "execute": False,
        "message": message,
        "execution_status": "failed",
        "steps": [],
    }
    payload["markdown"] = format_control_markdown(payload)
    return payload


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "schema",
        "status",
        "code",
        "message",
        "execution_status",
        "overall_execution_status",
        "completed_count",
        "failed_count",
        "degraded_count",
        "dry_run",
        "eligible_for_materialization",
    ]
    return {key: result.get(key) for key in keys if key in result}


def step_status_from_result(result: dict[str, Any]) -> str:
    if result.get("status") == "degraded" or result.get("code") == "DEGRADED_LEDGER_PENDING":
        return "degraded"
    if result.get("status") in {"error", "not_ready"}:
        return "failed"
    execution_status = result.get("overall_execution_status") or result.get("execution_status")
    if execution_status in {"blocked", "failed", "partial"}:
        return str(execution_status)
    return "completed" if result.get("status") == "ok" else str(result.get("status") or "failed")


def inject_task_graph_action(
    plan: dict[str, Any],
    proposal: dict[str, Any],
) -> dict[str, Any]:
    if not proposal.get("eligible_for_materialization"):
        return plan
    action = {
        "action_type": "materialize_task_graph",
        "priority": 45,
        "severity": "info",
        "reason": "Run only has the default planning placeholder; materialize a task graph first.",
        "suggested_cli_command": proposal.get("suggested_apply_command"),
        "related_ids": {"run_id": proposal.get("run_id")},
        "run_id": proposal.get("run_id"),
        "task_id": (proposal.get("planning_placeholder_task") or {}).get("task_id"),
        "blocks_execution": False,
        "task_graph_proposal": proposal,
    }
    updated = {**plan}
    actions = [
        item
        for item in updated.get("recommended_actions") or []
        if item.get("action_type") != "materialize_task_graph"
    ]
    actions.append(action)
    updated["recommended_actions"] = sorted(
        actions,
        key=lambda item: (
            int(item.get("priority") or 0),
            str(item.get("run_id") or ""),
            str(item.get("action_type") or ""),
        ),
    )
    updated["runnable_dispatch_commands"] = [
        item.get("suggested_cli_command")
        for item in updated["recommended_actions"]
        if item.get("action_type") in {"dispatch_claim", "dispatch_spawn"}
        and item.get("suggested_cli_command")
        and not item.get("blocks_execution")
    ]
    summary = dict(updated.get("summary") or {})
    summary["task_graph_action_count"] = sum(
        1
        for item in updated["recommended_actions"]
        if item.get("action_type") == "materialize_task_graph"
    )
    updated["summary"] = summary
    return updated


def readiness_from_plan(plan: dict[str, Any]) -> str | None:
    runs = plan.get("active_runs") or []
    if len(runs) == 1:
        return runs[0].get("readiness_status")
    run_plans = plan.get("run_plans") or []
    if len(run_plans) == 1:
        return (run_plans[0].get("run") or {}).get("readiness_status")
    return None


def infer_single_run_id(plan: dict[str, Any]) -> str | None:
    runs = plan.get("active_runs") or []
    if len(runs) == 1:
        return runs[0].get("run_id")
    run_plans = plan.get("run_plans") or []
    if len(run_plans) == 1:
        return (run_plans[0].get("run") or {}).get("run_id")
    return plan.get("run_id")


def new_control_id(
    *,
    run_id: str,
    mode: str,
    execute: bool,
    seed: dict[str, Any],
) -> str:
    raw = json.dumps(
        {"run_id": run_id, "mode": mode, "execute": execute, "seed": seed},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    if not execute:
        return f"{mode}-{suffix}"
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{mode}-{timestamp}-{suffix}"


def write_control_bundle(
    payload: dict[str, Any],
    *,
    base_dir: Path,
    control_dir: Path | None = None,
) -> dict[str, str]:
    run_id = payload.get("run_id") or "workspace"
    target_dir = control_dir or base_dir / str(run_id) / "control" / str(payload["control_id"])
    target_dir.mkdir(parents=True, exist_ok=True)
    plan_path = target_dir / "CONTROL_PLAN.json"
    trace_path = target_dir / "CONTROL_TRACE.jsonl"
    summary_path = target_dir / "SUMMARY.md"
    serializable = {key: value for key, value in payload.items() if key not in {"bundle"}}
    plan_path.write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    trace_lines = [
        json.dumps(step, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        for step in payload.get("steps") or []
    ]
    trace_path.write_text("".join(trace_lines), encoding="utf-8")
    summary_path.write_text(payload.get("markdown", ""), encoding="utf-8")
    return {
        "control_dir": str(target_dir),
        "control_plan_path": str(plan_path),
        "trace_path": str(trace_path),
        "summary_path": str(summary_path),
    }


def append_trace_step(control_dir: Path, step: dict[str, Any]) -> None:
    control_dir.mkdir(parents=True, exist_ok=True)
    trace_path = control_dir / "CONTROL_TRACE.jsonl"
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(step, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def format_control_markdown(payload: dict[str, Any]) -> str:
    title = (
        "Geond Orchestrator Agent Mode"
        if payload.get("mode") == "agent"
        else "Geond Orchestrator Plan Mode"
    )
    lines = [
        f"# {title}",
        "",
        f"- Control: `{payload.get('control_id')}`",
        f"- Mode: `{payload.get('mode')}`",
        f"- Execute: `{payload.get('execute')}`",
        f"- Status: `{payload.get('execution_status')}`",
        f"- Next action: `{payload.get('next_action') or 'none'}`",
    ]
    if payload.get("delegated_command"):
        lines.extend(["", "## Delegated Command", f"- `{payload['delegated_command']}`"])
    if payload.get("selected_action"):
        action = payload["selected_action"]
        lines.extend(
            [
                "",
                "## Selected Action",
                f"- Type: `{action.get('action_type')}`",
                f"- Severity: `{action.get('severity')}`",
                f"- Reason: {action.get('reason')}",
            ]
        )
    lines.extend(["", "## Steps"])
    lines.extend(
        markdown_list(
            (
                f"{step.get('step_index')} {step.get('action_type') or 'none'}: "
                f"{step.get('step_status')}"
            )
            for step in payload.get("steps") or []
        )
    )
    plan = payload.get("plan") or {}
    summary = plan.get("summary") or {}
    if summary:
        lines.extend(
            [
                "",
                "## Plan Summary",
                f"- Runs: `{summary.get('run_count', 0)}`",
                f"- Blocking actions: `{summary.get('blocking_action_count', 0)}`",
                f"- Dispatch actions: `{summary.get('dispatch_action_count', 0)}`",
            ]
        )
    if payload.get("bundle"):
        lines.extend(["", "## Bundle", f"- `{payload['bundle'].get('control_dir')}`"])
    return "\n".join(lines).rstrip() + "\n"


def markdown_list(items: Any) -> list[str]:
    values = list(items)
    return [f"- {value}" for value in values] if values else ["- none"]
