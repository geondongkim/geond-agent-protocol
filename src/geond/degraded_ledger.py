from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import Connection

from geond.storage import orchestration as orchestration_store

DEGRADED_LEDGER_SCHEMA = "geond.degraded_ledger_event.v1"
DEGRADED_LEDGER_SUMMARY_SCHEMA = "geond.degraded_ledger_summary.v1"
RECONCILE_SCHEMA = "geond.degraded_ledger_reconcile.v1"
LEDGER_FILENAME = "degraded-ledger.jsonl"
PENDING_EVENT_TYPES = {"command_evidence", "handoff"}


def ledger_path(run_id: str, base_dir: Path) -> Path:
    return base_dir / run_id / LEDGER_FILENAME


def append_event(
    *,
    run_id: str,
    base_dir: Path,
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    db_status: str,
    source_command: str = "",
) -> dict[str, Any]:
    event = {
        "schema": DEGRADED_LEDGER_SCHEMA,
        "event_id": str(uuid.uuid4()),
        "run_id": run_id,
        "event_type": event_type,
        "idempotency_key": idempotency_key,
        "payload": payload,
        "db_status": db_status,
        "created_at": datetime.now(UTC).isoformat(),
        "source_command": source_command,
    }
    path = ledger_path(run_id, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def read_events(run_id: str, base_dir: Path) -> list[dict[str, Any]]:
    path = ledger_path(run_id, base_dir)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            events.append(
                {
                    "schema": DEGRADED_LEDGER_SCHEMA,
                    "event_id": "",
                    "run_id": run_id,
                    "event_type": "corrupt_line",
                    "idempotency_key": "",
                    "payload": {"line": line},
                    "db_status": "corrupt",
                    "created_at": "",
                    "source_command": "",
                }
            )
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def pending_events(run_id: str, base_dir: Path) -> list[dict[str, Any]]:
    events = read_events(run_id, base_dir)
    applied_ids = {
        str((event.get("payload") or {}).get("event_id"))
        for event in events
        if event.get("event_type") == "reconcile_applied"
    }
    return [
        event
        for event in events
        if event.get("db_status") == "pending"
        and event.get("event_type") in PENDING_EVENT_TYPES
        and event.get("event_id") not in applied_ids
    ]


def ledger_summary(run_id: str, base_dir: Path) -> dict[str, Any]:
    events = read_events(run_id, base_dir)
    pending = pending_events(run_id, base_dir)
    return {
        "schema": DEGRADED_LEDGER_SUMMARY_SCHEMA,
        "status": "ok",
        "code": None,
        "run_id": run_id,
        "ledger_path": str(ledger_path(run_id, base_dir)),
        "event_count": len(events),
        "pending_count": len(pending),
        "pending_events": pending,
        "degraded": bool(pending),
    }


def reconcile(
    conn: Connection,
    *,
    run_id: str,
    base_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    pending = pending_events(run_id, base_dir)
    results: list[dict[str, Any]] = []
    if dry_run:
        return {
            "schema": RECONCILE_SCHEMA,
            "status": "ok",
            "code": None,
            "run_id": run_id,
            "dry_run": True,
            "pending_count": len(pending),
            "applied_count": 0,
            "results": [
                {"event_id": event.get("event_id"), "action": "would_apply"} for event in pending
            ],
        }

    applied_count = 0
    for event in pending:
        applied = apply_event(conn, event)
        results.append(applied)
        if applied.get("status") == "ok":
            applied_count += 1
            append_event(
                run_id=run_id,
                base_dir=base_dir,
                event_type="reconcile_applied",
                payload={"event_id": event.get("event_id"), "result": applied},
                idempotency_key=f"reconcile:{event.get('event_id')}",
                db_status="applied",
                source_command="geond ledger reconcile",
            )
    return {
        "schema": RECONCILE_SCHEMA,
        "status": "ok" if applied_count == len(pending) else "partial",
        "code": None if applied_count == len(pending) else "RECONCILE_PARTIAL",
        "run_id": run_id,
        "dry_run": False,
        "pending_count": len(pending),
        "applied_count": applied_count,
        "results": results,
    }


def apply_event(conn: Connection, event: dict[str, Any]) -> dict[str, Any]:
    event_type = event.get("event_type")
    payload = event.get("payload") or {}
    idempotency_key = str(event.get("idempotency_key") or event.get("event_id"))
    if event_type == "command_evidence":
        return orchestration_store.record_command_evidence(
            conn,
            payload["run_id"],
            payload["command"],
            task_id=payload.get("task_id"),
            worker_session_id=payload.get("worker_session_id"),
            purpose=payload.get("purpose") or "",
            status=payload.get("status"),
            exit_code=payload.get("exit_code"),
            stdout_summary=payload.get("stdout_summary") or "",
            stderr_summary=payload.get("stderr_summary") or "",
            log_path=payload.get("log_path"),
            metadata=payload.get("metadata") or {},
            idempotency_key=idempotency_key,
        )
    if event_type == "handoff":
        return orchestration_store.finish_task_with_handoff(
            conn,
            payload["lease_id"],
            summary=payload["summary"],
            task_status=payload.get("task_status") or "done",
            tested_commands=payload.get("tested_commands") or [],
            remaining_risks=payload.get("remaining_risks") or [],
            next_action=payload.get("next_action"),
            blocked_on=payload.get("blocked_on") or [],
            worker_session_id=payload.get("worker_session_id"),
            idempotency_key=idempotency_key,
        )
    return {
        "status": "error",
        "code": "LEDGER_EVENT_UNSUPPORTED",
        "message": f"Unsupported degraded ledger event type: {event_type}",
    }


def format_reconcile_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Degraded Ledger Reconcile",
        "",
        f"- Run: `{result.get('run_id')}`",
        f"- Status: `{result.get('status')}`",
        f"- Dry run: `{result.get('dry_run')}`",
        f"- Pending: `{result.get('pending_count')}`",
        f"- Applied: `{result.get('applied_count')}`",
        "",
        "## Results",
    ]
    items = result.get("results") or []
    if not items:
        lines.append("- none")
    else:
        for item in items:
            lines.append(f"- {item.get('event_id') or item.get('code')}: `{item.get('status')}`")
    return "\n".join(lines).rstrip() + "\n"
