from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from psycopg import Connection

from geond import orchestrator_spawn
from geond.storage import orchestration as orchestration_store


def execute_git_finalize(
    conn: Connection,
    *,
    run_id: str,
    status_payload: dict[str, Any],
    run_summary: dict[str, Any],
    git_checkpoint: bool = False,
    commit: bool = False,
    paths: list[str] | None = None,
    stage_all: bool = False,
    commit_message: str | None = None,
    push: bool = False,
    remote: str = "origin",
    branch: str = "CURRENT",
    create_pr: bool = False,
    pr_title: str | None = None,
    pr_body_file: Path | None = None,
    dry_run: bool = False,
    command_runner: Any | None = None,
) -> dict[str, Any]:
    validation = validate_git_finalize_request(
        status_payload,
        commit=commit,
        paths=paths or [],
        stage_all=stage_all,
        push=push,
        create_pr=create_pr,
    )
    if validation.get("status") != "ok":
        return validation

    workspace = resolve_finalize_workspace(conn, status_payload)
    if workspace.get("status") != "ok":
        return workspace
    if create_pr and not dry_run and not find_gh_binary():
        return error_result("GH_CLI_NOT_FOUND", "GitHub CLI was not found.")

    plan = build_git_command_plan(
        run_id=run_id,
        run_summary=run_summary,
        git_checkpoint=git_checkpoint,
        commit=commit,
        paths=paths or [],
        stage_all=stage_all,
        commit_message=commit_message,
        push=push,
        remote=remote,
        branch=branch,
        create_pr=create_pr,
        pr_title=pr_title,
        pr_body_file=pr_body_file,
    )
    if dry_run:
        return {
            "schema": "geond.git_finalize.v1",
            "status": "ok",
            "code": None,
            "dry_run": True,
            "workspace": workspace,
            "planned_commands": [command_record(step) for step in plan],
            "command_results": [],
            "decision_result": None,
        }

    runner = command_runner or default_command_runner
    command_results: list[dict[str, Any]] = []
    for index, step in enumerate(plan):
        result = runner(
            step["command"],
            cwd=Path(workspace["workspace_path"]),
            timeout_seconds=step.get("timeout_seconds", 120),
        )
        result = normalize_command_result(result, step["command"])
        result["label"] = step["label"]
        command_results.append(result)
        evidence = record_finalize_command_evidence(conn, run_id, result, index)
        result["evidence_result"] = evidence
        if result.get("exit_code") != 0:
            return {
                "schema": "geond.git_finalize.v1",
                "status": "error",
                "code": "GIT_COMMAND_FAILED",
                "dry_run": False,
                "workspace": workspace,
                "planned_commands": [command_record(item) for item in plan],
                "command_results": command_results,
                "failed_command": result,
                "decision_result": None,
            }

    metadata = git_finalize_metadata(command_results, workspace, remote)
    decision = orchestration_store.record_decision(
        conn,
        run_id,
        "Finalize run with Git checkpoint",
        reason="Readiness was ready and requested Git/PR finalize commands completed.",
        decided_by="geond-orchestrator",
        metadata=metadata,
    )
    return {
        "schema": "geond.git_finalize.v1",
        "status": "ok",
        "code": None,
        "dry_run": False,
        "workspace": workspace,
        "planned_commands": [command_record(item) for item in plan],
        "command_results": command_results,
        "decision_result": decision,
        "commit_sha": metadata["git"].get("commit_sha"),
        "branch": metadata["git"].get("branch"),
        "pr_url": metadata["git"].get("pr_url"),
    }


def validate_git_finalize_request(
    status_payload: dict[str, Any],
    *,
    commit: bool,
    paths: list[str],
    stage_all: bool,
    push: bool,
    create_pr: bool,
) -> dict[str, Any]:
    readiness_status = (status_payload.get("readiness") or {}).get("status")
    ledger_pending = (status_payload.get("degraded_ledger") or {}).get("pending_count", 0)
    mutation_requested = commit or push or create_pr
    if mutation_requested and readiness_status != "ready":
        return error_result("RUN_NOT_READY", "Git mutation is blocked until readiness is ready.")
    if mutation_requested and ledger_pending:
        return error_result(
            "DEGRADED_LEDGER_PENDING",
            "Git mutation is blocked until degraded ledger events are reconciled.",
        )
    if commit and not stage_all and not paths:
        return error_result(
            "GIT_STAGE_TARGET_REQUIRED",
            "Commit requires at least one --path or --stage-all.",
        )
    if push and not commit:
        return error_result("PUSH_REQUIRES_COMMIT", "Push is only allowed after a commit.")
    if create_pr and not push:
        return error_result("PR_REQUIRES_PUSH", "PR creation is only allowed after push.")
    return {"status": "ok", "code": None}


def resolve_finalize_workspace(conn: Connection, status_payload: dict[str, Any]) -> dict[str, Any]:
    root_uri = orchestrator_spawn.get_workspace_root_uri(
        conn,
        status_payload["run"]["workspace_id"],
    )
    if not root_uri:
        return error_result("WORKSPACE_NOT_FOUND", "Run workspace was not found.")
    workspace = orchestrator_spawn.resolve_local_workspace_path(root_uri)
    if workspace.get("status") != "ok":
        return workspace
    return workspace


