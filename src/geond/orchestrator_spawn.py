from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from psycopg import Connection

CODEX_AGENT_NAME = "codex"
CLAUDE_AGENT_NAME = "claude"
COPILOT_AGENT_NAME = "copilot"
SUPPORTED_SPAWN_AGENTS = {CODEX_AGENT_NAME, CLAUDE_AGENT_NAME, COPILOT_AGENT_NAME}
OUTPUT_SCHEMA_NAME = "OUTPUT_SCHEMA.json"
PROMPT_NAME = "PROMPT.md"
CODEX_EVENTS_NAME = "CODEX_EVENTS.jsonl"
CODEX_STDERR_NAME = "CODEX_STDERR.txt"
LAST_MESSAGE_NAME = "LAST_MESSAGE.json"
RESULT_NAME = "RESULT.json"
COMMON_AGENT_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


@dataclass(frozen=True)
class SpawnInvocation:
    invocation_id: str
    output_dir: Path
    prompt_path: Path
    output_schema_path: Path
    events_path: Path
    stderr_path: Path
    last_message_path: Path
    result_path: Path


@dataclass(frozen=True)
class CodexRunResult:
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    command: list[str]
    metadata: dict[str, Any] | None = None


def new_invocation(run_id: str, manifest_base_dir: Path) -> SpawnInvocation:
    invocation_id = str(uuid.uuid4())
    output_dir = manifest_base_dir / run_id / "spawn" / invocation_id
    return SpawnInvocation(
        invocation_id=invocation_id,
        output_dir=output_dir,
        prompt_path=output_dir / PROMPT_NAME,
        output_schema_path=output_dir / OUTPUT_SCHEMA_NAME,
        events_path=output_dir / CODEX_EVENTS_NAME,
        stderr_path=output_dir / CODEX_STDERR_NAME,
        last_message_path=output_dir / LAST_MESSAGE_NAME,
        result_path=output_dir / RESULT_NAME,
    )


def child_invocation(parent: SpawnInvocation, name: str) -> SpawnInvocation:
    output_dir = parent.output_dir / name
    return SpawnInvocation(
        invocation_id=f"{parent.invocation_id}-{name}",
        output_dir=output_dir,
        prompt_path=output_dir / PROMPT_NAME,
        output_schema_path=output_dir / OUTPUT_SCHEMA_NAME,
        events_path=output_dir / CODEX_EVENTS_NAME,
        stderr_path=output_dir / CODEX_STDERR_NAME,
        last_message_path=output_dir / LAST_MESSAGE_NAME,
        result_path=output_dir / RESULT_NAME,
    )


def output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "task_status",
            "summary",
            "tested_commands",
            "changed_files",
            "risks",
            "next_action",
        ],
        "properties": {
            "task_status": {"type": "string", "enum": ["done", "blocked"]},
            "summary": {"type": "string"},
            "tested_commands": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["command"],
                    "properties": {
                        "command": {"type": "string"},
                        "purpose": {"type": "string"},
                        "status": {"type": "string"},
                        "exit_code": {"type": ["integer", "null"]},
                        "stdout_summary": {"type": "string"},
                        "stderr_summary": {"type": "string"},
                    },
                },
            },
            "changed_files": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "next_action": {"type": "string"},
        },
    }


