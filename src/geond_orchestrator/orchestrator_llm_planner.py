from __future__ import annotations

import json
import shlex
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection

from geond_orchestrator import orchestrator, orchestrator_spawn, orchestrator_task_planner

LLM_PLANNER_SCHEMA = "geond.llm_task_graph_planner.v1"
PLANNER_EVENTS_NAME = "PLANNER_EVENTS.jsonl"
PLANNER_STDERR_NAME = "PLANNER_STDERR.txt"


@dataclass(frozen=True)
class PlannerInvocation:
    invocation_id: str
    output_dir: Path
    prompt_path: Path
    output_schema_path: Path
    events_path: Path
    stderr_path: Path
    last_message_path: Path
    result_path: Path


@dataclass(frozen=True)
class PlannerRunResult:
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    command: list[str]


PlannerRunner = Callable[
    [list[str], str, PlannerInvocation, int],
    PlannerRunResult,
]


def propose_task_graph_with_llm(
    conn: Connection,
    run_id: str,
    *,
    agent_name: str = orchestrator_spawn.CODEX_AGENT_NAME,
    execute_planner: bool = False,
    status_payload: dict[str, Any] | None = None,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    model: str | None = None,
    sandbox: str = "workspace-write",
    timeout_seconds: int = 3600,
    runner: PlannerRunner | None = None,
) -> dict[str, Any]:
    agent_name = normalize_agent_name(agent_name)
    if agent_name not in orchestrator_spawn.SUPPORTED_SPAWN_AGENTS:
        return error_result("UNSUPPORTED_AGENT", f"Unsupported planner agent: {agent_name}")

    status_payload = status_payload or orchestrator.get_status(conn, run_id)
    if status_payload.get("status") != "ok":
        return status_payload
    workspace = resolve_workspace(conn, status_payload)
    if workspace.get("status") != "ok":
        return error_result(
            str(workspace.get("code") or "WORKSPACE_NOT_LOCAL"),
            str(workspace.get("message") or "Workspace could not be resolved."),
            details=workspace,
        )

    invocation = new_invocation(run_id, base_dir)
    prompt = build_planner_prompt(
        status_payload=status_payload,
        workspace_path=str(workspace["workspace_path"]),
        agent_name=agent_name,
    )
    preview_payload = planner_payload(
        run_id=run_id,
        agent_name=agent_name,
        execute_planner=execute_planner,
        execution_status="preview",
        invocation=invocation,
        prompt=prompt,
        status_payload=status_payload,
    )
    if not execute_planner:
        preview_payload["markdown"] = format_planner_markdown(preview_payload)
        return preview_payload

    agent_bin = orchestrator_spawn.find_agent_binary(agent_name)
    if not agent_bin:
        return error_result(
            orchestrator_spawn.missing_binary_code(agent_name),
            f"{agent_name} CLI was not found.",
            run_id=run_id,
            planner_agent=agent_name,
        )

    write_planner_bundle(invocation, prompt)
    command = orchestrator_spawn.build_agent_command(
        agent_name=agent_name,
        agent_bin=agent_bin,
        workspace_path=str(workspace["workspace_path"]),
        invocation=invocation,  # type: ignore[arg-type]
        model=model,
        sandbox=sandbox,
    )
    run_result = (runner or run_planner_process)(
        command,
        prompt,
        invocation,
        timeout_seconds,
    )
    append_event(
        invocation,
        {
            "event_type": "planner_process_completed",
            "agent": agent_name,
            "status": "timeout" if run_result.timed_out else "completed",
            "exit_code": run_result.exit_code,
            "timed_out": run_result.timed_out,
            "command": run_result.command,
        },
    )
    if run_result.timed_out:
        return write_and_return_result(
            invocation,
            error_result(
                "PLANNER_TIMEOUT",
                "LLM task graph planner timed out.",
                run_id=run_id,
                planner_agent=agent_name,
            ),
        )
    if run_result.exit_code not in {0, None}:
        return write_and_return_result(
            invocation,
            error_result(
                "PLANNER_PROCESS_FAILED",
                "LLM task graph planner exited with a nonzero status.",
                run_id=run_id,
                planner_agent=agent_name,
                details={"exit_code": run_result.exit_code},
            ),
        )

    parsed = parse_planner_result(invocation)
    if parsed.get("status") != "ok":
        parsed.update({"run_id": run_id, "planner_agent": agent_name})
        return write_and_return_result(invocation, parsed)

    proposal = normalize_proposal(
        conn,
        run_id,
        parsed["result"],
        status_payload=status_payload,
        planner_agent=agent_name,
        invocation=invocation,
    )
    if proposal.get("status") != "ok":
        return write_and_return_result(invocation, proposal)

    payload = planner_payload(
        run_id=run_id,
        agent_name=agent_name,
        execute_planner=True,
        execution_status="completed",
        invocation=invocation,
        prompt=prompt,
        status_payload=status_payload,
        command=command,
        task_graph_proposal=proposal,
        status="ok",
    )
    payload["markdown"] = format_planner_markdown(payload)
    return write_and_return_result(invocation, payload)


