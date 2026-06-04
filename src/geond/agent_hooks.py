from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from psycopg import Connection
from psycopg.cursor import Cursor
from psycopg.types.json import Jsonb

from geond.storage import orchestration as orchestration_store

HOOK_EVENT_SCHEMA = "geond.agent_hook_event.v1"
HOOK_TEMPLATE_SCHEMA = "geond.agent_hook_template.v1"

HOOK_EVENT_TYPES = {
    "session_start",
    "heartbeat",
    "tool_start",
    "tool_end",
    "validation",
    "handoff",
    "session_stop",
    "compaction",
}
HOOK_TEMPLATE_AGENTS = {"codex", "claude"}

DEFAULT_ACTION_STATUS = {
    "session_start": "active",
    "heartbeat": "active",
    "tool_start": "running",
    "tool_end": "completed",
    "validation": "recorded",
    "handoff": "recorded",
    "session_stop": "stopped",
    "compaction": "recorded",
}


def record_hook_event(
    conn: Connection,
    *,
    workspace_id_or_uri: str | None = None,
    agent_name: str,
    event_type: str,
    session_external_id: str,
    summary: str = "",
    run_id: str | None = None,
    task_id: str | None = None,
    worker_session_id: str | None = None,
    lease_id: str | None = None,
    command: str | None = None,
    exit_code: int | None = None,
    status: str | None = None,
    purpose: str | None = None,
    stdout_summary: str = "",
    stderr_summary: str = "",
    log_path: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    ttl_minutes: int | None = 120,
) -> dict[str, Any]:
    payload = {
        "workspace_id_or_uri": workspace_id_or_uri,
        "agent_name": agent_name,
        "event_type": event_type,
        "session_external_id": session_external_id,
        "summary": summary,
        "run_id": run_id,
        "task_id": task_id,
        "worker_session_id": worker_session_id,
        "lease_id": lease_id,
        "command": command,
        "exit_code": exit_code,
        "status": status,
        "purpose": purpose,
        "stdout_summary": stdout_summary,
        "stderr_summary": stderr_summary,
        "log_path": log_path,
        "metadata": metadata or {},
    }
    return record_hook_event_payload(
        conn,
        payload,
        idempotency_key=idempotency_key,
        ttl_minutes=ttl_minutes,
    )


def record_hook_event_payload(
    conn: Connection,
    payload: dict[str, Any],
    *,
    idempotency_key: str | None = None,
    ttl_minutes: int | None = 120,
) -> dict[str, Any]:
    normalized = normalize_hook_payload(payload)
    if normalized.get("status") == "error":
        return normalized

    run_id = normalized.get("run_id")
    workspace_id = resolve_hook_workspace_id(
        conn,
        normalized.get("workspace_id_or_uri"),
        run_id,
    )
    if not workspace_id:
        return orchestration_store.error_result(
            "WORKSPACE_REQUIRED",
            "Hook events require --workspace or a run_id that resolves to a workspace.",
            suggested_cli_command=(
                "geond hook record --workspace file:///repo --agent codex "
                "--event session_start --session-external-id <id>"
            ),
        )

    normalized["workspace_id"] = workspace_id
    payload_idempotency_key = normalized.pop("idempotency_key", None)
    effective_idempotency_key = idempotency_key or payload_idempotency_key
    event_payload = dict(normalized)
    event_payload.pop("schema", None)

    with conn.cursor() as cur:
        hit = orchestration_store.idempotency_result(
            cur,
            "record_agent_hook_event",
            workspace_id,
            effective_idempotency_key,
            event_payload,
        )
        if hit is not None:
            conn.commit()
            return hit

        action_id = insert_hook_agent_action_cursor(cur, workspace_id, normalized)
        hook_event = {
            **normalized,
            "schema": HOOK_EVENT_SCHEMA,
            "action_id": action_id,
        }
        result: dict[str, Any] = {
            "schema": HOOK_EVENT_SCHEMA,
            "status": "ok",
            "code": None,
            "workspace_id": workspace_id,
            "hook_event": hook_event,
            "action": {
                "action_id": action_id,
                "action_type": action_type_for_event(normalized["event_type"]),
                "status": normalized["status"],
            },
            "lease_renewal": None,
            "command_evidence": None,
        }
    conn.commit()

    if normalized["event_type"] == "heartbeat" and normalized.get("lease_id"):
        result["lease_renewal"] = orchestration_store.renew_task_lease(
            conn,
            normalized["lease_id"],
            worker_session_id=normalized.get("worker_session_id"),
            ttl_minutes=ttl_minutes,
            idempotency_key=derived_key(effective_idempotency_key, "lease-renewal"),
        )

    if normalized["event_type"] == "validation" and normalized.get("command") and run_id:
        result["command_evidence"] = orchestration_store.record_command_evidence(
            conn,
            run_id=run_id,
            task_id=normalized.get("task_id"),
            worker_session_id=normalized.get("worker_session_id"),
            command=normalized["command"],
            purpose=normalized.get("purpose") or "agent hook validation",
            status=normalized["status"],
            exit_code=normalized.get("exit_code"),
            stdout_summary=normalized.get("stdout_summary") or "",
            stderr_summary=normalized.get("stderr_summary") or "",
            log_path=normalized.get("log_path"),
            metadata={
                "source": "agent_hook",
                "hook_action_id": result["action"]["action_id"],
                "hook_event_type": normalized["event_type"],
                **(normalized.get("metadata") or {}),
            },
            idempotency_key=derived_key(effective_idempotency_key, "command-evidence"),
        )

    with conn.cursor() as cur:
        orchestration_store.remember_idempotency(
            cur,
            "record_agent_hook_event",
            workspace_id,
            effective_idempotency_key,
            event_payload,
            result,
        )
    conn.commit()
    return result


