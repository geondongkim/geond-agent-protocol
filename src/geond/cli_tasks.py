from __future__ import annotations

from typing import Any

from psycopg import Connection

from geond.storage.context_review import review_workspace_context
from geond.storage.dashboard import get_dashboard_overview
from geond.storage.repository import (
    list_active_file_reservations,
    list_active_symbol_reservations,
    list_handoff_summaries,
    record_agent_action,
    record_changeset,
    record_handoff_summary,
    release_reservation,
    release_symbol_reservation,
    renew_reservation,
    renew_symbol_reservation,
    reserve_files,
    reserve_symbols,
    resolve_workspace_id,
)


def start_task(
    conn: Connection,
    workspace_id_or_uri: str,
    agent_name: str,
    intent: str,
    file_paths: list[str] | None = None,
    symbols: list[str] | None = None,
    reserve: bool = False,
    ttl_minutes: int | None = 120,
    override_reason: str | None = None,
    dry_run: bool = False,
    limit: int = 5,
    session_id: str | None = None,
    session_external_id: str | None = None,
) -> dict[str, Any]:
    workspace_id = require_task_workspace(conn, workspace_id_or_uri)
    requested_files = normalized_values(file_paths)
    requested_symbols = normalized_values(symbols)
    overview = get_dashboard_overview(conn, workspace_id, limit=max(limit, 1))
    open_handoffs = list_handoff_summaries(conn, workspace_id, status="open", limit=limit)
    file_reservations = list_active_file_reservations(
        conn,
        workspace_id,
        requested_files or None,
    )
    symbol_reservations = list_active_symbol_reservations(
        conn,
        workspace_id,
        requested_symbols or None,
    )
    review = review_workspace_context(
        conn,
        workspace_id,
        intent=intent,
        file_paths=requested_files,
        symbols=requested_symbols,
        agent_name=agent_name,
        limit=limit,
    )

    action_id = None
    file_reservation_result = None
    symbol_reservation_result = None
    if not dry_run:
        action_id = record_agent_action(
            conn,
            workspace_id=workspace_id,
            agent_name=agent_name,
            action_type="task_start",
            summary=intent or "Task started",
            intent=intent,
            status="recorded",
            metadata={"source": "start-task"},
            session_id=session_id,
            session_external_id=session_external_id,
        )
        if reserve and requested_files:
            file_reservation_result = reserve_files(
                conn,
                workspace_id=workspace_id,
                agent_name=agent_name,
                file_paths=requested_files,
                purpose=intent,
                ttl_minutes=ttl_minutes,
                override_reason=override_reason,
            )
        if reserve and requested_symbols:
            symbol_reservation_result = reserve_symbols(
                conn,
                workspace_id=workspace_id,
                agent_name=agent_name,
                symbols=requested_symbols,
                purpose=intent,
                ttl_minutes=ttl_minutes,
                override_reason=override_reason,
            )

    return {
        "status": "dry_run" if dry_run else "ok",
        "command": "start-task",
        "workspace_id": workspace_id,
        "agent_name": agent_name,
        "intent": intent,
        "dry_run": dry_run,
        "action_id": action_id,
        "requested": {"files": requested_files, "symbols": requested_symbols},
        "overview": overview,
        "open_handoffs": open_handoffs,
        "conflicts": {
            "file_reservations": external_reservations(file_reservations, agent_name),
            "symbol_reservations": external_reservations(symbol_reservations, agent_name),
        },
        "review": review,
        "reservations": {
            "files": file_reservation_result,
            "symbols": symbol_reservation_result,
        },
        "next_action_hint": start_task_hint(dry_run, reserve, review),
    }


def finish_task(
    conn: Connection,
    workspace_id_or_uri: str,
    agent_name: str,
    summary: str,
    intent: str | None = None,
    changed_files: list[dict[str, Any]] | None = None,
    git_commit: str | None = None,
    branch: str | None = None,
    to_agent_name: str | None = None,
    next_steps: list[str] | None = None,
    next_action: str | None = None,
    blocked_on: list[str] | None = None,
    tested_commands: list[str] | None = None,
    remaining_risks: list[str] | None = None,
    reservation_mode: str = "keep",
    ttl_minutes: int | None = 120,
    dry_run: bool = False,
    limit: int = 50,
    session_id: str | None = None,
    session_external_id: str | None = None,
) -> dict[str, Any]:
    workspace_id = require_task_workspace(conn, workspace_id_or_uri)
    files = changed_files or []
    active_files = own_reservations(
        list_active_file_reservations(conn, workspace_id, None)[:limit],
        agent_name,
    )
    active_symbols = own_reservations(
        list_active_symbol_reservations(conn, workspace_id, None)[:limit],
        agent_name,
    )

    action_id = None
    changeset = None
    handoff_id = None
    reservation_updates: dict[str, list[dict[str, Any]]] = {"files": [], "symbols": []}
    if not dry_run:
        action_id = record_agent_action(
            conn,
            workspace_id=workspace_id,
            agent_name=agent_name,
            action_type="task_finish",
            summary=summary,
            intent=intent,
            status="recorded",
            metadata={"source": "finish-task"},
            session_id=session_id,
            session_external_id=session_external_id,
        )
        if files:
            changeset = record_changeset(
                conn,
                workspace_id=workspace_id,
                files=files,
                git_commit=git_commit,
                branch=branch,
                intent=intent,
                summary=summary,
                metadata={"source": "finish-task"},
                session_id=session_id,
                session_external_id=session_external_id,
            )
        handoff_id = record_handoff_summary(
            conn,
            workspace_id=workspace_id,
            from_agent_name=agent_name,
            to_agent_name=to_agent_name,
            summary=summary,
            next_steps=next_steps,
            blocked_on=blocked_on,
            tested_commands=tested_commands,
            remaining_risks=remaining_risks,
            next_action=next_action,
            template="standard",
        )
        reservation_updates = apply_reservation_mode(
            conn,
            workspace_id,
            agent_name,
            active_files,
            active_symbols,
            reservation_mode,
            ttl_minutes,
        )

    return {
        "status": "dry_run" if dry_run else "ok",
        "command": "finish-task",
        "workspace_id": workspace_id,
        "agent_name": agent_name,
        "summary": summary,
        "dry_run": dry_run,
        "action_id": action_id,
        "changeset": changeset,
        "handoff_id": handoff_id,
        "reservation_mode": reservation_mode,
        "reservations_considered": {"files": active_files, "symbols": active_symbols},
        "reservation_updates": reservation_updates,
        "tested_commands": tested_commands or [],
        "remaining_risks": remaining_risks or [],
        "next_action": next_action,
    }


