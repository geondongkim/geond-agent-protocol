from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from geond import orchestrator_spawn

WORKER_REVIEW_SCHEMA = "geond.worker_review.v1"
WORKER_PLAN_SCHEMA = "geond.worker_plan.v1"
REVIEW_JSON_NAME = "WORKER_REVIEW.json"
REVIEW_MARKDOWN_NAME = "WORKER_REVIEW.md"


def worker_plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "summary",
            "steps",
            "files_to_change",
            "validation_commands",
            "risks",
        ],
        "properties": {
            "summary": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "string"}},
            "files_to_change": {"type": "array", "items": {"type": "string"}},
            "validation_commands": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
        },
    }


def review_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision", "summary", "findings", "recommended_next_action"],
        "properties": {
            "decision": {"type": "string", "enum": ["approved", "needs_revision", "blocked"]},
            "summary": {"type": "string"},
            "findings": {"type": "array", "items": {"type": "string"}},
            "recommended_next_action": {"type": "string"},
        },
    }


def run_copilot_with_senior_review(
    *,
    command: list[str],
    prompt: str,
    invocation: orchestrator_spawn.SpawnInvocation,
    timeout_seconds: int,
    workspace_path: str,
    selected_task: dict[str, Any],
    model: str | None = None,
    runner: Any | None = None,
) -> orchestrator_spawn.CodexRunResult:
    run_agent = runner or orchestrator_spawn.run_codex
    plan_invocation = orchestrator_spawn.child_invocation(invocation, "copilot-plan")
    plan_invocation.output_dir.mkdir(parents=True, exist_ok=True)
    invocation.output_dir.mkdir(parents=True, exist_ok=True)
    plan_prompt = build_plan_prompt(
        base_prompt=prompt,
        result_path=str(plan_invocation.result_path),
        selected_task=selected_task,
    )
    plan_result = run_agent(
        command=command,
        prompt=plan_prompt,
        invocation=plan_invocation,
        timeout_seconds=timeout_seconds,
    )
    if plan_result.timed_out or plan_result.exit_code != 0:
        blocked = blocked_worker_result(
            summary="Copilot failed while producing an implementation plan.",
            code="COPILOT_PLAN_FAILED",
            next_action="Inspect Copilot plan logs and retry.",
        )
        write_json(invocation.result_path, blocked)
        return reviewed_run_result(
            plan_result,
            command=command,
            invocation=invocation,
            stage="plan",
            review=None,
            worker_result=blocked,
        )

    plan_payload = parse_worker_plan(plan_invocation)
    if plan_payload.get("status") != "ok":
        blocked = blocked_worker_result(
            summary="Copilot returned an invalid implementation plan.",
            code=str(plan_payload.get("code") or "WORKER_PLAN_INVALID"),
            next_action="Inspect PLAN result and retry.",
        )
        write_json(invocation.result_path, blocked)
        return reviewed_run_result(
            plan_result,
            command=command,
            invocation=invocation,
            stage="plan",
            review=None,
            worker_result=blocked,
            error=plan_payload,
        )

    plan_review = review_payload_with_codex(
        workspace_path=workspace_path,
        invocation=invocation,
        stage="plan",
        review_subject={
            "schema": WORKER_PLAN_SCHEMA,
            "selected_task": selected_task,
            "worker_plan": plan_payload["plan"],
        },
        model=model,
        timeout_seconds=timeout_seconds,
    )
    if plan_review.get("decision") != "approved":
        blocked = blocked_worker_result(
            summary="Senior Codex review blocked the Copilot plan.",
            code=str(plan_review.get("code") or "WORKER_PLAN_REVIEW_BLOCKED"),
            next_action=str(plan_review.get("recommended_next_action") or "Revise the plan."),
            risks=[str(item) for item in plan_review.get("findings") or []],
        )
        write_json(invocation.result_path, blocked)
        return reviewed_run_result(
            plan_result,
            command=command,
            invocation=invocation,
            stage="plan",
            review=plan_review,
            worker_result=blocked,
        )

    implementation_prompt = build_implementation_prompt(
        base_prompt=prompt,
        plan=plan_payload["plan"],
        plan_review=plan_review,
        result_path=str(invocation.result_path),
    )
    implementation_result = run_agent(
        command=command,
        prompt=implementation_prompt,
        invocation=invocation,
        timeout_seconds=timeout_seconds,
    )
    if implementation_result.timed_out or implementation_result.exit_code != 0:
        return implementation_result

    parsed = orchestrator_spawn.parse_worker_result(invocation)
    if parsed.get("status") != "ok":
        blocked = blocked_worker_result(
            summary="Copilot returned an invalid implementation result.",
            code=str(parsed.get("code") or "WORKER_RESULT_INVALID"),
            next_action="Inspect Copilot implementation output and retry.",
        )
        write_json(invocation.result_path, blocked)
        return reviewed_run_result(
            implementation_result,
            command=command,
            invocation=invocation,
            stage="implementation",
            review=None,
            worker_result=blocked,
            error=parsed,
        )

    implementation_review = review_payload_with_codex(
        workspace_path=workspace_path,
        invocation=invocation,
        stage="implementation",
        review_subject={
            "selected_task": selected_task,
            "worker_plan": plan_payload["plan"],
            "worker_result": parsed["result"],
            "workspace_diff": read_workspace_diff(workspace_path),
        },
        model=model,
        timeout_seconds=timeout_seconds,
    )
    if implementation_review.get("decision") != "approved":
        blocked = blocked_worker_result(
            summary="Senior Codex review blocked the Copilot implementation.",
            code=str(implementation_review.get("code") or "WORKER_IMPLEMENTATION_REVIEW_BLOCKED"),
            next_action=str(
                implementation_review.get("recommended_next_action") or "Revise the implementation."
            ),
            risks=[str(item) for item in implementation_review.get("findings") or []],
        )
        write_json(invocation.result_path, blocked)
        return reviewed_run_result(
            implementation_result,
            command=command,
            invocation=invocation,
            stage="implementation",
            review=implementation_review,
            worker_result=blocked,
        )

    return reviewed_run_result(
        implementation_result,
        command=command,
        invocation=invocation,
        stage="implementation",
        review=implementation_review,
        worker_result=parsed["result"],
    )


