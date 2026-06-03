from __future__ import annotations

from pathlib import Path
from typing import Any

from psycopg import Connection

from geond.orchestration_manifest import write_run_manifest
from geond.storage import orchestration as orchestration_store

ORCHESTRATOR_STATUS_SCHEMA = "geond.orchestrator_status.v1"
ORCHESTRATOR_RUN_SCHEMA = "geond.orchestrator_run.v1"
ORCHESTRATOR_DISPATCH_SCHEMA = "geond.orchestrator_dispatch.v1"
ORCHESTRATOR_RESUME_SCHEMA = "geond.orchestrator_resume.v1"
ORCHESTRATOR_FINALIZE_SCHEMA = "geond.orchestrator_finalize.v1"
DEFAULT_MANIFEST_BASE_DIR = Path("tmp/geond-runs")


def start_run(
    conn: Connection,
    *,
    goal: str,
    workspace_id_or_uri: str,
    risk_level: str = "medium",
    created_by_agent: str = "geond-orchestrator",
) -> dict[str, Any]:
    goal_result = orchestration_store.create_goal(
        conn,
        workspace_id_or_uri,
        goal,
        summary="Created by Geond Orchestrator claim-mode MVP.",
        created_by_agent=created_by_agent,
    )
    if goal_result.get("status") != "ok":
        return goal_result
    run_result = orchestration_store.create_run(
        conn,
        workspace_id_or_uri,
        goal,
        goal_id=goal_result["goal"]["goal_id"],
        risk_level=risk_level,
        created_by_agent=created_by_agent,
    )
    if run_result.get("status") != "ok":
        return run_result
    task_result = orchestration_store.create_task(
        conn,
        run_result["run"]["run_id"],
        "Plan and dispatch claim-mode workers",
        description=(
            "Break down the goal, claim work from separate Codex/Claude sessions, "
            "and submit handoff/evidence through geond CLI."
        ),
        priority=100,
        created_by_agent=created_by_agent,
    )
    if task_result.get("status") != "ok":
        return task_result
    status_payload = get_status(conn, run_result["run"]["run_id"])
    result = {
        "schema": ORCHESTRATOR_RUN_SCHEMA,
        "status": "ok",
        "code": None,
        "goal": goal_result["goal"],
        "run": run_result["run"],
        "planning_task": task_result["task"],
        "orchestrator_status": status_payload,
    }
    result["markdown"] = format_start_markdown(result)
    return result


def get_status(
    conn: Connection,
    run_id: str,
    *,
    agent_name: str = "codex",
    manifest_base_dir: Path = DEFAULT_MANIFEST_BASE_DIR,
    limit: int = 100,
) -> dict[str, Any]:
    package = orchestration_store.get_run_handoff_package(conn, run_id, limit=limit)
    if package.get("status") != "ok":
        return package
    readiness = orchestration_store.get_readiness_report(conn, run_id)
    claimable = orchestration_store.get_claimable_tasks(conn, run_id=run_id, limit=limit)
    claimable_tasks = claimable.get("tasks", []) if claimable.get("status") == "ok" else []
    workers = package.get("workers") or []
    findings = package.get("review_findings") or []
    approvals = package.get("approval_requests") or []
    decisions = package.get("decisions") or []
    run = package["run"]
    status_payload = {
        "schema": ORCHESTRATOR_STATUS_SCHEMA,
        "status": "ok",
        "code": None,
        "run": run,
        "readiness": readiness,
        "claimable_tasks": claimable_tasks,
        "active_workers": [
            worker for worker in workers if worker.get("status") in {"registered", "active", "idle"}
        ],
        "open_findings": [finding for finding in findings if finding.get("status") == "open"],
        "pending_approvals": [
            approval for approval in approvals if approval.get("status") == "requested"
        ],
        "latest_decisions": decisions[:5],
        "next_worker_commands": build_claim_mode_commands(run_id, claimable_tasks, agent_name),
        "manifest_dir": str(manifest_base_dir / run_id),
        "next_action": next_action_for_status(readiness, claimable_tasks),
    }
    status_payload["markdown"] = format_status_markdown(status_payload)
    return status_payload


