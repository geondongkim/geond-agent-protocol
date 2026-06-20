from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from psycopg import Connection

from geond_orchestrator import orchestrator, orchestrator_budget, orchestrator_scheduler

DAEMON_SCHEMA = "geond.orchestrator_daemon.v1"
DAEMON_TRACE_SCHEMA = "geond.orchestrator_daemon_trace.v1"
DAEMON_LOCK_SCHEMA = "geond.orchestrator_daemon_lock.v1"
DAEMON_PLAN_JSON = "DAEMON_PLAN.json"
DAEMON_TRACE_JSONL = "DAEMON_TRACE.jsonl"
DAEMON_SUMMARY_MD = "SUMMARY.md"
DAEMON_LOCK_JSON = "DAEMON_LOCK.json"


def run_daemon_once(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    run_id: str | None = None,
    execute: bool = False,
    agents: list[str] | None = None,
    max_actions: int = 5,
    max_workers: int = 1,
    model: str | None = None,
    sandbox: str = "workspace-write",
    timeout_seconds: int = 3600,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    budget_actions: int | None = None,
    budget_spawn_actions: int | None = None,
    budget_tokens: int | None = None,
    budget_cost_usd: float | str | None = None,
    budget_window_hours: int = 24,
    estimate_spawn_tokens: int = 0,
    estimate_spawn_cost_usd: float | str | None = None,
    interval_seconds: int = 60,
    limit: int = 50,
) -> dict[str, Any]:
    daemon_id = new_daemon_id(workspace_id_or_uri=workspace_id_or_uri, run_id=run_id)
    lock = None
    if execute:
        lock = acquire_lock(
            workspace_id_or_uri,
            daemon_id=daemon_id,
            base_dir=base_dir,
            ttl_seconds=max(120, int(interval_seconds or 60) * 2),
        )
        if lock.get("status") != "ok":
            return daemon_payload(
                workspace_id_or_uri=workspace_id_or_uri,
                run_id=run_id,
                daemon_id=daemon_id,
                execute=execute,
                execution_status="blocked",
                status="blocked",
                code=lock.get("code"),
                cycles=[],
                lock=lock,
            )

    cycles: list[dict[str, Any]] = []
    try:
        budget = orchestrator_budget.build_budget_report(
            conn,
            workspace_id_or_uri=workspace_id_or_uri,
            run_id=run_id,
            agents=agents,
            max_actions=max_actions,
            base_dir=base_dir,
            budget_actions=budget_actions,
            budget_spawn_actions=budget_spawn_actions,
            budget_tokens=budget_tokens,
            budget_cost_usd=budget_cost_usd,
            budget_window_hours=budget_window_hours,
            estimate_spawn_tokens=estimate_spawn_tokens,
            estimate_spawn_cost_usd=estimate_spawn_cost_usd,
            limit=limit,
        )
        scheduler_result = None
        stop_reason = None
        if budget.get("decision") == "blocked":
            stop_reason = budget.get("code") or "ORCHESTRATOR_BUDGET_EXCEEDED"
        else:
            scheduler_result = orchestrator_scheduler.drain_scheduler(
                conn,
                workspace_id_or_uri=workspace_id_or_uri,
                run_id=run_id,
                execute=execute,
                agents=agents,
                max_actions=max_actions,
                max_workers=max_workers,
                model=model,
                sandbox=sandbox,
                timeout_seconds=timeout_seconds,
                base_dir=base_dir,
                budget_actions=budget_actions,
                budget_spawn_actions=budget_spawn_actions,
                budget_tokens=budget_tokens,
                budget_cost_usd=budget_cost_usd,
                budget_window_hours=budget_window_hours,
                estimate_spawn_tokens=estimate_spawn_tokens,
                estimate_spawn_cost_usd=estimate_spawn_cost_usd,
                limit=limit,
            )
            stop_reason = cycle_stop_reason(scheduler_result)
        cycle = daemon_cycle(
            index=0,
            budget=budget,
            scheduler_result=scheduler_result,
            stop_reason=stop_reason,
        )
        cycles.append(cycle)
        payload = daemon_payload(
            workspace_id_or_uri=workspace_id_or_uri,
            run_id=run_id,
            daemon_id=daemon_id,
            execute=execute,
            execution_status="blocked" if stop_reason else ("completed" if execute else "preview"),
            status="blocked" if stop_reason else "ok",
            code=stop_reason,
            cycles=cycles,
            lock=lock,
        )
        if execute:
            payload["bundle"] = write_daemon_bundle(payload, base_dir=base_dir, trace_steps=cycles)
        return payload
    finally:
        if execute and lock and lock.get("status") == "ok":
            release_lock(workspace_id_or_uri, daemon_id=daemon_id, base_dir=base_dir)