def review_payload_with_codex(
    *,
    workspace_path: str,
    invocation: orchestrator_spawn.SpawnInvocation,
    stage: str,
    review_subject: dict[str, Any],
    model: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    codex_bin = orchestrator_spawn.find_agent_binary(orchestrator_spawn.CODEX_AGENT_NAME)
    if not codex_bin:
        return {
            "schema": WORKER_REVIEW_SCHEMA,
            "status": "error",
            "code": "CODEX_CLI_NOT_FOUND",
            "stage": stage,
            "decision": "blocked",
            "summary": "Codex CLI was not found for senior review.",
            "findings": ["CODEX_CLI_NOT_FOUND"],
            "recommended_next_action": "Install Codex CLI or rerun with manual review.",
        }
    review_invocation = orchestrator_spawn.child_invocation(invocation, f"codex-{stage}-review")
    review_invocation.output_dir.mkdir(parents=True, exist_ok=True)
    review_invocation.output_schema_path.write_text(
        json.dumps(review_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    review_prompt = build_review_prompt(
        stage=stage,
        review_subject=review_subject,
        result_path=str(review_invocation.result_path),
    )
    review_invocation.prompt_path.write_text(review_prompt, encoding="utf-8")
    command = orchestrator_spawn.build_agent_command(
        agent_name=orchestrator_spawn.CODEX_AGENT_NAME,
        agent_bin=codex_bin,
        workspace_path=workspace_path,
        invocation=review_invocation,
        model=model,
        sandbox="read-only",
    )
    result = orchestrator_spawn.run_codex(
        command=command,
        prompt=review_prompt,
        invocation=review_invocation,
        timeout_seconds=timeout_seconds,
    )
    if result.timed_out or result.exit_code != 0:
        return {
            "schema": WORKER_REVIEW_SCHEMA,
            "status": "error",
            "code": "CODEX_REVIEW_FAILED",
            "stage": stage,
            "decision": "blocked",
            "summary": "Codex reviewer failed before returning a review.",
            "findings": ["CODEX_REVIEW_FAILED"],
            "recommended_next_action": "Inspect reviewer logs and retry.",
            "artifact_refs": artifact_refs(review_invocation),
        }
    parsed = parse_review_result(review_invocation)
    parsed["artifact_refs"] = artifact_refs(review_invocation)
    write_review_artifacts(invocation, parsed, stage)
    return parsed


def parse_worker_plan(invocation: orchestrator_spawn.SpawnInvocation) -> dict[str, Any]:
    raw_text = orchestrator_spawn.read_worker_result_text(invocation)
    if raw_text is None:
        return {"status": "error", "code": "WORKER_PLAN_MISSING", "message": "Plan missing."}
    payload_result = orchestrator_spawn.parse_json_object(raw_text)
    if payload_result.get("status") != "ok":
        return {
            "status": "error",
            "code": "WORKER_PLAN_INVALID_JSON",
            "message": payload_result.get("message"),
            "raw_text": raw_text[:4000],
        }
    payload = orchestrator_spawn.unwrap_worker_result(payload_result["payload"])
    if not isinstance(payload, dict):
        return {
            "status": "error",
            "code": "WORKER_PLAN_INVALID",
            "message": "Plan is not an object.",
        }
    for key in ("summary", "steps", "files_to_change", "validation_commands", "risks"):
        if key == "summary":
            if not isinstance(payload.get(key), str):
                return {
                    "status": "error",
                    "code": "WORKER_PLAN_INVALID",
                    "message": "summary must be a string.",
                }
        elif not isinstance(payload.get(key), list):
            return {
                "status": "error",
                "code": "WORKER_PLAN_INVALID",
                "message": f"{key} must be a list.",
            }
    write_json(invocation.result_path, payload)
    return {"status": "ok", "code": None, "plan": payload}


def parse_review_result(invocation: orchestrator_spawn.SpawnInvocation) -> dict[str, Any]:
    raw_text = orchestrator_spawn.read_worker_result_text(invocation)
    if raw_text is None:
        return review_error("WORKER_REVIEW_MISSING", "Review result missing.")
    payload_result = orchestrator_spawn.parse_json_object(raw_text)
    if payload_result.get("status") != "ok":
        return review_error(
            "WORKER_REVIEW_INVALID_JSON",
            str(payload_result.get("message") or "Invalid JSON."),
        )
    payload = orchestrator_spawn.unwrap_worker_result(payload_result["payload"])
    if not isinstance(payload, dict):
        return review_error("WORKER_REVIEW_INVALID", "Review is not an object.")
    decision = payload.get("decision")
    if decision not in {"approved", "needs_revision", "blocked"}:
        return review_error(
            "WORKER_REVIEW_INVALID",
            "decision must be approved, needs_revision, or blocked.",
        )
    review = {
        "schema": WORKER_REVIEW_SCHEMA,
        "status": "ok",
        "code": None,
        "decision": decision,
        "summary": str(payload.get("summary") or ""),
        "findings": [str(item) for item in payload.get("findings") or []],
        "recommended_next_action": str(payload.get("recommended_next_action") or ""),
    }
    write_json(invocation.result_path, review)
    return review


def review_error(code: str, message: str) -> dict[str, Any]:
    return {
        "schema": WORKER_REVIEW_SCHEMA,
        "status": "error",
        "code": code,
        "decision": "blocked",
        "summary": message,
        "findings": [code],
        "recommended_next_action": "Inspect review logs and retry.",
    }


def build_plan_prompt(*, base_prompt: str, result_path: str, selected_task: dict[str, Any]) -> str:
    return (
        "# Geond Copilot Planning Phase\n\n"
        "You are the junior Copilot worker. Produce an implementation plan only. "
        "Do not edit files yet. Do not run commands unless necessary for inspection. "
        f"Write the JSON plan to `{result_path}` and also return JSON only.\n\n"
        "## Selected Task\n\n"
        f"```json\n{json.dumps(selected_task, ensure_ascii=False, indent=2)}\n```\n\n"
        "## Plan JSON Schema\n\n"
        f"```json\n{json.dumps(worker_plan_schema(), ensure_ascii=False, indent=2)}\n```\n\n"
        "## Original Worker Context\n\n"
        f"{base_prompt}\n"
    )


def build_implementation_prompt(
    *,
    base_prompt: str,
    plan: dict[str, Any],
    plan_review: dict[str, Any],
    result_path: str,
) -> str:
    return (
        "# Geond Copilot Implementation Phase\n\n"
        "Your implementation plan was approved by the senior Codex reviewer. "
        "Implement only that approved plan. Do not commit or push. "
        f"Write the final worker JSON to `{result_path}` and also return JSON only.\n\n"
        "## Approved Plan\n\n"
        f"```json\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n```\n\n"
        "## Senior Plan Review\n\n"
        f"```json\n{json.dumps(plan_review, ensure_ascii=False, indent=2)}\n```\n\n"
        "## Original Worker Context\n\n"
        f"{base_prompt}\n"
    )


def build_review_prompt(
    *,
    stage: str,
    review_subject: dict[str, Any],
    result_path: str,
) -> str:
    return (
        "# Geond Senior Codex Review\n\n"
        "You are the senior Codex reviewer supervising a junior Copilot worker. "
        "Do not modify files. Review the submitted plan or implementation. "
        "Approve only if the work is scoped, testable, and safe for the selected task. "
        f"Write the review JSON to `{result_path}` and return JSON only.\n\n"
        f"## Review Stage\n\n`{stage}`\n\n"
        "## Review Subject\n\n"
        f"```json\n{json.dumps(review_subject, ensure_ascii=False, indent=2)}\n```\n\n"
        "## Required Review JSON Schema\n\n"
        f"```json\n{json.dumps(review_schema(), ensure_ascii=False, indent=2)}\n```\n"
    )


def blocked_worker_result(
    *,
    summary: str,
    code: str,
    next_action: str,
    risks: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "task_status": "blocked",
        "summary": summary,
        "tested_commands": [],
        "changed_files": [],
        "risks": risks or [code],
        "next_action": next_action,
    }


def reviewed_run_result(
    result: orchestrator_spawn.CodexRunResult,
    *,
    command: list[str],
    invocation: orchestrator_spawn.SpawnInvocation,
    stage: str,
    review: dict[str, Any] | None,
    worker_result: dict[str, Any],
    error: dict[str, Any] | None = None,
) -> orchestrator_spawn.CodexRunResult:
    metadata = {
        "worker_review": {
            "schema": WORKER_REVIEW_SCHEMA,
            "stage": stage,
            "decision": (review or {}).get("decision", "blocked"),
            "code": (review or error or {}).get("code"),
            "summary": (review or error or {}).get("summary")
            or (review or error or {}).get("message"),
            "findings": (review or {}).get("findings") or [],
            "artifact_refs": (review or {}).get("artifact_refs") or {},
        },
        "worker_result": worker_result,
    }
    return orchestrator_spawn.CodexRunResult(
        exit_code=0,
        timed_out=False,
        stdout=result.stdout,
        stderr=result.stderr,
        command=command,
        metadata=metadata,
    )


def read_workspace_diff(workspace_path: str) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "-C", workspace_path, "diff", "--stat", "--", "."],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return result.stderr.strip()[:4000]
    return result.stdout.strip()[:4000]


def artifact_refs(invocation: orchestrator_spawn.SpawnInvocation) -> dict[str, str]:
    return {
        "output_dir": str(invocation.output_dir),
        "prompt_path": str(invocation.prompt_path),
        "result_path": str(invocation.result_path),
        "events_path": str(invocation.events_path),
        "stderr_path": str(invocation.stderr_path),
    }


def write_review_artifacts(
    invocation: orchestrator_spawn.SpawnInvocation,
    review: dict[str, Any],
    stage: str,
) -> None:
    review_dir = invocation.output_dir / "reviews"
    review_dir.mkdir(parents=True, exist_ok=True)
    write_json(review_dir / f"{stage}_{REVIEW_JSON_NAME}", review)
    (review_dir / f"{stage}_{REVIEW_MARKDOWN_NAME}").write_text(
        format_review_markdown(review),
        encoding="utf-8",
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def format_review_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Worker Review",
        "",
        f"- Decision: `{payload.get('decision')}`",
        f"- Status: `{payload.get('status')}`",
        f"- Code: `{payload.get('code')}`",
        f"- Summary: {payload.get('summary') or ''}",
    ]
    findings = payload.get("findings") or []
    if findings:
        lines.extend(["", "## Findings"])
        lines.extend(f"- {item}" for item in findings)
    next_action = payload.get("recommended_next_action")
    if next_action:
        lines.extend(["", "## Next Action", str(next_action)])
    return "\n".join(lines) + "\n"