def normalize_hook_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event_type = normalize_text(payload.get("event_type") or payload.get("event"))
    agent_name = normalize_text(payload.get("agent_name") or payload.get("agent"))
    session_external_id = normalize_text(
        payload.get("session_external_id") or payload.get("session_id")
    )
    workspace_id_or_uri = normalize_optional_text(
        payload.get("workspace_id_or_uri") or payload.get("workspace")
    )

    if event_type not in HOOK_EVENT_TYPES:
        return orchestration_store.error_result(
            "HOOK_EVENT_UNSUPPORTED",
            "Hook event_type must be one of: " + ", ".join(sorted(HOOK_EVENT_TYPES)),
            related_ids={"event_type": event_type},
        )
    if not agent_name:
        return orchestration_store.error_result("AGENT_REQUIRED", "Hook events require agent_name.")
    if not session_external_id:
        return orchestration_store.error_result(
            "SESSION_REQUIRED",
            "Hook events require session_external_id.",
        )

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        return orchestration_store.error_result(
            "HOOK_PAYLOAD_INVALID",
            "Hook metadata must be a JSON object.",
        )

    exit_code = payload.get("exit_code")
    if exit_code in {"", None}:
        exit_code = None
    elif not isinstance(exit_code, int):
        try:
            exit_code = int(str(exit_code))
        except ValueError:
            return orchestration_store.error_result(
                "HOOK_PAYLOAD_INVALID",
                "Hook exit_code must be an integer.",
            )

    status = normalize_optional_text(payload.get("status"))
    if not status:
        status = default_status(event_type, exit_code)

    summary = normalize_optional_text(payload.get("summary"))
    if not summary:
        summary = f"{agent_name} {event_type.replace('_', ' ')}"

    return {
        "schema": HOOK_EVENT_SCHEMA,
        "workspace_id_or_uri": workspace_id_or_uri,
        "agent_name": agent_name,
        "event_type": event_type,
        "session_external_id": session_external_id,
        "summary": summary,
        "run_id": normalize_optional_text(payload.get("run_id") or payload.get("run")),
        "task_id": normalize_optional_text(payload.get("task_id") or payload.get("task")),
        "worker_session_id": normalize_optional_text(payload.get("worker_session_id")),
        "lease_id": normalize_optional_text(payload.get("lease_id")),
        "command": normalize_optional_text(payload.get("command")),
        "exit_code": exit_code,
        "status": status,
        "purpose": normalize_optional_text(payload.get("purpose")),
        "stdout_summary": normalize_optional_text(payload.get("stdout_summary")) or "",
        "stderr_summary": normalize_optional_text(payload.get("stderr_summary")) or "",
        "log_path": normalize_optional_text(payload.get("log_path")),
        "metadata": metadata,
        "idempotency_key": normalize_optional_text(payload.get("idempotency_key")),
    }