def get_workspace_root_uri(conn: Connection, workspace_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT root_uri
            FROM workspaces
            WHERE id = %s::uuid
            LIMIT 1
            """,
            (workspace_id,),
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def resolve_local_workspace_path(root_uri: str) -> dict[str, Any]:
    parsed = urlparse(root_uri)
    if parsed.scheme and parsed.scheme != "file":
        return {
            "status": "error",
            "code": "WORKSPACE_NOT_LOCAL",
            "message": "Spawn mode only supports local file workspaces.",
            "root_uri": root_uri,
        }
    if parsed.scheme == "file":
        if parsed.netloc not in {"", "localhost"}:
            return {
                "status": "error",
                "code": "WORKSPACE_NOT_LOCAL",
                "message": "Remote file workspaces are not supported for spawn mode.",
                "root_uri": root_uri,
            }
        raw_path = unquote(parsed.path)
    else:
        raw_path = root_uri

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    if not path.exists() or not path.is_dir():
        return {
            "status": "error",
            "code": "WORKSPACE_PATH_NOT_FOUND",
            "message": "Workspace path does not exist or is not a directory.",
            "root_uri": root_uri,
            "workspace_path": str(path),
        }
    return {
        "status": "ok",
        "code": None,
        "root_uri": root_uri,
        "workspace_path": str(path),
    }


def planned_worktree_path(
    *,
    manifest_base_dir: Path,
    run_id: str,
    invocation_id: str,
) -> Path:
    return manifest_base_dir / run_id / "worktrees" / invocation_id


def prepare_git_worktree(
    *,
    source_workspace_path: str,
    worktree_path: Path,
    branch_name: str,
    create: bool,
) -> dict[str, Any]:
    payload = {
        "mode": "git_worktree",
        "source_workspace_path": source_workspace_path,
        "worktree_path": str(worktree_path),
        "branch_name": branch_name,
        "created": False,
    }
    if not create:
        return {"status": "ok", "code": None, **payload}

    source = Path(source_workspace_path)
    try:
        check = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            check=False,
        )
        if check.returncode != 0:
            return {
                "status": "error",
                "code": "WORKTREE_SOURCE_NOT_GIT_REPO",
                "message": check.stderr.strip() or "Workspace is not a git repository.",
                **payload,
            }
        if worktree_path.exists():
            return {"status": "ok", "code": None, **payload, "created": False}
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "worktree",
                "add",
                "-B",
                branch_name,
                str(worktree_path),
                "HEAD",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return {
            "status": "error",
            "code": "WORKTREE_CREATE_FAILED",
            "message": str(exc),
            **payload,
        }
    if result.returncode != 0:
        return {
            "status": "error",
            "code": "WORKTREE_CREATE_FAILED",
            "message": result.stderr.strip() or result.stdout.strip(),
            **payload,
        }
    return {"status": "ok", "code": None, **payload, "created": True}


def find_codex_binary() -> str | None:
    configured = os.environ.get("GEOND_CODEX_BIN")
    if configured:
        return configured
    return shutil.which("codex")


def find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for directory in COMMON_AGENT_BIN_DIRS:
        candidate = Path(directory) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def find_agent_binary(agent_name: str) -> str | None:
    if agent_name == CODEX_AGENT_NAME:
        return find_codex_binary()
    if agent_name == CLAUDE_AGENT_NAME:
        configured = os.environ.get("GEOND_CLAUDE_BIN")
        if configured:
            return configured
        return shutil.which("claude")
    if agent_name == COPILOT_AGENT_NAME:
        configured = os.environ.get("GEOND_COPILOT_BIN")
        if configured:
            return configured
        return find_executable("copilot") or find_executable("gh")
    return None


def missing_binary_code(agent_name: str) -> str:
    if agent_name == CLAUDE_AGENT_NAME:
        return "CLAUDE_CLI_NOT_FOUND"
    if agent_name == COPILOT_AGENT_NAME:
        return "COPILOT_CLI_NOT_FOUND"
    return "CODEX_CLI_NOT_FOUND"


def build_worker_prompt(
    *,
    status_payload: dict[str, Any],
    run_summary: dict[str, Any],
    selected_task: dict[str, Any],
    workspace_path: str,
    agent_name: str = CODEX_AGENT_NAME,
    result_path: str | None = None,
) -> str:
    readiness = status_payload.get("readiness") or {}
    prompt_payload = {
        "run": status_payload.get("run"),
        "selected_task": selected_task,
        "workspace_path": workspace_path,
        "result_path": result_path,
        "readiness": readiness,
        "blocking_reasons": readiness.get("blocking_reasons") or [],
        "open_findings": status_payload.get("open_findings") or [],
        "pending_approvals": status_payload.get("pending_approvals") or [],
        "latest_decisions": status_payload.get("latest_decisions") or [],
        "run_summary": run_summary,
    }
    return (
        "# Geond Spawned Worker Task\n\n"
        f"You are a spawned {agent_name} worker controlled by Geond Orchestrator.\n"
        "Work only on the selected task. Do not commit or push changes.\n"
        "Use the repository at the provided workspace path.\n"
        "Run only the validation commands that are needed for this task, and report only "
        "commands that actually ran.\n"
        "Write your final JSON result to the provided result_path when one is provided.\n"
        "Your final response must be valid JSON matching the provided output schema. "
        "Do not wrap it in Markdown.\n\n"
        "## Context\n\n"
        f"```json\n{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}\n```\n\n"
        "## Required Final JSON Shape\n\n"
        f"```json\n{json.dumps(output_schema(), ensure_ascii=False, indent=2)}\n```\n"
    )


def write_prompt_bundle(invocation: SpawnInvocation, prompt: str) -> dict[str, Any]:
    invocation.output_dir.mkdir(parents=True, exist_ok=True)
    invocation.prompt_path.write_text(prompt, encoding="utf-8")
    invocation.output_schema_path.write_text(
        json.dumps(output_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "output_dir": str(invocation.output_dir),
        "prompt_path": str(invocation.prompt_path),
        "output_schema_path": str(invocation.output_schema_path),
    }


def build_codex_command(
    *,
    codex_bin: str,
    workspace_path: str,
    invocation: SpawnInvocation,
    model: str | None = None,
    sandbox: str = "workspace-write",
) -> list[str]:
    command = [
        codex_bin,
        "exec",
        "--json",
        "--cd",
        workspace_path,
        "--sandbox",
        sandbox,
        "--output-last-message",
        str(invocation.last_message_path),
        "--output-schema",
        str(invocation.output_schema_path),
    ]
    if model:
        command.extend(["--model", model])
    command.append("-")
    return command


def build_claude_command(
    *,
    claude_bin: str,
    workspace_path: str,
    model: str | None = None,
    max_turns: int = 10,
) -> list[str]:
    inner = [
        claude_bin,
        "-p",
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
    ]
    if model:
        inner.extend(["--model", model])
    return [
        "/bin/sh",
        "-c",
        f"cd {shlex.quote(workspace_path)} && exec {shlex.join(inner)}",
    ]


def build_copilot_command(
    *,
    copilot_bin: str,
    workspace_path: str,
    model: str | None = None,
) -> list[str]:
    if Path(copilot_bin).name == "gh":
        prefix = shlex.join([copilot_bin, "copilot", "--", "-p"])
    else:
        prefix = shlex.join([copilot_bin, "-p"])
    path_prefix = str(Path(copilot_bin).parent)
    options = [
        "--no-auto-update",
        "--no-color",
        "--no-remote",
        "--silent",
        "--output-format=json",
        "--allow-tool=write",
        "--allow-tool=shell",
        "--deny-tool=shell(git push)",
        "--deny-tool=shell(git commit)",
    ]
    if model:
        options.extend(["--model", model])
    tail = shlex.join(options)
    return [
        "/bin/sh",
        "-c",
        (
            f"prompt=$(cat)\n"
            f'export PATH={shlex.quote(path_prefix)}:"$PATH"\n'
            f'cd {shlex.quote(workspace_path)} && exec {prefix} "$prompt" {tail}'
        ),
    ]


def build_agent_command(
    *,
    agent_name: str,
    agent_bin: str,
    workspace_path: str,
    invocation: SpawnInvocation,
    model: str | None = None,
    sandbox: str = "workspace-write",
) -> list[str]:
    if agent_name == CLAUDE_AGENT_NAME:
        return build_claude_command(
            claude_bin=agent_bin,
            workspace_path=workspace_path,
            model=model,
        )
    if agent_name == COPILOT_AGENT_NAME:
        return build_copilot_command(
            copilot_bin=agent_bin,
            workspace_path=workspace_path,
            model=model,
        )
    return build_codex_command(
        codex_bin=agent_bin,
        workspace_path=workspace_path,
        invocation=invocation,
        model=model,
        sandbox=sandbox,
    )


def run_codex(
    *,
    command: list[str],
    prompt: str,
    invocation: SpawnInvocation,
    timeout_seconds: int,
) -> CodexRunResult:
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
        stdout = normalize_process_text(exc.stdout)
        stderr = normalize_process_text(exc.stderr)
        exit_code = None
        timed_out = True

    invocation.events_path.write_text(stdout, encoding="utf-8")
    invocation.stderr_path.write_text(stderr, encoding="utf-8")
    if stdout.strip() and not invocation.last_message_path.exists():
        invocation.last_message_path.write_text(stdout.strip(), encoding="utf-8")
    return CodexRunResult(
        exit_code=exit_code,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        command=command,
    )


def normalize_process_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def parse_worker_result(invocation: SpawnInvocation) -> dict[str, Any]:
    raw_text = read_worker_result_text(invocation)
    if raw_text is None:
        return {
            "status": "error",
            "code": "WORKER_RESULT_MISSING",
            "message": "Worker did not write RESULT.json, LAST_MESSAGE.json, or stdout.",
        }
    payload_result = parse_json_object(raw_text)
    if payload_result.get("status") != "ok":
        return {
            "status": "error",
            "code": "WORKER_RESULT_INVALID_JSON",
            "message": payload_result.get("message"),
            "raw_text": raw_text[:4000],
        }
    payload = payload_result["payload"]
    payload = unwrap_worker_result(payload)
    validation = validate_worker_result(payload)
    if validation.get("status") != "ok":
        validation["raw_payload"] = payload
        return validation
    invocation.result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"status": "ok", "code": None, "result": payload}


def read_worker_result_text(invocation: SpawnInvocation) -> str | None:
    for path in (invocation.result_path, invocation.last_message_path, invocation.events_path):
        if path.exists():
            raw_text = path.read_text(encoding="utf-8").strip()
            if raw_text:
                return raw_text
    return None


def parse_json_object(raw_text: str) -> dict[str, Any]:
    try:
        return {"status": "ok", "code": None, "payload": json.loads(raw_text)}
    except json.JSONDecodeError as exc:
        extracted = extract_first_json_object(raw_text)
        if extracted is None:
            return {"status": "error", "code": "WORKER_RESULT_INVALID_JSON", "message": str(exc)}
        try:
            return {"status": "ok", "code": None, "payload": json.loads(extracted)}
        except json.JSONDecodeError as extracted_exc:
            return {
                "status": "error",
                "code": "WORKER_RESULT_INVALID_JSON",
                "message": str(extracted_exc),
            }


def extract_first_json_object(raw_text: str) -> str | None:
    start = raw_text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(raw_text[start:], start=start):
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw_text[start : index + 1]
    return None


def unwrap_worker_result(payload: Any) -> Any:
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            stripped = result.strip()
            if stripped.startswith("{"):
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    return payload
    return payload


def validate_worker_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "status": "error",
            "code": "WORKER_RESULT_INVALID",
            "message": "Result is not an object.",
        }
    if payload.get("task_status") not in {"done", "blocked"}:
        return {
            "status": "error",
            "code": "WORKER_RESULT_INVALID",
            "message": "task_status must be done or blocked.",
        }
    for key in ("summary", "next_action"):
        if not isinstance(payload.get(key), str):
            return {
                "status": "error",
                "code": "WORKER_RESULT_INVALID",
                "message": f"{key} must be a string.",
            }
    for key in ("tested_commands", "changed_files", "risks"):
        if not isinstance(payload.get(key), list):
            return {
                "status": "error",
                "code": "WORKER_RESULT_INVALID",
                "message": f"{key} must be a list.",
            }
    return {"status": "ok", "code": None}


def normalized_tested_commands(payload: dict[str, Any]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for item in payload.get("tested_commands") or []:
        if isinstance(item, str):
            commands.append(
                {
                    "command": item,
                    "purpose": "",
                    "status": None,
                    "exit_code": None,
                    "stdout_summary": "",
                    "stderr_summary": "",
                }
            )
        elif isinstance(item, dict) and isinstance(item.get("command"), str):
            commands.append(
                {
                    "command": item["command"],
                    "purpose": str(item.get("purpose") or ""),
                    "status": item.get("status") if isinstance(item.get("status"), str) else None,
                    "exit_code": item.get("exit_code")
                    if isinstance(item.get("exit_code"), int)
                    else None,
                    "stdout_summary": str(item.get("stdout_summary") or ""),
                    "stderr_summary": str(item.get("stderr_summary") or ""),
                }
            )
    return commands