def parse_changed_file(value: str) -> dict[str, Any]:
    known_statuses = {"added", "modified", "deleted", "renamed", "copied"}
    file_path = value.strip()
    status = "modified"
    if ":" in file_path:
        candidate_path, candidate_status = file_path.rsplit(":", 1)
        if candidate_status in known_statuses and candidate_path:
            file_path = candidate_path
            status = candidate_status
    if not file_path:
        raise ValueError("changed file path is required")
    return {"file_path": file_path, "status": status}


def parse_changed_files(values: list[str] | None) -> list[dict[str, Any]]:
    return [parse_changed_file(value) for value in values or []]


def format_task_result_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# {result.get('command')}",
        "",
        f"- Status: `{result.get('status')}`",
        f"- Workspace: `{result.get('workspace_id')}`",
        f"- Agent: `{result.get('agent_name')}`",
    ]
    if result.get("intent"):
        lines.append(f"- Intent: {result.get('intent')}")
    if result.get("summary"):
        lines.append(f"- Summary: {result.get('summary')}")
    if result.get("action_id"):
        lines.append(f"- Action: `{result.get('action_id')}`")
    if result.get("handoff_id"):
        lines.append(f"- Handoff: `{result.get('handoff_id')}`")
    changeset = result.get("changeset")
    if isinstance(changeset, dict) and changeset.get("changeset_id"):
        lines.append(f"- Changeset: `{changeset.get('changeset_id')}`")

    conflicts = result.get("conflicts") if isinstance(result.get("conflicts"), dict) else {}
    if conflicts:
        lines.extend(
            [
                "",
                "## Conflicts",
                "",
                f"- File reservations: `{len(conflicts.get('file_reservations') or [])}`",
                f"- Symbol reservations: `{len(conflicts.get('symbol_reservations') or [])}`",
            ]
        )
    if result.get("next_action_hint"):
        lines.extend(["", "## Next", "", str(result["next_action_hint"])])
    return "\n".join(lines)


def apply_reservation_mode(
    conn: Connection,
    workspace_id: str,
    agent_name: str,
    file_reservations: list[dict[str, Any]],
    symbol_reservations: list[dict[str, Any]],
    reservation_mode: str,
    ttl_minutes: int | None,
) -> dict[str, list[dict[str, Any]]]:
    updates: dict[str, list[dict[str, Any]]] = {"files": [], "symbols": []}
    if reservation_mode == "keep":
        return updates
    for reservation in file_reservations:
        reservation_id = str(reservation["reservation_id"])
        count = (
            release_reservation(
                conn,
                workspace_id,
                reservation_id=reservation_id,
                agent_name=agent_name,
            )
            if reservation_mode == "release"
            else renew_reservation(
                conn,
                workspace_id,
                reservation_id=reservation_id,
                agent_name=agent_name,
                ttl_minutes=ttl_minutes,
            )
        )
        updates["files"].append({"reservation_id": reservation_id, "count": count})
    for reservation in symbol_reservations:
        reservation_id = str(reservation["reservation_id"])
        count = (
            release_symbol_reservation(
                conn,
                workspace_id,
                reservation_id=reservation_id,
                agent_name=agent_name,
            )
            if reservation_mode == "release"
            else renew_symbol_reservation(
                conn,
                workspace_id,
                reservation_id=reservation_id,
                agent_name=agent_name,
                ttl_minutes=ttl_minutes,
            )
        )
        updates["symbols"].append({"reservation_id": reservation_id, "count": count})
    return updates


def require_task_workspace(conn: Connection, workspace_id_or_uri: str) -> str:
    workspace_id = resolve_workspace_id(conn, workspace_id_or_uri)
    if not workspace_id:
        raise ValueError(f"Workspace not found: {workspace_id_or_uri}")
    return workspace_id


def normalized_values(values: list[str] | None) -> list[str]:
    return [value.strip() for value in values or [] if value and value.strip()]


def external_reservations(
    reservations: list[dict[str, Any]],
    agent_name: str,
) -> list[dict[str, Any]]:
    return [item for item in reservations if item.get("agent_name") != agent_name]


def own_reservations(
    reservations: list[dict[str, Any]],
    agent_name: str,
) -> list[dict[str, Any]]:
    return [item for item in reservations if item.get("agent_name") == agent_name]


def start_task_hint(dry_run: bool, reserve: bool, review: dict[str, Any]) -> str:
    assessment = review.get("assessment") if isinstance(review.get("assessment"), dict) else {}
    status = assessment.get("status")
    if status in {"blocked_by_policy", "override_reason_required"}:
        return "Resolve reservation conflicts before editing."
    if dry_run:
        return "Rerun without --dry-run to record intent and optional reservations."
    if reserve:
        return "Proceed with the reserved work and finish with finish-task."
    return "Proceed carefully and reserve files or symbols if collision risk increases."