def build_git_command_plan(
    *,
    run_id: str,
    run_summary: dict[str, Any],
    git_checkpoint: bool,
    commit: bool,
    paths: list[str],
    stage_all: bool,
    commit_message: str | None,
    push: bool,
    remote: str,
    branch: str,
    create_pr: bool,
    pr_title: str | None,
    pr_body_file: Path | None,
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    if git_checkpoint or commit or push or create_pr:
        commands.extend(
            [
                {"label": "git status", "command": ["git", "status", "--short"]},
                {"label": "git head", "command": ["git", "rev-parse", "HEAD"]},
                {"label": "git branch", "command": ["git", "branch", "--show-current"]},
            ]
        )
    if commit:
        if stage_all:
            commands.append({"label": "git stage all", "command": ["git", "add", "-A"]})
        else:
            commands.append({"label": "git stage paths", "command": ["git", "add", "--", *paths]})
        message = commit_message or f"Finalize Geond Orchestrator run {run_id}"
        commands.append({"label": "git commit", "command": ["git", "commit", "-m", message]})
        commands.append({"label": "git commit sha", "command": ["git", "rev-parse", "HEAD"]})
    if push:
        target_branch = branch if branch and branch != "CURRENT" else "HEAD"
        commands.append({"label": "git push", "command": ["git", "push", remote, target_branch]})
    if create_pr:
        body = pr_body_text(run_summary, pr_body_file)
        title = pr_title or f"Finalize Geond Orchestrator run {run_id}"
        commands.append(
            {
                "label": "gh pr create",
                "command": ["gh", "pr", "create", "--title", title, "--body", body],
            }
        )
    return commands


def default_command_runner(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "command": command,
            "exit_code": completed.returncode,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "exit_code": None,
            "stdout": normalize_process_text(exc.stdout),
            "stderr": normalize_process_text(exc.stderr),
            "timed_out": True,
        }


def normalize_command_result(result: Any, command: list[str]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {
            "command": command,
            "exit_code": 1,
            "stdout": "",
            "stderr": str(result),
            "timed_out": False,
        }
    return {
        "command": result.get("command") or command,
        "exit_code": result.get("exit_code"),
        "stdout": str(result.get("stdout") or ""),
        "stderr": str(result.get("stderr") or ""),
        "timed_out": bool(result.get("timed_out")),
    }


def record_finalize_command_evidence(
    conn: Connection,
    run_id: str,
    result: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    command = shlex.join([str(part) for part in result["command"]])
    return orchestration_store.record_command_evidence(
        conn,
        run_id,
        command,
        purpose="geond-orchestrator finalize git checkpoint",
        status="passed" if result.get("exit_code") == 0 else "failed",
        exit_code=result.get("exit_code"),
        stdout_summary=truncate_text(result.get("stdout") or "", 1000),
        stderr_summary=truncate_text(result.get("stderr") or "", 1000),
        metadata={"source": "geond-orchestrator-finalize", "index": index},
    )


def git_finalize_metadata(
    command_results: list[dict[str, Any]],
    workspace: dict[str, Any],
    remote: str,
) -> dict[str, Any]:
    command_records = [command_result_record(result) for result in command_results]
    commit_sha = latest_stdout_for_label(command_results, "git commit sha")
    if not commit_sha:
        commit_sha = latest_stdout_for_label(command_results, "git head")
    branch = latest_stdout_for_label(command_results, "git branch")
    pr_url = extract_first_url(latest_stdout_for_label(command_results, "gh pr create"))
    return {
        "source": "geond-orchestrator-finalize",
        "workspace_path": workspace.get("workspace_path"),
        "git": {
            "commit_sha": commit_sha,
            "branch": branch,
            "remote": remote,
            "pr_url": pr_url,
            "commands": command_records,
        },
    }


def command_record(step: dict[str, Any]) -> dict[str, str]:
    command = [str(part) for part in step["command"]]
    return {"label": str(step["label"]), "command": shlex.join(command)}


def command_result_record(result: dict[str, Any]) -> dict[str, Any]:
    command = [str(part) for part in result["command"]]
    return {
        "label": result.get("label"),
        "command": shlex.join(command),
        "exit_code": result.get("exit_code"),
        "timed_out": result.get("timed_out"),
    }


def latest_stdout_for_label(command_results: list[dict[str, Any]], label: str) -> str | None:
    for result in reversed(command_results):
        if result.get("label") == label:
            value = str(result.get("stdout") or "").strip().splitlines()
            return value[0] if value else None
    return None


def pr_body_text(run_summary: dict[str, Any], pr_body_file: Path | None) -> str:
    if pr_body_file:
        return pr_body_file.read_text(encoding="utf-8")
    return str(run_summary.get("markdown") or "Finalized by Geond Orchestrator.")


def find_gh_binary() -> str | None:
    return shutil.which("gh")


def error_result(code: str, message: str) -> dict[str, Any]:
    return {"schema": "geond.git_finalize.v1", "status": "error", "code": code, "message": message}


def truncate_text(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def normalize_process_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def extract_first_url(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"https?://\S+", value)
    return match.group(0) if match else None