def resolve_hook_workspace_id(
    conn: Connection,
    workspace_id_or_uri: str | None,
    run_id: str | None,
) -> str | None:
    if workspace_id_or_uri:
        return orchestration_store.ensure_workspace(conn, workspace_id_or_uri)
    if not run_id:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT workspace_id::text
            FROM orchestration_runs
            WHERE id::text = %s
            LIMIT 1
            """,
            (run_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def insert_hook_agent_action_cursor(
    cur: Cursor,
    workspace_id: str,
    event: dict[str, Any],
) -> str:
    cur.execute(
        """
        INSERT INTO agents (name, kind)
        VALUES (%s, 'coding-agent')
        ON CONFLICT (name, kind) DO UPDATE SET name = EXCLUDED.name
        RETURNING id::text
        """,
        (event["agent_name"],),
    )
    agent_id = cur.fetchone()[0]
    session_id = resolve_session_id_cursor(
        cur,
        workspace_id=workspace_id,
        session_external_id=event["session_external_id"],
    )
    cur.execute(
        """
        INSERT INTO agent_actions (
            workspace_id, agent_id, session_id, action_type, intent, status,
            summary, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (
            workspace_id,
            agent_id,
            session_id,
            action_type_for_event(event["event_type"]),
            event.get("command") or event.get("purpose"),
            event["status"],
            event["summary"],
            Jsonb(hook_action_metadata(event)),
        ),
    )
    return cur.fetchone()[0]