def dispatch_claim(
    conn: Connection,
    *,
    run_id: str,
    agent_name: str = "codex",
    manifest_base_dir: Path = DEFAULT_MANIFEST_BASE_DIR,
) -> dict[str, Any]:
    status_payload = get_status(
        conn,
        run_id,
        agent_name=agent_name,
        manifest_base_dir=manifest_base_dir,
    )
    if status_payload.get("status") != "ok":
        return status_payload
    result = {
        "schema": ORCHESTRATOR_DISPATCH_SCHEMA,
        "status": "ok",
        "code": None,
        "dispatch_mode": "claim",
        "agent_name": agent_name,
        "orchestrator_status": status_payload,
        "next_worker_commands": status_payload["next_worker_commands"],
    }
    result["markdown"] = format_dispatch_markdown(result)
    return result


def resume_run(
    conn: Connection,
    run_id: str,
    *,
    manifest_base_dir: Path = DEFAULT_MANIFEST_BASE_DIR,
) -> dict[str, Any]:
    status_payload = get_status(conn, run_id, manifest_base_dir=manifest_base_dir)
    if status_payload.get("status") != "ok":
        return status_payload
    summary = orchestration_store.summarize_run(conn, run_id)
    result = {
        "schema": ORCHESTRATOR_RESUME_SCHEMA,
        "status": "ok",
        "code": None,
        "orchestrator_status": status_payload,
        "run_summary": summary,
        "manifest_dir": status_payload["manifest_dir"],
    }
    result["markdown"] = format_resume_markdown(result)
    return result


def finalize_run(
    conn: Connection,
    run_id: str,
    *,
    write_manifest: bool = False,
    manifest_base_dir: Path = DEFAULT_MANIFEST_BASE_DIR,
) -> dict[str, Any]:
    status_payload = get_status(conn, run_id, manifest_base_dir=manifest_base_dir)
    if status_payload.get("status") != "ok":
        return status_payload
    summary = orchestration_store.summarize_run(conn, run_id)
    readiness_status = (status_payload.get("readiness") or {}).get("status")
    manifest = None
    result_status = "ok" if readiness_status == "ready" else "not_ready"
    code = None if readiness_status == "ready" else "RUN_NOT_READY"
    if write_manifest and readiness_status == "ready":
        package = orchestration_store.get_run_handoff_package(conn, run_id)
        manifest = write_run_manifest(
            package,
            summary.get("markdown", ""),
            base_dir=manifest_base_dir,
            write_result=True,
        )
    result = {
        "schema": ORCHESTRATOR_FINALIZE_SCHEMA,
        "status": result_status,
        "code": code,
        "orchestrator_status": status_payload,
        "run_summary": summary,
        "manifest": manifest,
    }
    result["markdown"] = format_finalize_markdown(result)
    return result


def build_claim_mode_commands(
    run_id: str,
    claimable_tasks: list[dict[str, Any]],
    agent_name: str,
) -> list[dict[str, str]]:
    commands = [
        {
            "label": "register worker",
            "command": f"geond worker register {run_id} --agent {agent_name}",
        }
    ]
    if not claimable_tasks:
        commands.append(
            {
                "label": "create task",
                "command": f'geond task create {run_id} --title "Describe next task"',
            }
        )
        return commands
    for task in claimable_tasks:
        task_id = task["task_id"]
        commands.extend(
            [
                {
                    "label": f"claim {task_id}",
                    "command": (
                        f"geond worker claim --task-id {task_id} --agent {agent_name} "
                        "--worker-session-id <worker_session_id>"
                    ),
                },
                {
                    "label": f"finish {task_id}",
                    "command": (
                        "geond worker finish <lease_id> --worker-session-id "
                        '<worker_session_id> --summary "<summary>" '
                        '--tested-command "<validation command>"'
                    ),
                },
                {
                    "label": f"evidence {task_id}",
                    "command": (
                        f"geond evidence command --run {run_id} --task {task_id} "
                        "--worker-session-id <worker_session_id> "
                        '--command "<validation command>" --exit-code 0'
                    ),
                },
            ]
        )
    return commands