def normalize_agent_name(agent_name: str | None) -> str:
    return (agent_name or orchestrator_spawn.CODEX_AGENT_NAME).strip().lower()


def new_invocation(run_id: str, base_dir: Path) -> PlannerInvocation:
    invocation_id = str(uuid.uuid4())
    output_dir = base_dir / run_id / "planner" / invocation_id
    return PlannerInvocation(
        invocation_id=invocation_id,
        output_dir=output_dir,
        prompt_path=output_dir / orchestrator_spawn.PROMPT_NAME,
        output_schema_path=output_dir / orchestrator_spawn.OUTPUT_SCHEMA_NAME,
        events_path=output_dir / PLANNER_EVENTS_NAME,
        stderr_path=output_dir / PLANNER_STDERR_NAME,
        last_message_path=output_dir / orchestrator_spawn.LAST_MESSAGE_NAME,
        result_path=output_dir / orchestrator_spawn.RESULT_NAME,
    )


def resolve_workspace(conn: Connection, status_payload: dict[str, Any]) -> dict[str, Any]:
    run = status_payload.get("run") or {}
    workspace_id = run.get("workspace_id")
    if not workspace_id:
        return {
            "status": "error",
            "code": "WORKSPACE_NOT_FOUND",
            "message": "Run status did not include a workspace id.",
        }
    root_uri = orchestrator_spawn.get_workspace_root_uri(conn, str(workspace_id))
    if not root_uri:
        return {
            "status": "error",
            "code": "WORKSPACE_NOT_FOUND",
            "message": "Run workspace could not be found.",
        }
    return orchestrator_spawn.resolve_local_workspace_path(root_uri)