def run_daemon_loop(
    conn: Connection,
    *,
    workspace_id_or_uri: str,
    run_id: str | None = None,
    execute: bool = False,
    agents: list[str] | None = None,
    max_actions: int = 5,
    max_workers: int = 1,
    model: str | None = None,
    sandbox: str = "workspace-write",
    timeout_seconds: int = 3600,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
    budget_actions: int | None = None,
    budget_spawn_actions: int | None = None,
    budget_tokens: int | None = None,
    budget_cost_usd: float | str | None = None,
    budget_window_hours: int = 24,
    estimate_spawn_tokens: int = 0,
    estimate_spawn_cost_usd: float | str | None = None,
    interval_seconds: int = 60,
    max_cycles: int = 1,
    forever: bool = False,
    limit: int = 50,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    daemon_id = new_daemon_id(workspace_id_or_uri=workspace_id_or_uri, run_id=run_id)
    lock = None
    if execute:
        lock = acquire_lock(
            workspace_id_or_uri,
            daemon_id=daemon_id,
            base_dir=base_dir,
            ttl_seconds=max(120, int(interval_seconds or 60) * 2),
        )
        if lock.get("status") != "ok":
            return daemon_payload(
                workspace_id_or_uri=workspace_id_or_uri,
                run_id=run_id,
                daemon_id=daemon_id,
                execute=execute,
                execution_status="blocked",
                status="blocked",
                code=lock.get("code"),
                cycles=[],
                lock=lock,
            )

    cycles: list[dict[str, Any]] = []
    stop_reason = None
    max_count = None if forever else max(1, int(max_cycles or 1))
    try:
        cycle_index = 0
        while max_count is None or cycle_index < max_count:
            budget = orchestrator_budget.build_budget_report(
                conn,
                workspace_id_or_uri=workspace_id_or_uri,
                run_id=run_id,
                agents=agents,
                max_actions=max_actions,
                base_dir=base_dir,
                budget_actions=budget_actions,
                budget_spawn_actions=budget_spawn_actions,
                budget_tokens=budget_tokens,
                budget_cost_usd=budget_cost_usd,
                budget_window_hours=budget_window_hours,
                estimate_spawn_tokens=estimate_spawn_tokens,
                estimate_spawn_cost_usd=estimate_spawn_cost_usd,
                limit=limit,
            )
            if budget.get("decision") == "blocked":
                cycle = daemon_cycle(
                    index=cycle_index,
                    budget=budget,
                    scheduler_result=None,
                    stop_reason=budget.get("code") or "ORCHESTRATOR_BUDGET_EXCEEDED",
                )
            else:
                scheduler_result = orchestrator_scheduler.drain_scheduler(
                    conn,
                    workspace_id_or_uri=workspace_id_or_uri,
                    run_id=run_id,
                    execute=execute,
                    agents=agents,
                    max_actions=max_actions,
                    max_workers=max_workers,
                    model=model,
                    sandbox=sandbox,
                    timeout_seconds=timeout_seconds,
                    base_dir=base_dir,
                    budget_actions=budget_actions,
                    budget_spawn_actions=budget_spawn_actions,
                    budget_tokens=budget_tokens,
                    budget_cost_usd=budget_cost_usd,
                    budget_window_hours=budget_window_hours,
                    estimate_spawn_tokens=estimate_spawn_tokens,
                    estimate_spawn_cost_usd=estimate_spawn_cost_usd,
                    limit=limit,
                )
                cycle = daemon_cycle(
                    index=cycle_index,
                    budget=budget,
                    scheduler_result=scheduler_result,
                    stop_reason=cycle_stop_reason(scheduler_result),
                )
            cycles.append(cycle)
            stop_reason = cycle.get("stop_reason")
            selected_count = (
                (cycle.get("scheduler_result") or {}).get("budget", {}).get("selected_actions")
                or (cycle.get("budget_report") or {}).get("forecast", {}).get("selected_actions")
                or 0
            )
            if stop_reason or selected_count == 0:
                stop_reason = stop_reason or "NO_APPROVED_ACTION"
                break
            cycle_index += 1
            if max_count is None or cycle_index < max_count:
                sleep_fn(max(0, int(interval_seconds or 0)))
        payload = daemon_payload(
            workspace_id_or_uri=workspace_id_or_uri,
            run_id=run_id,
            daemon_id=daemon_id,
            execute=execute,
            execution_status="blocked" if stop_reason else ("completed" if execute else "preview"),
            status="blocked" if stop_reason else "ok",
            code=stop_reason,
            cycles=cycles,
            lock=lock,
        )
        if execute:
            payload["bundle"] = write_daemon_bundle(payload, base_dir=base_dir, trace_steps=cycles)
        return payload
    finally:
        if execute and lock and lock.get("status") == "ok":
            release_lock(workspace_id_or_uri, daemon_id=daemon_id, base_dir=base_dir)


def build_dashboard_daemon(
    *,
    workspace_id_or_uri: str,
    limit: int = 50,
    base_dir: Path = orchestrator.DEFAULT_MANIFEST_BASE_DIR,
) -> dict[str, Any]:
    root = daemon_root(base_dir, workspace_id_or_uri)
    lock = read_lock(workspace_id_or_uri, base_dir=base_dir)
    bundles = sorted(root.glob("*/DAEMON_PLAN.json"), key=lambda path: path.stat().st_mtime)
    latest = read_json_file(bundles[-1]) if bundles else None
    return {
        "schema": DAEMON_SCHEMA,
        "status": "ok",
        "code": None,
        "workspace_id_or_uri": workspace_id_or_uri,
        "lock": lock,
        "latest": compact_daemon_payload(latest) if latest else None,
        "artifact_refs": [str(path) for path in bundles[-limit:]],
    }


def acquire_lock(
    workspace_id_or_uri: str,
    *,
    daemon_id: str,
    base_dir: Path,
    ttl_seconds: int,
) -> dict[str, Any]:
    path = lock_path(base_dir, workspace_id_or_uri)
    now = datetime.now(UTC)
    existing = read_json_file(path)
    if existing and parse_time(existing.get("expires_at")) > now:
        return {
            "schema": DAEMON_LOCK_SCHEMA,
            "status": "blocked",
            "code": "DAEMON_LOCK_HELD",
            "lock_path": str(path),
            "existing": existing,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = {
        "schema": DAEMON_LOCK_SCHEMA,
        "status": "ok",
        "daemon_id": daemon_id,
        "pid": os.getpid(),
        "acquired_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=max(1, ttl_seconds))).isoformat(),
        "lock_path": str(path),
        "reclaimed": bool(existing),
    }
    path.write_text(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return lock


def release_lock(workspace_id_or_uri: str, *, daemon_id: str, base_dir: Path) -> None:
    path = lock_path(base_dir, workspace_id_or_uri)
    existing = read_json_file(path)
    if existing and existing.get("daemon_id") == daemon_id:
        path.unlink(missing_ok=True)


def read_lock(workspace_id_or_uri: str, *, base_dir: Path) -> dict[str, Any]:
    path = lock_path(base_dir, workspace_id_or_uri)
    lock = read_json_file(path)
    if not lock:
        return {"schema": DAEMON_LOCK_SCHEMA, "status": "none", "lock_path": str(path)}
    lock["active"] = parse_time(lock.get("expires_at")) > datetime.now(UTC)
    return lock


def daemon_cycle(
    *,
    index: int,
    budget: dict[str, Any],
    scheduler_result: dict[str, Any] | None,
    stop_reason: str | None,
) -> dict[str, Any]:
    return {
        "schema": DAEMON_TRACE_SCHEMA,
        "cycle_index": index,
        "budget_status": budget.get("status"),
        "budget_code": budget.get("code"),
        "scheduler_status": (scheduler_result or {}).get("status"),
        "scheduler_code": (scheduler_result or {}).get("code"),
        "stop_reason": stop_reason,
        "budget_report": compact_budget(budget),
        "scheduler_result": compact_scheduler(scheduler_result),
        "created_at": datetime.now(UTC).isoformat(),
    }


def cycle_stop_reason(scheduler_result: dict[str, Any] | None) -> str | None:
    if not scheduler_result:
        return None
    if scheduler_result.get("status") in {"blocked", "failed", "degraded", "error"}:
        return scheduler_result.get("code") or scheduler_result.get("status")
    if scheduler_result.get("execution_status") in {"blocked", "failed", "degraded"}:
        return scheduler_result.get("code") or scheduler_result.get("execution_status")
    if not scheduler_result.get("selected_actions"):
        return "NO_APPROVED_ACTION"
    return None


def daemon_payload(
    *,
    workspace_id_or_uri: str,
    run_id: str | None,
    daemon_id: str,
    execute: bool,
    execution_status: str,
    status: str,
    code: str | None,
    cycles: list[dict[str, Any]],
    lock: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = {
        "schema": DAEMON_SCHEMA,
        "status": status,
        "code": code,
        "execution_status": execution_status,
        "workspace_id_or_uri": workspace_id_or_uri,
        "run_id": run_id,
        "daemon_id": daemon_id,
        "execute": execute,
        "cycle_count": len(cycles),
        "cycles": cycles,
        "lock": lock,
        "stop_reason": code,
    }
    payload["markdown"] = format_daemon_markdown(payload)
    return payload


def compact_daemon_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "code": payload.get("code"),
        "execution_status": payload.get("execution_status"),
        "daemon_id": payload.get("daemon_id"),
        "cycle_count": payload.get("cycle_count"),
        "stop_reason": payload.get("stop_reason"),
        "bundle": payload.get("bundle"),
    }


def compact_budget(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "code": payload.get("code"),
        "decision": payload.get("decision"),
        "forecast": payload.get("forecast"),
        "remaining": payload.get("remaining"),
        "blocking_reasons": payload.get("blocking_reasons") or [],
    }


def compact_scheduler(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "code": payload.get("code"),
        "execution_status": payload.get("execution_status"),
        "selected_count": len(payload.get("selected_actions") or []),
        "executed_count": payload.get("executed_count"),
        "stop_reason": payload.get("stop_reason"),
        "bundle": payload.get("bundle"),
    }


def write_daemon_bundle(
    payload: dict[str, Any],
    *,
    base_dir: Path,
    trace_steps: list[dict[str, Any]],
) -> dict[str, str]:
    bundle_dir = daemon_root(base_dir, str(payload["workspace_id_or_uri"])) / str(
        payload["daemon_id"]
    )
    bundle_dir.mkdir(parents=True, exist_ok=True)
    plan_path = bundle_dir / DAEMON_PLAN_JSON
    trace_path = bundle_dir / DAEMON_TRACE_JSONL
    summary_path = bundle_dir / DAEMON_SUMMARY_MD
    plan_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    with trace_path.open("w", encoding="utf-8") as handle:
        for step in trace_steps:
            handle.write(json.dumps(step, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    summary_path.write_text(payload.get("markdown", ""), encoding="utf-8")
    return {
        "daemon_dir": str(bundle_dir),
        "plan_path": str(plan_path),
        "trace_path": str(trace_path),
        "summary_path": str(summary_path),
    }


def daemon_root(base_dir: Path, workspace_id_or_uri: str) -> Path:
    return base_dir / workspace_key(workspace_id_or_uri) / "daemon"


def lock_path(base_dir: Path, workspace_id_or_uri: str) -> Path:
    return daemon_root(base_dir, workspace_id_or_uri) / DAEMON_LOCK_JSON


def workspace_key(workspace_id_or_uri: str) -> str:
    digest = hashlib.sha256(workspace_id_or_uri.encode("utf-8")).hexdigest()
    return f"workspace-{digest[:12]}"


def new_daemon_id(*, workspace_id_or_uri: str, run_id: str | None) -> str:
    raw = json.dumps(
        {
            "workspace_id_or_uri": workspace_id_or_uri,
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def parse_time(value: object) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.fromtimestamp(0, UTC)
    return datetime.fromtimestamp(0, UTC)


def read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def format_daemon_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Orchestrator Daemon",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Execution: `{payload.get('execution_status')}`",
        f"- Cycles: `{payload.get('cycle_count')}`",
        f"- Stop reason: `{payload.get('stop_reason')}`",
        "",
        "## Cycles",
    ]
    cycles = payload.get("cycles") or []
    if not cycles:
        lines.append("- none")
    for cycle in cycles:
        lines.append(
            "- "
            f"{cycle.get('cycle_index')} budget `{cycle.get('budget_status')}` "
            f"scheduler `{cycle.get('scheduler_status')}` stop `{cycle.get('stop_reason')}`"
        )
    return "\n".join(lines).rstrip() + "\n"
