from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from psycopg import Connection

from geond import orchestrator_spawn
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
    leases = package.get("leases") or []
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
        "active_leases": [
            lease
            for lease in leases
            if lease.get("status") in {"active", "claimed", "executing"}
            and lease.get("released_at") is None
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


def dispatch_spawn(
    conn: Connection,
    *,
    run_id: str,
    agent_name: str = "codex",
    execute: bool = False,
    task_id: str | None = None,
    model: str | None = None,
    sandbox: str = "workspace-write",
    timeout_seconds: int = 3600,
    write_bundle: bool = False,
    manifest_base_dir: Path = DEFAULT_MANIFEST_BASE_DIR,
    codex_runner: Any | None = None,
) -> dict[str, Any]:
    status_payload = get_status(
        conn,
        run_id,
        agent_name=agent_name,
        manifest_base_dir=manifest_base_dir,
    )
    if status_payload.get("status") != "ok":
        return status_payload
    run_summary = orchestration_store.summarize_run(conn, run_id)
    base_payload = spawn_base_payload(status_payload, run_summary, agent_name)

    if agent_name != orchestrator_spawn.CODEX_AGENT_NAME:
        return finish_spawn_payload(
            base_payload,
            status="error",
            code="UNSUPPORTED_AGENT",
            execution_status="failed",
            message="Spawn mode currently supports only the codex agent.",
        )

    selection = select_spawn_task(status_payload, task_id)
    if selection.get("status") != "ok":
        return finish_spawn_payload(
            base_payload,
            status="error",
            code=selection.get("code"),
            execution_status="blocked",
            message=selection.get("message"),
            selected_task=selection.get("selected_task"),
        )
    selected_task = selection["selected_task"]

    root_uri = orchestrator_spawn.get_workspace_root_uri(
        conn,
        status_payload["run"]["workspace_id"],
    )
    if not root_uri:
        return finish_spawn_payload(
            base_payload,
            status="error",
            code="WORKSPACE_NOT_FOUND",
            execution_status="failed",
            message="Run workspace was not found.",
            selected_task=selected_task,
        )
    workspace = orchestrator_spawn.resolve_local_workspace_path(root_uri)
    if workspace.get("status") != "ok":
        return finish_spawn_payload(
            base_payload,
            status="error",
            code=workspace.get("code"),
            execution_status="failed",
            message=workspace.get("message"),
            selected_task=selected_task,
            workspace=workspace,
        )

    codex_bin = orchestrator_spawn.find_codex_binary()
    if not codex_bin:
        return finish_spawn_payload(
            base_payload,
            status="error",
            code="CODEX_CLI_NOT_FOUND",
            execution_status="failed",
            message="Codex CLI was not found. Set GEOND_CODEX_BIN or add codex to PATH.",
            selected_task=selected_task,
            workspace=workspace,
        )

    invocation = orchestrator_spawn.new_invocation(run_id, manifest_base_dir)
    prompt = orchestrator_spawn.build_worker_prompt(
        status_payload=status_payload,
        run_summary=run_summary,
        selected_task=selected_task,
        workspace_path=workspace["workspace_path"],
    )
    command = orchestrator_spawn.build_codex_command(
        codex_bin=codex_bin,
        workspace_path=workspace["workspace_path"],
        invocation=invocation,
        model=model,
        sandbox=sandbox,
    )
    bundle = spawn_invocation_payload(invocation, command, prompt, model, sandbox, timeout_seconds)
    if write_bundle or execute:
        bundle.update(orchestrator_spawn.write_prompt_bundle(invocation, prompt))

    if not execute:
        return finish_spawn_payload(
            base_payload,
            status="ok",
            code=None,
            execution_status="preview",
            selected_task=selected_task,
            workspace=workspace,
            invocation=bundle,
            worker_prompt=prompt,
            expected_output_schema=orchestrator_spawn.output_schema(),
        )

    register = orchestration_store.register_worker_session(
        conn,
        run_id,
        agent_name,
        session_external_id=invocation.invocation_id,
        metadata={
            "launch_mode": "spawned",
            "agent": agent_name,
            "invocation_id": invocation.invocation_id,
            "prompt_path": str(invocation.prompt_path),
            "output_dir": str(invocation.output_dir),
        },
        idempotency_key=f"geond-orchestrator:{invocation.invocation_id}:register",
    )
    if register.get("status") != "ok":
        return finish_spawn_payload(
            base_payload,
            status="error",
            code=register.get("code"),
            execution_status="failed",
            message=register.get("message"),
            selected_task=selected_task,
            workspace=workspace,
            invocation=bundle,
            worker_session=register.get("worker_session"),
            storage_result=register,
        )
    worker_session = register["worker_session"]

    claim = orchestration_store.claim_task(
        conn,
        selected_task["task_id"],
        agent_name,
        worker_session_id=worker_session["worker_session_id"],
        metadata={"launch_mode": "spawned", "invocation_id": invocation.invocation_id},
        idempotency_key=f"geond-orchestrator:{invocation.invocation_id}:claim",
    )
    if claim.get("status") != "ok":
        return finish_spawn_payload(
            base_payload,
            status="error",
            code=claim.get("code"),
            execution_status="failed",
            message=claim.get("message"),
            selected_task=selected_task,
            workspace=workspace,
            invocation=bundle,
            worker_session=worker_session,
            claim_result=claim,
        )

    runner = codex_runner or orchestrator_spawn.run_codex
    run_result = runner(
        command=command,
        prompt=prompt,
        invocation=invocation,
        timeout_seconds=timeout_seconds,
    )
    bundle.update(
        {
            "exit_code": run_result.exit_code,
            "timed_out": run_result.timed_out,
            "events_path": str(invocation.events_path),
            "stderr_path": str(invocation.stderr_path),
            "last_message_path": str(invocation.last_message_path),
            "result_path": str(invocation.result_path),
        }
    )

    if run_result.timed_out or run_result.exit_code != 0:
        code = "CODEX_TIMEOUT" if run_result.timed_out else "CODEX_RUN_FAILED"
        return finish_blocked_spawn(
            conn,
            base_payload=base_payload,
            code=code,
            message="Codex did not complete successfully.",
            selected_task=selected_task,
            workspace=workspace,
            invocation=bundle,
            worker_session=worker_session,
            claim_result=claim,
            lease_id=claim["lease"]["lease_id"],
            summary=(
                "Codex spawn failed before returning a valid task result. "
                f"See {invocation.output_dir}."
            ),
            blocked_on=[code],
            next_action="Inspect spawn logs and retry or claim the task manually.",
        )

    parsed = orchestrator_spawn.parse_worker_result(invocation)
    if parsed.get("status") != "ok":
        return finish_blocked_spawn(
            conn,
            base_payload=base_payload,
            code=parsed.get("code"),
            message=parsed.get("message"),
            selected_task=selected_task,
            workspace=workspace,
            invocation=bundle,
            worker_session=worker_session,
            claim_result=claim,
            lease_id=claim["lease"]["lease_id"],
            summary=f"Codex spawn returned an invalid result. See {invocation.output_dir}.",
            blocked_on=[str(parsed.get("code") or "WORKER_RESULT_INVALID")],
            next_action="Inspect LAST_MESSAGE.json and retry or finish the task manually.",
            worker_result_error=parsed,
        )

    worker_result = parsed["result"]
    evidence_results = record_spawn_evidence(
        conn,
        run_id=run_id,
        task_id=selected_task["task_id"],
        worker_session_id=worker_session["worker_session_id"],
        invocation_id=invocation.invocation_id,
        log_path=str(invocation.events_path),
        worker_result=worker_result,
    )
    finish = orchestration_store.finish_task_with_handoff(
        conn,
        claim["lease"]["lease_id"],
        summary=worker_result["summary"],
        task_status=worker_result["task_status"],
        tested_commands=[
            item["command"] for item in orchestrator_spawn.normalized_tested_commands(worker_result)
        ],
        remaining_risks=[str(item) for item in worker_result.get("risks") or []],
        next_action=worker_result.get("next_action") or None,
        worker_session_id=worker_session["worker_session_id"],
        idempotency_key=f"geond-orchestrator:{invocation.invocation_id}:finish",
    )
    if finish.get("status") != "ok":
        return finish_spawn_payload(
            base_payload,
            status="error",
            code=finish.get("code"),
            execution_status="failed",
            message=finish.get("message"),
            selected_task=selected_task,
            workspace=workspace,
            invocation=bundle,
            worker_session=worker_session,
            claim_result=claim,
            evidence_results=evidence_results,
            handoff_result=finish,
            worker_result=worker_result,
        )
    execution_status = "completed" if worker_result["task_status"] == "done" else "blocked"
    return finish_spawn_payload(
        base_payload,
        status="ok",
        code=None,
        execution_status=execution_status,
        selected_task=selected_task,
        workspace=workspace,
        invocation=bundle,
        worker_session=worker_session,
        claim_result=claim,
        evidence_results=evidence_results,
        handoff_result=finish,
        worker_result=worker_result,
    )


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


def spawn_base_payload(
    status_payload: dict[str, Any],
    run_summary: dict[str, Any],
    agent_name: str,
) -> dict[str, Any]:
    return {
        "schema": ORCHESTRATOR_DISPATCH_SCHEMA,
        "dispatch_mode": "spawn",
        "agent_name": agent_name,
        "orchestrator_status": status_payload,
        "run_summary": run_summary,
        "selected_task": None,
        "workspace": None,
        "invocation": None,
        "worker_session": None,
        "claim_result": None,
        "handoff_result": None,
        "evidence_results": [],
        "worker_result": None,
    }


def finish_spawn_payload(
    base_payload: dict[str, Any],
    *,
    status: str,
    code: str | None,
    execution_status: str,
    message: str | None = None,
    selected_task: dict[str, Any] | None = None,
    workspace: dict[str, Any] | None = None,
    invocation: dict[str, Any] | None = None,
    worker_prompt: str | None = None,
    expected_output_schema: dict[str, Any] | None = None,
    worker_session: dict[str, Any] | None = None,
    claim_result: dict[str, Any] | None = None,
    evidence_results: list[dict[str, Any]] | None = None,
    handoff_result: dict[str, Any] | None = None,
    worker_result: dict[str, Any] | None = None,
    worker_result_error: dict[str, Any] | None = None,
    storage_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        **base_payload,
        "status": status,
        "code": code,
        "execution_status": execution_status,
        "message": message,
        "selected_task": selected_task,
        "workspace": workspace,
        "invocation": invocation,
        "worker_prompt": worker_prompt,
        "expected_output_schema": expected_output_schema,
        "worker_session": worker_session,
        "claim_result": claim_result,
        "evidence_results": evidence_results if evidence_results is not None else [],
        "handoff_result": handoff_result,
        "worker_result": worker_result,
        "worker_result_error": worker_result_error,
        "storage_result": storage_result,
    }
    payload["markdown"] = format_dispatch_markdown(payload)
    return payload


def finish_blocked_spawn(
    conn: Connection,
    *,
    base_payload: dict[str, Any],
    code: str | None,
    message: str | None,
    selected_task: dict[str, Any],
    workspace: dict[str, Any],
    invocation: dict[str, Any],
    worker_session: dict[str, Any],
    claim_result: dict[str, Any],
    lease_id: str,
    summary: str,
    blocked_on: list[str],
    next_action: str,
    worker_result_error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    handoff = orchestration_store.finish_task_with_handoff(
        conn,
        lease_id,
        summary=summary,
        task_status="blocked",
        tested_commands=[],
        remaining_risks=blocked_on,
        next_action=next_action,
        blocked_on=blocked_on,
        worker_session_id=worker_session["worker_session_id"],
        idempotency_key=f"geond-orchestrator:{invocation['invocation_id']}:blocked",
    )
    status = "ok" if handoff.get("status") == "ok" else "error"
    return finish_spawn_payload(
        base_payload,
        status=status,
        code=code,
        execution_status="blocked" if status == "ok" else "failed",
        message=message,
        selected_task=selected_task,
        workspace=workspace,
        invocation=invocation,
        worker_session=worker_session,
        claim_result=claim_result,
        handoff_result=handoff,
        worker_result_error=worker_result_error,
    )


def select_spawn_task(
    status_payload: dict[str, Any],
    task_id: str | None,
) -> dict[str, Any]:
    claimable_tasks = status_payload.get("claimable_tasks") or []
    if task_id:
        for task in claimable_tasks:
            if task.get("task_id") == task_id:
                return {"status": "ok", "code": None, "selected_task": task}
        for lease in status_payload.get("active_leases") or []:
            if lease.get("task_id") == task_id:
                return {
                    "status": "error",
                    "code": "LEASE_CONFLICT",
                    "message": "Requested task already has an active lease.",
                    "selected_task": None,
                    "lease": lease,
                }
        return {
            "status": "error",
            "code": "TASK_NOT_CLAIMABLE",
            "message": "Requested task is not currently claimable.",
            "selected_task": None,
        }
    if not claimable_tasks:
        return {
            "status": "error",
            "code": "TASK_NOT_CLAIMABLE",
            "message": "No claimable task is available for spawn mode.",
            "selected_task": None,
        }
    return {"status": "ok", "code": None, "selected_task": claimable_tasks[0]}


def spawn_invocation_payload(
    invocation: orchestrator_spawn.SpawnInvocation,
    command: list[str],
    prompt: str,
    model: str | None,
    sandbox: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    return {
        "invocation_id": invocation.invocation_id,
        "output_dir": str(invocation.output_dir),
        "prompt_path": str(invocation.prompt_path),
        "output_schema_path": str(invocation.output_schema_path),
        "events_path": str(invocation.events_path),
        "stderr_path": str(invocation.stderr_path),
        "last_message_path": str(invocation.last_message_path),
        "result_path": str(invocation.result_path),
        "command": command,
        "display_command": shlex.join(command),
        "prompt_preview": prompt[:4000],
        "model": model,
        "sandbox": sandbox,
        "timeout_seconds": timeout_seconds,
    }


def record_spawn_evidence(
    conn: Connection,
    *,
    run_id: str,
    task_id: str,
    worker_session_id: str,
    invocation_id: str,
    log_path: str,
    worker_result: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence_results: list[dict[str, Any]] = []
    for index, item in enumerate(orchestrator_spawn.normalized_tested_commands(worker_result)):
        result = orchestration_store.record_command_evidence(
            conn,
            run_id,
            item["command"],
            task_id=task_id,
            worker_session_id=worker_session_id,
            purpose=item["purpose"],
            status=item["status"],
            exit_code=item["exit_code"],
            stdout_summary=item["stdout_summary"],
            stderr_summary=item["stderr_summary"],
            log_path=log_path,
            metadata={"source": "geond-orchestrator-spawn", "invocation_id": invocation_id},
            idempotency_key=f"geond-orchestrator:{invocation_id}:evidence:{index}",
        )
        evidence_results.append(result)
    return evidence_results


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
    if payload.get("dispatch_mode") == "spawn":
        return format_spawn_dispatch_markdown(payload)
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


def format_spawn_dispatch_markdown(payload: dict[str, Any]) -> str:
    task = payload.get("selected_task") or {}
    invocation = payload.get("invocation") or {}
    workspace = payload.get("workspace") or {}
    lines = [
        "# Spawn-mode Dispatch",
        "",
        f"- Agent: `{payload.get('agent_name')}`",
        f"- Status: `{payload.get('execution_status')}`",
        f"- Code: `{payload.get('code')}`",
        f"- Task: `{task.get('title') or 'none'}` (`{task.get('task_id') or 'none'}`)",
        f"- Workspace: `{workspace.get('workspace_path') or workspace.get('root_uri') or 'none'}`",
        f"- Output: `{invocation.get('output_dir') or 'none'}`",
    ]
    if payload.get("message"):
        lines.extend(["", "## Message", "", str(payload["message"])])
    if invocation.get("display_command"):
        lines.extend(["", "## Codex Command"])
        lines.extend(markdown_list([f"`{invocation['display_command']}`"]))
    if payload.get("execution_status") == "preview" and payload.get("worker_prompt"):
        lines.extend(
            [
                "",
                "## Worker Prompt Preview",
                "",
                "```markdown",
                str(payload["worker_prompt"])[:2000],
                "```",
            ]
        )
    handoff = payload.get("handoff_result") or {}
    if handoff:
        lines.extend(["", "## Handoff"])
        lines.extend(markdown_list([f"{handoff.get('status')} `{handoff.get('handoff_id')}`"]))
    evidence = payload.get("evidence_results") or []
    if evidence:
        lines.extend(["", "## Evidence"])
        lines.extend(
            markdown_list(
                f"{item.get('status')} "
                f"`{(item.get('command_evidence') or {}).get('command_evidence_id')}`"
                for item in evidence
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