def next_action_for_status(readiness: dict[str, Any], claimable_tasks: list[dict[str, Any]]) -> str:
    readiness_status = readiness.get("status")
    if readiness_status == "ready":
        return "finalize"
    if readiness_status == "needs_human_approval":
        return "resolve pending approval"
    if claimable_tasks:
        return "dispatch claim-mode worker"
    return "create or release a task before dispatch"


def format_start_markdown(payload: dict[str, Any]) -> str:
    run = payload["run"]
    task = payload["planning_task"]
    lines = [
        f"# {run['title']}",
        "",
        f"- Run: `{run['run_id']}`",
        f"- Planning task: `{task['task_id']}`",
        "",
        payload["orchestrator_status"]["markdown"].rstrip(),
    ]
    return "\n".join(lines).rstrip() + "\n"


def format_status_markdown(payload: dict[str, Any]) -> str:
    run = payload.get("run") or {}
    readiness = payload.get("readiness") or {}
    lines = [
        f"# {run.get('title') or 'Geond Orchestrator Status'}",
        "",
        f"- Run: `{run.get('run_id')}`",
        f"- Risk: `{run.get('risk_level')}`",
        f"- Readiness: `{readiness.get('status')}`",
        f"- Next action: `{payload.get('next_action')}`",
        f"- Manifest: `{payload.get('manifest_dir')}`",
        "",
        "## Blockers",
    ]
    lines.extend(markdown_list(readiness.get("blocking_reasons") or []))
    lines.extend(["", "## Claimable Tasks"])
    lines.extend(
        markdown_list(
            f"{task.get('title')} (`{task.get('task_id')}`)"
            for task in payload.get("claimable_tasks") or []
        )
    )
    lines.extend(["", "## Next Worker Commands"])
    lines.extend(
        markdown_list(
            f"{item.get('label')}: `{item.get('command')}`"
            for item in payload.get("next_worker_commands") or []
        )
    )
    lines.extend(["", "## Latest Decisions"])
    lines.extend(
        markdown_list(
            f"{decision.get('decision')} (`{decision.get('decision_id')}`)"
            for decision in payload.get("latest_decisions") or []
        )
    )
    return "\n".join(lines).rstrip() + "\n"


def format_dispatch_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Claim-mode Dispatch",
        "",
        f"- Agent: `{payload.get('agent_name')}`",
        "",
        "## Commands",
    ]
    lines.extend(
        markdown_list(
            f"{item.get('label')}: `{item.get('command')}`"
            for item in payload.get("next_worker_commands") or []
        )
    )
    return "\n".join(lines).rstrip() + "\n"


def format_resume_markdown(payload: dict[str, Any]) -> str:
    status_payload = payload.get("orchestrator_status") or {}
    summary = payload.get("run_summary") or {}
    lines = [
        "# Resume Geond Orchestrator Run",
        "",
        status_payload.get("markdown", "").rstrip(),
        "",
        "## Run Summary",
        "",
        summary.get("markdown", "").rstrip(),
    ]
    return "\n".join(line for line in lines if line is not None).rstrip() + "\n"


def format_finalize_markdown(payload: dict[str, Any]) -> str:
    status_payload = payload.get("orchestrator_status") or {}
    readiness = status_payload.get("readiness") or {}
    lines = [
        "# Finalize Geond Orchestrator Run",
        "",
        f"- Finalize status: `{payload.get('status')}`",
        f"- Readiness: `{readiness.get('status')}`",
    ]
    manifest = payload.get("manifest")
    if manifest:
        lines.append(f"- Manifest: `{manifest.get('run_dir')}`")
    if payload.get("status") != "ok":
        lines.extend(["", "## Blockers"])
        lines.extend(markdown_list(readiness.get("blocking_reasons") or []))
    return "\n".join(lines).rstrip() + "\n"


def markdown_list(items: Any) -> list[str]:
    values = list(items)
    return [f"- {value}" for value in values] if values else ["- none"]