def output_schema() -> dict[str, Any]:
    task_shape = {
        "type": "object",
        "additionalProperties": True,
        "required": ["key", "title"],
        "properties": {
            "key": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "priority": {"type": "integer"},
            "depends_on": {"type": "array", "items": {"type": "string"}},
            "required_evidence": {"type": "array"},
            "status": {"type": "string"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["tasks"],
        "properties": {
            "tasks": {"type": "array", "items": task_shape},
            "template": {"type": "string"},
            "summary": {"type": "string"},
            "rationale": {"type": "string"},
        },
    }


def build_planner_prompt(
    *,
    status_payload: dict[str, Any],
    workspace_path: str,
    agent_name: str,
) -> str:
    run = status_payload.get("run") or {}
    readiness = status_payload.get("readiness") or {}
    graph = status_payload.get("task_graph") or {}
    context = {
        "run": run,
        "goal_or_title": run.get("title"),
        "workspace_path": workspace_path,
        "readiness": readiness,
        "blocking_reasons": readiness.get("blocking_reasons") or [],
        "claimable_tasks": status_payload.get("claimable_tasks") or [],
        "current_tasks": graph.get("tasks") or status_payload.get("tasks") or [],
        "task_edges": graph.get("edges") or [],
        "open_findings": status_payload.get("open_findings") or [],
        "pending_approvals": status_payload.get("pending_approvals") or [],
        "latest_decisions": status_payload.get("latest_decisions") or [],
    }
    return (
        "# Geond Task Graph Planner\n\n"
        f"You are a spawned {agent_name} planner controlled by Geond Orchestrator.\n"
        "Create a task graph proposal only. Do not edit files, run commands, commit, or push.\n"
        "Use the repository path only as context for planning.\n"
        "Return final output as JSON only, with no Markdown wrapper.\n"
        "The proposal must be compatible with geond.task_graph_proposal.v1 task input: "
        "key, title, description, priority, depends_on, required_evidence, status.\n\n"
        "## Context\n\n"
        f"```json\n{json.dumps(context, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
        "## Required Final JSON Shape\n\n"
        f"```json\n{json.dumps(output_schema(), ensure_ascii=False, indent=2)}\n```\n"
    )


def planner_payload(
    *,
    run_id: str,
    agent_name: str,
    execute_planner: bool,
    execution_status: str,
    invocation: PlannerInvocation,
    prompt: str,
    status_payload: dict[str, Any],
    command: list[str] | None = None,
    task_graph_proposal: dict[str, Any] | None = None,
    status: str = "preview",
) -> dict[str, Any]:
    return {
        "schema": LLM_PLANNER_SCHEMA,
        "status": status,
        "code": None,
        "run_id": run_id,
        "planner": "llm",
        "planner_agent": agent_name,
        "execute_planner": execute_planner,
        "execution_status": execution_status,
        "invocation": {
            "invocation_id": invocation.invocation_id,
            "output_dir": str(invocation.output_dir) if execute_planner else None,
            "prompt_path": str(invocation.prompt_path) if execute_planner else None,
            "output_schema_path": str(invocation.output_schema_path) if execute_planner else None,
            "events_path": str(invocation.events_path) if execute_planner else None,
            "result_path": str(invocation.result_path) if execute_planner else None,
        },
        "delegated_command": shlex.join(command) if command else None,
        "prompt_preview": prompt[:1200],
        "output_schema": output_schema(),
        "task_graph_proposal": task_graph_proposal,
        "proposal_id": (task_graph_proposal or {}).get("proposal_id"),
        "run_title": (status_payload.get("run") or {}).get("title"),
    }


def write_planner_bundle(invocation: PlannerInvocation, prompt: str) -> None:
    invocation.output_dir.mkdir(parents=True, exist_ok=True)
    invocation.prompt_path.write_text(prompt, encoding="utf-8")
    invocation.output_schema_path.write_text(
        json.dumps(output_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    append_event(
        invocation,
        {
            "event_type": "planner_invocation_created",
            "status": "created",
        },
    )


def run_planner_process(
    command: list[str],
    prompt: str,
    invocation: PlannerInvocation,
    timeout_seconds: int,
) -> PlannerRunResult:
    invocation.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        exit_code: int | None = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = orchestrator_spawn.normalize_process_text(exc.stdout)
        stderr = orchestrator_spawn.normalize_process_text(exc.stderr)
        exit_code = None
        timed_out = True

    invocation.stderr_path.write_text(stderr, encoding="utf-8")
    if stdout.strip() and not invocation.last_message_path.exists():
        invocation.last_message_path.write_text(stdout.strip(), encoding="utf-8")
    return PlannerRunResult(
        exit_code=exit_code,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        command=command,
    )


def append_event(invocation: PlannerInvocation, event: dict[str, Any]) -> None:
    invocation.output_dir.mkdir(parents=True, exist_ok=True)
    with invocation.events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def parse_planner_result(invocation: PlannerInvocation) -> dict[str, Any]:
    if not invocation.last_message_path.exists():
        return error_result(
            "PLANNER_RESULT_MISSING",
            "LLM planner did not produce a final JSON message.",
        )
    raw_text = invocation.last_message_path.read_text(encoding="utf-8").strip()
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return error_result(
            "PLANNER_RESULT_INVALID_JSON",
            str(exc),
            details={"raw_text": raw_text[:4000]},
        )
    unwrapped = orchestrator_spawn.unwrap_worker_result(payload)
    if not isinstance(unwrapped, dict):
        return error_result("PLANNER_RESULT_INVALID", "Planner result must be an object.")
    return {"status": "ok", "code": None, "result": unwrapped}


def normalize_proposal(
    conn: Connection,
    run_id: str,
    result: dict[str, Any],
    *,
    status_payload: dict[str, Any],
    planner_agent: str,
    invocation: PlannerInvocation,
) -> dict[str, Any]:
    proposal = result if result.get("schema") == orchestrator_task_planner.PROPOSAL_SCHEMA else {}
    tasks = proposal.get("tasks") or result.get("tasks") or []
    validation = orchestrator_task_planner.validate_task_graph_tasks(tasks)
    if validation.get("status") != "ok":
        return validation
    eligibility = orchestrator_task_planner.materialization_eligibility(
        conn,
        run_id,
        status_payload=status_payload,
    )
    payload = {
        "schema": orchestrator_task_planner.PROPOSAL_SCHEMA,
        "status": "ok",
        "code": None,
        "run_id": run_id,
        "planner": "llm",
        "planner_agent": planner_agent,
        "template": result.get("template") or proposal.get("template") or "llm",
        "requested_template": "llm",
        "eligible_for_materialization": eligibility["eligible"],
        "eligibility_reason": eligibility["reason"],
        "planning_placeholder_task": eligibility.get("planning_placeholder_task"),
        "tasks": validation["tasks"],
        "planner_invocation": {
            "invocation_id": invocation.invocation_id,
            "output_dir": str(invocation.output_dir),
            "prompt_path": str(invocation.prompt_path),
            "output_schema_path": str(invocation.output_schema_path),
            "events_path": str(invocation.events_path),
            "result_path": str(invocation.result_path),
        },
    }
    payload["proposal_id"] = orchestrator_task_planner.stable_proposal_id(payload)
    payload["suggested_apply_command"] = (
        f"geond-orchestrator agent {run_id} --execute "
        f"--planner llm --planner-agent {planner_agent} "
        "--allow-llm-planner --execute-planner --allow-task-graph-create"
    )
    payload["markdown"] = orchestrator_task_planner.format_proposal_markdown(payload)
    return payload


def write_and_return_result(
    invocation: PlannerInvocation,
    payload: dict[str, Any],
) -> dict[str, Any]:
    invocation.output_dir.mkdir(parents=True, exist_ok=True)
    invocation.result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return payload


def format_planner_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LLM Task Graph Planner",
        "",
        f"- Run: `{payload.get('run_id')}`",
        f"- Agent: `{payload.get('planner_agent')}`",
        f"- Execute planner: `{payload.get('execute_planner')}`",
        f"- Status: `{payload.get('execution_status')}`",
    ]
    if payload.get("proposal_id"):
        lines.append(f"- Proposal: `{payload.get('proposal_id')}`")
    if payload.get("delegated_command"):
        lines.extend(["", "## Delegated Command", f"- `{payload.get('delegated_command')}`"])
    proposal = payload.get("task_graph_proposal") or {}
    tasks = proposal.get("tasks") or []
    if tasks:
        lines.extend(["", "## Proposed Tasks"])
        lines.extend(
            orchestrator_task_planner.markdown_list(
                (
                    f"{item.get('key')} | {item.get('title')} | "
                    f"depends_on={','.join(item.get('depends_on') or [])}"
                )
                for item in tasks
            )
        )
    else:
        lines.extend(
            [
                "",
                "## Preview",
                "- Planner execution is disabled until `--execute-planner` is provided.",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def error_result(
    code: str,
    message: str,
    *,
    run_id: str | None = None,
    planner_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": LLM_PLANNER_SCHEMA,
        "status": "error",
        "code": code,
        "message": message,
        "run_id": run_id,
        "planner": "llm",
        "planner_agent": planner_agent,
        "details": details or {},
    }
    payload["markdown"] = format_planner_markdown(payload)
    return payload