def resolve_session_id_cursor(
    cur: Cursor,
    *,
    workspace_id: str,
    session_external_id: str,
) -> str | None:
    cur.execute(
        """
        SELECT id::text
        FROM sessions
        WHERE workspace_id = %s::uuid
          AND (id::text = %s OR external_id = %s)
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (workspace_id, session_external_id, session_external_id),
    )
    row = cur.fetchone()
    return row[0] if row else None


def hook_action_metadata(event: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "source": "agent_hook",
        "schema": HOOK_EVENT_SCHEMA,
        "event_type": event["event_type"],
        "agent_name": event["agent_name"],
        "session_external_id": event["session_external_id"],
        "run_id": event.get("run_id"),
        "task_id": event.get("task_id"),
        "worker_session_id": event.get("worker_session_id"),
        "lease_id": event.get("lease_id"),
        "command": event.get("command"),
        "exit_code": event.get("exit_code"),
        "purpose": event.get("purpose"),
        "stdout_summary": event.get("stdout_summary"),
        "stderr_summary": event.get("stderr_summary"),
        "log_path": event.get("log_path"),
    }
    return {
        key: value
        for key, value in {**metadata, "event_metadata": event.get("metadata") or {}}.items()
        if value not in (None, "", [], {})
    }


def action_type_for_event(event_type: str) -> str:
    return f"hook:{event_type}"


def default_status(event_type: str, exit_code: int | None) -> str:
    if event_type == "validation" and exit_code is not None:
        return "passed" if exit_code == 0 else "failed"
    return DEFAULT_ACTION_STATUS[event_type]


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_optional_text(value: Any) -> str | None:
    normalized = normalize_text(value)
    return normalized or None


def derived_key(idempotency_key: str | None, suffix: str) -> str | None:
    return f"{idempotency_key}:{suffix}" if idempotency_key else None


def load_hook_payload(path: Path) -> dict[str, Any]:
    if str(path) == "-":
        raise ValueError("stdin payloads are not supported by load_hook_payload")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("hook payload must be a JSON object")
    return value


def write_hook_template(
    *,
    agent_name: str,
    output_dir: Path,
    template_format: str = "shell",
) -> dict[str, Any]:
    agent = normalize_text(agent_name).lower()
    if agent not in HOOK_TEMPLATE_AGENTS:
        return orchestration_store.error_result(
            "HOOK_AGENT_UNSUPPORTED",
            "Hook templates are available for codex and claude.",
            related_ids={"agent_name": agent_name},
        )
    if template_format not in {"shell", "json"}:
        return orchestration_store.error_result(
            "HOOK_TEMPLATE_FORMAT_UNSUPPORTED",
            "Hook template format must be shell or json.",
            related_ids={"format": template_format},
        )

    target_dir = output_dir / agent
    target_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, str]] = []
    if template_format == "shell":
        path = target_dir / "record-hook.sh"
        path.write_text(shell_template(agent), encoding="utf-8")
        path.chmod(0o755)
        files.append({"path": str(path), "kind": "shell"})
    if template_format == "json":
        path = target_dir / "hook-event.json"
        path.write_text(
            json.dumps(json_template(agent), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        files.append({"path": str(path), "kind": "json"})
    readme_path = target_dir / "README.md"
    readme_path.write_text(readme_template(agent), encoding="utf-8")
    files.append({"path": str(readme_path), "kind": "readme"})
    return {
        "schema": HOOK_TEMPLATE_SCHEMA,
        "status": "ok",
        "code": None,
        "agent_name": agent,
        "format": template_format,
        "output_dir": str(target_dir),
        "files": files,
    }


def shell_template(agent: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

: "${{GEOND_WORKSPACE:?Set GEOND_WORKSPACE to a workspace URI such as file:///repo}}"
: "${{GEOND_AGENT_SESSION_ID:?Set GEOND_AGENT_SESSION_ID to the agent session id}}"

event_type="${{1:-heartbeat}}"
summary="${{GEOND_HOOK_SUMMARY:-{agent} hook event}}"

args=(
  hook record
  --workspace "$GEOND_WORKSPACE"
  --agent {agent}
  --event "$event_type"
  --session-external-id "$GEOND_AGENT_SESSION_ID"
  --summary "$summary"
)

if [[ -n "${{GEOND_RUN_ID:-}}" ]]; then
  args+=(--run "$GEOND_RUN_ID")
fi
if [[ -n "${{GEOND_TASK_ID:-}}" ]]; then
  args+=(--task "$GEOND_TASK_ID")
fi
if [[ -n "${{GEOND_WORKER_SESSION_ID:-}}" ]]; then
  args+=(--worker-session-id "$GEOND_WORKER_SESSION_ID")
fi
if [[ -n "${{GEOND_LEASE_ID:-}}" ]]; then
  args+=(--lease-id "$GEOND_LEASE_ID")
fi
if [[ -n "${{GEOND_VALIDATION_COMMAND:-}}" ]]; then
  args+=(--command "$GEOND_VALIDATION_COMMAND")
fi
if [[ -n "${{GEOND_VALIDATION_EXIT_CODE:-}}" ]]; then
  args+=(--exit-code "$GEOND_VALIDATION_EXIT_CODE")
fi

geond "${{args[@]}}"
"""


def json_template(agent: str) -> dict[str, Any]:
    return {
        "schema": HOOK_EVENT_SCHEMA,
        "workspace_id_or_uri": "file:///absolute/path/to/repo",
        "agent_name": agent,
        "event_type": "session_start",
        "session_external_id": f"{agent}-session-id",
        "summary": f"{agent} session started",
        "run_id": None,
        "task_id": None,
        "worker_session_id": None,
        "lease_id": None,
        "metadata": {
            "source": "example",
            "redaction": "Do not include raw prompts, secrets, or full stdout.",
        },
    }


def readme_template(agent: str) -> str:
    return f"""# {agent.title()} Hook Adapter

This template records lightweight lifecycle events into Geond without storing raw
prompts, secrets, or full command output.

Required environment:

- `GEOND_WORKSPACE`: workspace URI, for example `file:///repo`
- `GEOND_AGENT_SESSION_ID`: external session id from the agent runtime

Optional environment:

- `GEOND_RUN_ID`
- `GEOND_TASK_ID`
- `GEOND_WORKER_SESSION_ID`
- `GEOND_LEASE_ID`
- `GEOND_VALIDATION_COMMAND`
- `GEOND_VALIDATION_EXIT_CODE`
- `GEOND_HOOK_SUMMARY`

Example:

```bash
GEOND_WORKSPACE=file:///repo \\
GEOND_AGENT_SESSION_ID={agent}-session-1 \\
./record-hook.sh session_start
```
"""
