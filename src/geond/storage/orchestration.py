from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.cursor import Cursor
from psycopg.types.json import Jsonb

from geond.storage.repository import (
    resolve_workspace_id,
    resolve_workspace_id_cursor,
    upsert_agent,
    upsert_workspace,
)

GOAL_SCHEMA = "geond.goal.v1"
RUN_SCHEMA = "geond.run.v1"
TASK_SCHEMA = "geond.task.v1"
WORKER_SCHEMA = "geond.worker_session.v1"
LEASE_SCHEMA = "geond.task_lease.v1"
COMMAND_SCHEMA = "geond.command_run.v1"
FINDING_SCHEMA = "geond.review_finding.v1"
APPROVAL_SCHEMA = "geond.approval_request.v1"
DECISION_SCHEMA = "geond.decision.v1"
READINESS_SCHEMA = "geond.readiness_report.v1"
BRIEF_SCHEMA = "geond.orchestrator_brief.v1"
RUN_HANDOFF_PACKAGE_SCHEMA = "geond.run_handoff_package.v1"
RUN_SUMMARY_SCHEMA = "geond.run_summary.v1"
TASK_GRAPH_SCHEMA = "geond.task_graph.v1"

ACTIVE_LEASE_STATUSES = {"active", "renewed"}
BLOCKING_FINDING_SEVERITIES = {"P0", "P1"}
HIGH_RISK_LEVELS = {"high", "critical"}


def create_goal(
    conn: Connection,
    workspace_id_or_uri: str,
    title: str,
    summary: str = "",
    status: str = "accepted",
    created_by_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    workspace_id = ensure_workspace(conn, workspace_id_or_uri)
    payload = {
        "workspace_id": workspace_id,
        "title": title,
        "summary": summary,
        "status": status,
        "created_by_agent": created_by_agent,
        "metadata": metadata or {},
    }
    with conn.cursor() as cur:
        hit = idempotency_result(cur, "create_goal", workspace_id, idempotency_key, payload)
        if hit is not None:
            conn.commit()
            return hit
        cur.execute(
            """
            INSERT INTO orchestration_goals (
                workspace_id, title, summary, status, created_by_agent, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING
                id::text, workspace_id::text, title, summary, status, created_by_agent,
                metadata, created_at, updated_at
            """,
            (
                workspace_id,
                title,
                summary,
                status,
                created_by_agent,
                Jsonb(metadata or {}),
            ),
        )
        result = ok_result("goal", goal_row(cur.fetchone()))
        remember_idempotency(cur, "create_goal", workspace_id, idempotency_key, payload, result)
    conn.commit()
    return result


def create_run(
    conn: Connection,
    workspace_id_or_uri: str,
    title: str,
    goal_id: str | None = None,
    risk_level: str = "medium",
    status: str = "active",
    created_by_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    workspace_id = ensure_workspace(conn, workspace_id_or_uri)
    payload = {
        "workspace_id": workspace_id,
        "title": title,
        "goal_id": goal_id,
        "risk_level": risk_level,
        "status": status,
        "created_by_agent": created_by_agent,
        "metadata": metadata or {},
    }
    with conn.cursor() as cur:
        hit = idempotency_result(cur, "create_run", workspace_id, idempotency_key, payload)
        if hit is not None:
            conn.commit()
            return hit
        if goal_id and not goal_exists_cursor(cur, workspace_id, goal_id):
            result = error_result(
                "GOAL_NOT_FOUND",
                "Goal was not found in this workspace.",
                suggested_cli_command="geond goal start <title>",
                related_ids={"goal_id": goal_id, "workspace_id": workspace_id},
            )
            remember_idempotency(cur, "create_run", workspace_id, idempotency_key, payload, result)
            conn.commit()
            return result
        cur.execute(
            """
            INSERT INTO orchestration_runs (
                workspace_id, goal_id, title, risk_level, status, created_by_agent, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING
                id::text, workspace_id::text, goal_id::text, title, risk_level, status,
                created_by_agent, metadata, created_at, updated_at
            """,
            (
                workspace_id,
                goal_id,
                title,
                normalize_risk_level(risk_level),
                status,
                created_by_agent,
                Jsonb(metadata or {}),
            ),
        )
        result = ok_result("run", run_row(cur.fetchone()))
        remember_idempotency(cur, "create_run", workspace_id, idempotency_key, payload, result)
    conn.commit()
    return result


def create_task(
    conn: Connection,
    run_id: str,
    title: str,
    description: str = "",
    status: str = "ready",
    priority: int = 0,
    required_evidence: list[dict[str, Any]] | None = None,
    created_by_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload = {
        "run_id": run_id,
        "title": title,
        "description": description,
        "status": status,
        "priority": priority,
        "required_evidence": required_evidence or [],
        "created_by_agent": created_by_agent,
        "metadata": metadata or {},
    }
    with conn.cursor() as cur:
        run = get_run_row_cursor(cur, run_id)
        if not run:
            result = error_result(
                "RUN_NOT_FOUND",
                "Run was not found.",
                suggested_cli_command="geond run start <workspace> --title <title>",
                related_ids={"run_id": run_id},
            )
            conn.commit()
            return result
        workspace_id = run["workspace_id"]
        hit = idempotency_result(cur, "create_task", workspace_id, idempotency_key, payload)
        if hit is not None:
            conn.commit()
            return hit
        cur.execute(
            """
            INSERT INTO orchestration_tasks (
                workspace_id, run_id, title, description, status, priority,
                required_evidence, created_by_agent, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING
                id::text, workspace_id::text, run_id::text, title, description, status,
                priority, required_evidence, created_by_agent, metadata, created_at, updated_at
            """,
            (
                workspace_id,
                run_id,
                title,
                description,
                status,
                priority,
                Jsonb(required_evidence or []),
                created_by_agent,
                Jsonb(metadata or {}),
            ),
        )
        result = ok_result("task", task_row(cur.fetchone()))
        remember_idempotency(cur, "create_task", workspace_id, idempotency_key, payload, result)
    conn.commit()
    return result


def update_task_state(
    conn: Connection,
    task_id: str,
    status: str,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload = {"task_id": task_id, "status": status, "metadata": metadata or {}}
    with conn.cursor() as cur:
        task = get_task_row_cursor(cur, task_id)
        if not task:
            conn.commit()
            return error_result(
                "TASK_NOT_FOUND", "Task was not found.", related_ids={"task_id": task_id}
            )
        hit = idempotency_result(
            cur, "update_task_state", task["workspace_id"], idempotency_key, payload
        )
        if hit is not None:
            conn.commit()
            return hit
        cur.execute(
            """
            UPDATE orchestration_tasks
            SET status = %s,
                metadata = metadata || %s,
                updated_at = now()
            WHERE id = %s::uuid
            RETURNING
                id::text, workspace_id::text, run_id::text, title, description, status,
                priority, required_evidence, created_by_agent, metadata, created_at, updated_at
            """,
            (status, Jsonb(metadata or {}), task_id),
        )
        result = ok_result("task", task_row(cur.fetchone()))
        remember_idempotency(
            cur, "update_task_state", task["workspace_id"], idempotency_key, payload, result
        )
    conn.commit()
    return result


def create_task_graph(
    conn: Connection,
    run_id: str,
    tasks: list[dict[str, Any]],
    created_by_agent: str | None = "geond-orchestrator",
) -> dict[str, Any]:
    if not task_graph_table_exists(conn):
        return error_result(
            "TASK_GRAPH_SCHEMA_MISSING",
            "Task graph migration 008_orchestration_task_graph is not applied.",
        )

    keys = [str(item.get("key") or "").strip() for item in tasks]
    if any(not key for key in keys):
        return error_result("VALIDATION_ERROR", "Each task graph task requires a key.")
    if len(set(keys)) != len(keys):
        return error_result("VALIDATION_ERROR", "Task graph keys must be unique.")
    known_keys = set(keys)
    for item in tasks:
        to_key = str(item.get("key") or "").strip()
        for from_key in item.get("depends_on") or []:
            from_key = str(from_key).strip()
            if from_key not in known_keys:
                return error_result(
                    "TASK_GRAPH_DEPENDENCY_NOT_FOUND",
                    "Task graph dependency key was not found.",
                    related_ids={"dependency_key": from_key, "task_key": to_key},
                )

    created_tasks: dict[str, dict[str, Any]] = {}
    for item in tasks:
        key = str(item.get("key") or "").strip()
        result = create_task(
            conn,
            run_id,
            str(item.get("title") or ""),
            description=str(item.get("description") or ""),
            status=str(item.get("status") or "ready"),
            priority=int(item.get("priority") or 0),
            required_evidence=item.get("required_evidence") or [],
            created_by_agent=created_by_agent,
            metadata={"graph_key": key, "source": "task_graph"},
            idempotency_key=f"task_graph:{run_id}:task:{key}",
        )
        if result.get("status") != "ok":
            return result
        created_tasks[key] = result["task"]

    edges: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        run = get_run_row_cursor(cur, run_id)
        if not run:
            conn.commit()
            return error_result(
                "RUN_NOT_FOUND",
                "Run was not found.",
                related_ids={"run_id": run_id},
            )
        for item in tasks:
            to_key = str(item.get("key") or "").strip()
            to_task = created_tasks[to_key]
            for from_key in item.get("depends_on") or []:
                from_key = str(from_key).strip()
                from_task = created_tasks[from_key]
                cur.execute(
                    """
                    INSERT INTO orchestration_task_edges (
                        workspace_id, run_id, from_task_id, to_task_id, edge_type, metadata
                    )
                    VALUES (%s, %s, %s, %s, 'blocks', %s)
                    ON CONFLICT (from_task_id, to_task_id, edge_type)
                    DO UPDATE SET metadata = orchestration_task_edges.metadata || EXCLUDED.metadata
                    RETURNING
                        id::text, workspace_id::text, run_id::text,
                        from_task_id::text, to_task_id::text, edge_type,
                        metadata, created_at
                    """,
                    (
                        run["workspace_id"],
                        run_id,
                        from_task["task_id"],
                        to_task["task_id"],
                        Jsonb({"from_key": from_key, "to_key": to_key}),
                    ),
                )
                edges.append(task_edge_row(cur.fetchone()))
    conn.commit()
    return {
        "schema": TASK_GRAPH_SCHEMA,
        "status": "ok",
        "code": None,
        "run_id": run_id,
        "tasks": list(created_tasks.values()),
        "edges": edges,
    }


def list_task_graph(conn: Connection, run_id: str) -> dict[str, Any]:
    if not task_graph_table_exists(conn):
        return {
            "schema": TASK_GRAPH_SCHEMA,
            "status": "ok",
            "code": None,
            "run_id": run_id,
            "tasks": [],
            "edges": [],
            "blocked_tasks": [],
        }
    with conn.cursor() as cur:
        run = get_run_row_cursor(cur, run_id)
        if not run:
            return error_result(
                "RUN_NOT_FOUND",
                "Run was not found.",
                related_ids={"run_id": run_id},
            )
        tasks = list_run_tasks_cursor(cur, run_id)
        cur.execute(
            """
            SELECT
                id::text, workspace_id::text, run_id::text,
                from_task_id::text, to_task_id::text, edge_type,
                metadata, created_at
            FROM orchestration_task_edges
            WHERE run_id = %s::uuid
            ORDER BY created_at, id
            """,
            (run_id,),
        )
        edges = [task_edge_row(row) for row in cur.fetchall()]
    return {
        "schema": TASK_GRAPH_SCHEMA,
        "status": "ok",
        "code": None,
        "run_id": run_id,
        "tasks": tasks,
        "edges": edges,
        "blocked_tasks": get_blocked_task_reasons(conn, run_id).get("blocked_tasks", []),
    }


def get_blocked_task_reasons(conn: Connection, run_id: str) -> dict[str, Any]:
    if not task_graph_table_exists(conn):
        return {
            "schema": TASK_GRAPH_SCHEMA + ".blocked_reasons",
            "status": "ok",
            "code": None,
            "run_id": run_id,
            "blocked_tasks": [],
        }
    with conn.cursor() as cur:
        run = get_run_row_cursor(cur, run_id)
        if not run:
            return error_result(
                "RUN_NOT_FOUND",
                "Run was not found.",
                related_ids={"run_id": run_id},
            )
        cur.execute(
            """
            SELECT
                t.id::text,
                t.title,
                jsonb_agg(
                    jsonb_build_object(
                        'task_id', dep.id::text,
                        'title', dep.title,
                        'status', dep.status,
                        'edge_id', e.id::text
                    )
                    ORDER BY dep.priority DESC, dep.created_at
                ) AS blockers
            FROM orchestration_tasks t
            JOIN orchestration_task_edges e ON e.to_task_id = t.id
            JOIN orchestration_tasks dep ON dep.id = e.from_task_id
            WHERE t.run_id = %s::uuid
              AND t.status = 'ready'
              AND dep.status <> 'done'
              AND e.edge_type = 'blocks'
            GROUP BY t.id, t.title
            ORDER BY t.title
            """,
            (run_id,),
        )
        blocked = [
            {"task_id": row[0], "title": row[1], "blockers": row[2] or []} for row in cur.fetchall()
        ]
    return {
        "schema": TASK_GRAPH_SCHEMA + ".blocked_reasons",
        "status": "ok",
        "code": None,
        "run_id": run_id,
        "blocked_tasks": blocked,
    }


def register_worker_session(
    conn: Connection,
    run_id: str,
    agent_name: str,
    status: str = "active",
    session_external_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload = {
        "run_id": run_id,
        "agent_name": agent_name,
        "status": status,
        "session_external_id": session_external_id,
        "metadata": metadata or {},
    }
    with conn.cursor() as cur:
        run = get_run_row_cursor(cur, run_id)
        if not run:
            conn.commit()
            return error_result(
                "RUN_NOT_FOUND", "Run was not found.", related_ids={"run_id": run_id}
            )
        workspace_id = run["workspace_id"]
        hit = idempotency_result(
            cur, "register_worker_session", workspace_id, idempotency_key, payload
        )
        if hit is not None:
            conn.commit()
            return hit
        worker = insert_worker_session_cursor(
            cur,
            workspace_id=workspace_id,
            run_id=run_id,
            agent_name=agent_name,
            status=status,
            session_external_id=session_external_id,
            metadata=metadata,
        )
        result = ok_result("worker_session", worker)
        remember_idempotency(
            cur, "register_worker_session", workspace_id, idempotency_key, payload, result
        )
    conn.commit()
    return result


def claim_task(
    conn: Connection,
    task_id: str,
    agent_name: str,
    worker_session_id: str | None = None,
    ttl_minutes: int | None = 120,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload = {
        "task_id": task_id,
        "agent_name": agent_name,
        "worker_session_id": worker_session_id,
        "ttl_minutes": ttl_minutes,
        "metadata": metadata or {},
    }
    with conn.cursor() as cur:
        task = get_task_row_cursor(cur, task_id)
        if not task:
            conn.commit()
            return error_result(
                "TASK_NOT_FOUND", "Task was not found.", related_ids={"task_id": task_id}
            )
        workspace_id = task["workspace_id"]
        hit = idempotency_result(cur, "claim_task", workspace_id, idempotency_key, payload)
        if hit is not None:
            conn.commit()
            return hit
        cleanup_expired_task_leases_cursor(cur, task["run_id"])
        task = get_task_row_cursor(cur, task_id)
        active_lease = get_active_task_lease_cursor(cur, task_id)
        if active_lease:
            suggested_command = f"geond worker claim --run {task['run_id']} --agent {agent_name}"
            result = error_result(
                "LEASE_CONFLICT",
                "Task already has an active lease.",
                retryable=True,
                suggested_cli_command=suggested_command,
                related_ids={"task_id": task_id, "lease_id": active_lease["lease_id"]},
            )
            remember_idempotency(cur, "claim_task", workspace_id, idempotency_key, payload, result)
            conn.commit()
            return result
        if not task or task["status"] != "ready":
            result = error_result(
                "TASK_NOT_CLAIMABLE",
                "Task must be in ready status before it can be claimed.",
                suggested_cli_command=f"geond task create {task['run_id']} --title <title>"
                if task
                else None,
                related_ids={"task_id": task_id},
            )
            remember_idempotency(cur, "claim_task", workspace_id, idempotency_key, payload, result)
            conn.commit()
            return result
        worker = (
            get_worker_session_row_cursor(cur, worker_session_id)
            if worker_session_id
            else insert_worker_session_cursor(
                cur,
                workspace_id=workspace_id,
                run_id=task["run_id"],
                agent_name=agent_name,
                metadata={"source": "claim_task"},
            )
        )
        if not worker or worker["run_id"] != task["run_id"]:
            suggested_command = f"geond worker register {task['run_id']} --agent {agent_name}"
            result = error_result(
                "WORKER_NOT_FOUND",
                "Worker session was not found for this run.",
                suggested_cli_command=suggested_command,
                related_ids={"worker_session_id": worker_session_id, "run_id": task["run_id"]},
            )
            remember_idempotency(cur, "claim_task", workspace_id, idempotency_key, payload, result)
            conn.commit()
            return result
        agent_id = upsert_agent(conn, agent_name)
        try:
            cur.execute(
                """
                INSERT INTO task_leases (
                    workspace_id, run_id, task_id, worker_session_id, agent_id,
                    expires_at, metadata
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    CASE
                        WHEN %s::integer IS NULL THEN NULL
                        ELSE now() + make_interval(mins => %s::integer)
                    END,
                    %s
                )
                RETURNING
                    id::text, workspace_id::text, run_id::text, task_id::text,
                    worker_session_id::text, agent_id::text, status, expires_at,
                    released_at, last_heartbeat_at, metadata, created_at, updated_at
                """,
                (
                    workspace_id,
                    task["run_id"],
                    task_id,
                    worker["worker_session_id"],
                    agent_id,
                    ttl_minutes,
                    ttl_minutes,
                    Jsonb(metadata or {}),
                ),
            )
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            suggested_command = f"geond worker claim --run {task['run_id']} --agent {agent_name}"
            return error_result(
                "LEASE_CONFLICT",
                "Task already has an active lease.",
                retryable=True,
                suggested_cli_command=suggested_command,
                related_ids={"task_id": task_id},
            )
        lease = lease_row(cur.fetchone())
        cur.execute(
            """
            UPDATE orchestration_tasks
            SET status = 'claimed', updated_at = now()
            WHERE id = %s::uuid
            RETURNING
                id::text, workspace_id::text, run_id::text, title, description, status,
                priority, required_evidence, created_by_agent, metadata, created_at, updated_at
            """,
            (task_id,),
        )
        task = task_row(cur.fetchone())
        record_agent_action_cursor(
            cur,
            workspace_id=workspace_id,
            agent_id=agent_id,
            action_type="task_claim",
            summary=f"{agent_name} claimed task: {task['title']}",
            intent=task["title"],
            status="recorded",
            metadata={
                "source": "orchestration",
                "run_id": task["run_id"],
                "task_id": task["task_id"],
                "lease_id": lease["lease_id"],
                "worker_session_id": worker["worker_session_id"],
            },
        )
        result = {
            "schema": LEASE_SCHEMA,
            "status": "ok",
            "code": None,
            "lease": lease,
            "task": task,
            "worker_session": worker,
        }
        remember_idempotency(cur, "claim_task", workspace_id, idempotency_key, payload, result)
    conn.commit()
    return result


def renew_task_lease(
    conn: Connection,
    lease_id: str,
    worker_session_id: str | None = None,
    ttl_minutes: int | None = 120,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload = {
        "lease_id": lease_id,
        "worker_session_id": worker_session_id,
        "ttl_minutes": ttl_minutes,
    }
    with conn.cursor() as cur:
        lease = get_task_lease_row_cursor(cur, lease_id)
        workspace_id = lease["workspace_id"] if lease else None
        hit = idempotency_result(cur, "renew_task_lease", workspace_id, idempotency_key, payload)
        if hit is not None:
            conn.commit()
            return hit
        if not lease or not lease_is_active(lease):
            result = error_result(
                "LEASE_EXPIRED", "Lease is not active.", related_ids={"lease_id": lease_id}
            )
            remember_idempotency(
                cur, "renew_task_lease", workspace_id, idempotency_key, payload, result
            )
            conn.commit()
            return result
        if worker_session_id and lease["worker_session_id"] != worker_session_id:
            result = error_result(
                "LEASE_CONFLICT",
                "Lease belongs to another worker session.",
                related_ids={"lease_id": lease_id, "worker_session_id": worker_session_id},
            )
            remember_idempotency(
                cur, "renew_task_lease", workspace_id, idempotency_key, payload, result
            )
            conn.commit()
            return result
        cur.execute(
            """
            UPDATE task_leases
            SET status = 'renewed',
                expires_at = CASE
                    WHEN %s::integer IS NULL THEN NULL
                    ELSE now() + make_interval(mins => %s::integer)
                END,
                last_heartbeat_at = now(),
                updated_at = now()
            WHERE id = %s::uuid
            RETURNING
                id::text, workspace_id::text, run_id::text, task_id::text,
                worker_session_id::text, agent_id::text, status, expires_at,
                released_at, last_heartbeat_at, metadata, created_at, updated_at
            """,
            (ttl_minutes, ttl_minutes, lease_id),
        )
        renewed = lease_row(cur.fetchone())
        cur.execute(
            """
            UPDATE worker_sessions
            SET last_heartbeat_at = now(), updated_at = now()
            WHERE id = %s::uuid
            """,
            (renewed["worker_session_id"],),
        )
        result = ok_result("lease", renewed)
        remember_idempotency(
            cur, "renew_task_lease", workspace_id, idempotency_key, payload, result
        )
    conn.commit()
    return result


def release_task_lease(
    conn: Connection,
    lease_id: str,
    reason: str = "released",
    worker_session_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload = {"lease_id": lease_id, "reason": reason, "worker_session_id": worker_session_id}
    with conn.cursor() as cur:
        lease = get_task_lease_row_cursor(cur, lease_id)
        workspace_id = lease["workspace_id"] if lease else None
        hit = idempotency_result(cur, "release_task_lease", workspace_id, idempotency_key, payload)
        if hit is not None:
            conn.commit()
            return hit
        if not lease or not lease_is_active(lease):
            result = error_result(
                "LEASE_EXPIRED", "Lease is not active.", related_ids={"lease_id": lease_id}
            )
            remember_idempotency(
                cur, "release_task_lease", workspace_id, idempotency_key, payload, result
            )
            conn.commit()
            return result
        if worker_session_id and lease["worker_session_id"] != worker_session_id:
            result = error_result("LEASE_CONFLICT", "Lease belongs to another worker session.")
            remember_idempotency(
                cur, "release_task_lease", workspace_id, idempotency_key, payload, result
            )
            conn.commit()
            return result
        cur.execute(
            """
            UPDATE task_leases
            SET status = %s, released_at = now(), updated_at = now()
            WHERE id = %s::uuid
            RETURNING
                id::text, workspace_id::text, run_id::text, task_id::text,
                worker_session_id::text, agent_id::text, status, expires_at,
                released_at, last_heartbeat_at, metadata, created_at, updated_at
            """,
            (reason or "released", lease_id),
        )
        released = lease_row(cur.fetchone())
        cur.execute(
            """
            UPDATE orchestration_tasks
            SET status = 'ready', updated_at = now()
            WHERE id = %s::uuid
              AND status IN ('claimed', 'executing')
            """,
            (released["task_id"],),
        )
        result = ok_result("lease", released)
        remember_idempotency(
            cur, "release_task_lease", workspace_id, idempotency_key, payload, result
        )
    conn.commit()
    return result


def finish_task_with_handoff(
    conn: Connection,
    lease_id: str,
    summary: str,
    task_status: str = "done",
    tested_commands: list[str] | None = None,
    remaining_risks: list[str] | None = None,
    next_action: str | None = None,
    blocked_on: list[str] | None = None,
    worker_session_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    final_status = "blocked" if task_status == "blocked" else "done"
    payload = {
        "lease_id": lease_id,
        "summary": summary,
        "task_status": final_status,
        "tested_commands": tested_commands or [],
        "remaining_risks": remaining_risks or [],
        "next_action": next_action,
        "blocked_on": blocked_on or [],
        "worker_session_id": worker_session_id,
    }
    with conn.cursor() as cur:
        lease = get_task_lease_row_cursor(cur, lease_id)
        workspace_id = lease["workspace_id"] if lease else None
        hit = idempotency_result(
            cur, "finish_task_with_handoff", workspace_id, idempotency_key, payload
        )
        if hit is not None:
            conn.commit()
            return hit
        if not lease or not lease_is_active(lease):
            result = error_result(
                "LEASE_EXPIRED", "Lease is not active.", related_ids={"lease_id": lease_id}
            )
            remember_idempotency(
                cur, "finish_task_with_handoff", workspace_id, idempotency_key, payload, result
            )
            conn.commit()
            return result
        if worker_session_id and lease["worker_session_id"] != worker_session_id:
            result = error_result("LEASE_CONFLICT", "Lease belongs to another worker session.")
            remember_idempotency(
                cur, "finish_task_with_handoff", workspace_id, idempotency_key, payload, result
            )
            conn.commit()
            return result
        worker = get_worker_session_row_cursor(cur, lease["worker_session_id"])
        task = get_task_row_cursor(cur, lease["task_id"])
        from_agent_id = lease["agent_id"]
        cur.execute(
            """
            UPDATE orchestration_tasks
            SET status = %s, updated_at = now()
            WHERE id = %s::uuid
            RETURNING
                id::text, workspace_id::text, run_id::text, title, description, status,
                priority, required_evidence, created_by_agent, metadata, created_at, updated_at
            """,
            (final_status, lease["task_id"]),
        )
        updated_task = task_row(cur.fetchone())
        cur.execute(
            """
            UPDATE task_leases
            SET status = %s, released_at = now(), updated_at = now()
            WHERE id = %s::uuid
            RETURNING
                id::text, workspace_id::text, run_id::text, task_id::text,
                worker_session_id::text, agent_id::text, status, expires_at,
                released_at, last_heartbeat_at, metadata, created_at, updated_at
            """,
            (final_status, lease_id),
        )
        finished_lease = lease_row(cur.fetchone())
        cur.execute(
            """
            INSERT INTO handoff_summaries (
                workspace_id, from_agent_id, status, summary,
                next_steps, blocked_on, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id::text
            """,
            (
                workspace_id,
                from_agent_id,
                "blocked" if final_status == "blocked" else "open",
                summary,
                Jsonb([next_action] if next_action else []),
                Jsonb(blocked_on or []),
                Jsonb(
                    {
                        "template": "orchestration_worker_handoff",
                        "run_id": lease["run_id"],
                        "task_id": lease["task_id"],
                        "lease_id": lease_id,
                        "worker_session_id": lease["worker_session_id"],
                        "tested_commands": tested_commands or [],
                        "remaining_risks": remaining_risks or [],
                        "next_action": next_action,
                    }
                ),
            ),
        )
        handoff_id = cur.fetchone()[0]
        record_agent_action_cursor(
            cur,
            workspace_id=workspace_id,
            agent_id=from_agent_id,
            action_type="task_finish",
            summary=summary,
            intent=task["title"] if task else None,
            status=final_status,
            metadata={
                "source": "orchestration",
                "run_id": lease["run_id"],
                "task_id": lease["task_id"],
                "lease_id": lease_id,
                "worker_session_id": lease["worker_session_id"],
                "handoff_id": handoff_id,
            },
        )
        result = {
            "schema": TASK_SCHEMA,
            "status": "ok",
            "code": None,
            "task": updated_task,
            "lease": finished_lease,
            "worker_session": worker,
            "handoff_id": handoff_id,
        }
        remember_idempotency(
            cur,
            "finish_task_with_handoff",
            workspace_id,
            idempotency_key,
            payload,
            result,
        )
    conn.commit()
    return result


def record_command_evidence(
    conn: Connection,
    run_id: str,
    command: str,
    task_id: str | None = None,
    worker_session_id: str | None = None,
    purpose: str = "",
    status: str | None = None,
    exit_code: int | None = None,
    stdout_summary: str = "",
    stderr_summary: str = "",
    log_path: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload = {
        "run_id": run_id,
        "task_id": task_id,
        "worker_session_id": worker_session_id,
        "command": command,
        "purpose": purpose,
        "status": status,
        "exit_code": exit_code,
        "stdout_summary": stdout_summary,
        "stderr_summary": stderr_summary,
        "log_path": log_path,
        "metadata": metadata or {},
    }
    with conn.cursor() as cur:
        run = get_run_row_cursor(cur, run_id)
        if not run:
            conn.commit()
            return error_result(
                "RUN_NOT_FOUND", "Run was not found.", related_ids={"run_id": run_id}
            )
        hit = idempotency_result(
            cur, "record_command_evidence", run["workspace_id"], idempotency_key, payload
        )
        if hit is not None:
            conn.commit()
            return hit
        evidence_status = status or (
            "passed" if exit_code == 0 else "failed" if exit_code else "recorded"
        )
        cur.execute(
            """
            INSERT INTO command_evidence (
                workspace_id, run_id, task_id, worker_session_id, command, purpose,
                status, exit_code, stdout_summary, stderr_summary, log_path, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING
                id::text, workspace_id::text, run_id::text, task_id::text,
                worker_session_id::text, command, purpose, status, exit_code,
                stdout_summary, stderr_summary, log_path, metadata, created_at
            """,
            (
                run["workspace_id"],
                run_id,
                task_id,
                worker_session_id,
                command,
                purpose,
                evidence_status,
                exit_code,
                stdout_summary,
                stderr_summary,
                log_path,
                Jsonb(metadata or {}),
            ),
        )
        result = ok_result("command_evidence", command_row(cur.fetchone()))
        remember_idempotency(
            cur, "record_command_evidence", run["workspace_id"], idempotency_key, payload, result
        )
    conn.commit()
    return result


def record_review_finding(
    conn: Connection,
    run_id: str,
    summary: str,
    severity: str = "P2",
    status: str = "open",
    reviewer: str | None = None,
    task_id: str | None = None,
    affected_refs: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    return insert_run_child(
        conn,
        operation="record_review_finding",
        run_id=run_id,
        task_id=task_id,
        payload={
            "summary": summary,
            "severity": severity,
            "status": status,
            "reviewer": reviewer,
            "affected_refs": affected_refs or [],
            "metadata": metadata or {},
        },
        idempotency_key=idempotency_key,
        inserter=lambda cur, run: insert_review_finding_cursor(
            cur,
            run,
            task_id=task_id,
            summary=summary,
            severity=severity,
            status=status,
            reviewer=reviewer,
            affected_refs=affected_refs,
            metadata=metadata,
        ),
    )


def resolve_review_finding(
    conn: Connection,
    finding_id: str,
    status: str,
    reason: str = "",
    resolved_by: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload = {
        "finding_id": finding_id,
        "status": status,
        "reason": reason,
        "resolved_by": resolved_by,
        "metadata": metadata or {},
    }
    resolution_metadata = {
        key: value
        for key, value in {
            "resolution_reason": reason,
            "resolved_by": resolved_by,
            **(metadata or {}),
        }.items()
        if value
    }
    with conn.cursor() as cur:
        finding = get_review_finding_row_cursor(cur, finding_id)
        workspace_id = finding["workspace_id"] if finding else None
        hit = idempotency_result(
            cur, "resolve_review_finding", workspace_id, idempotency_key, payload
        )
        if hit is not None:
            conn.commit()
            return hit
        if not finding:
            result = error_result(
                "FINDING_NOT_FOUND",
                "Review finding was not found.",
                related_ids={"finding_id": finding_id},
            )
            remember_idempotency(
                cur, "resolve_review_finding", workspace_id, idempotency_key, payload, result
            )
            conn.commit()
            return result
        cur.execute(
            """
            UPDATE review_findings
            SET status = %s,
                metadata = metadata || %s,
                resolved_at = now(),
                updated_at = now()
            WHERE id = %s::uuid
            RETURNING
                id::text, workspace_id::text, run_id::text, task_id::text,
                severity, status, summary, reviewer, affected_refs, metadata,
                resolved_at, created_at, updated_at
            """,
            (status, Jsonb(resolution_metadata), finding_id),
        )
        result = ok_result("finding", finding_row(cur.fetchone()))
        remember_idempotency(
            cur, "resolve_review_finding", workspace_id, idempotency_key, payload, result
        )
    conn.commit()
    return result


def record_decision(
    conn: Connection,
    run_id: str,
    decision: str,
    task_id: str | None = None,
    status: str = "accepted",
    reason: str = "",
    decided_by: str | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    return insert_run_child(
        conn,
        operation="record_decision",
        run_id=run_id,
        task_id=task_id,
        payload={
            "decision": decision,
            "status": status,
            "reason": reason,
            "decided_by": decided_by,
            "evidence_refs": evidence_refs or [],
            "metadata": metadata or {},
        },
        idempotency_key=idempotency_key,
        inserter=lambda cur, run: insert_decision_cursor(
            cur,
            run,
            task_id=task_id,
            decision=decision,
            status=status,
            reason=reason,
            decided_by=decided_by,
            evidence_refs=evidence_refs,
            metadata=metadata,
        ),
    )


def request_approval(
    conn: Connection,
    run_id: str,
    reason: str,
    task_id: str | None = None,
    risk_level: str = "high",
    requested_by_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    return insert_run_child(
        conn,
        operation="request_approval",
        run_id=run_id,
        task_id=task_id,
        payload={
            "reason": reason,
            "risk_level": risk_level,
            "requested_by_agent": requested_by_agent,
            "metadata": metadata or {},
        },
        idempotency_key=idempotency_key,
        inserter=lambda cur, run: insert_approval_cursor(
            cur,
            run,
            task_id=task_id,
            reason=reason,
            risk_level=risk_level,
            requested_by_agent=requested_by_agent,
            metadata=metadata,
        ),
    )


def resolve_approval(
    conn: Connection,
    approval_id: str,
    status: str,
    resolved_by: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload = {
        "approval_id": approval_id,
        "status": status,
        "resolved_by": resolved_by,
        "metadata": metadata or {},
    }
    with conn.cursor() as cur:
        approval = get_approval_row_cursor(cur, approval_id)
        workspace_id = approval["workspace_id"] if approval else None
        hit = idempotency_result(cur, "resolve_approval", workspace_id, idempotency_key, payload)
        if hit is not None:
            conn.commit()
            return hit
        if not approval:
            result = error_result(
                "APPROVAL_NOT_FOUND",
                "Approval request was not found.",
                related_ids={"approval_id": approval_id},
            )
            remember_idempotency(
                cur, "resolve_approval", workspace_id, idempotency_key, payload, result
            )
            conn.commit()
            return result
        cur.execute(
            """
            UPDATE approval_requests
            SET status = %s,
                resolved_by = %s,
                metadata = metadata || %s,
                resolved_at = now(),
                updated_at = now()
            WHERE id = %s::uuid
            RETURNING
                id::text, workspace_id::text, run_id::text, task_id::text,
                risk_level, status, reason, requested_by_agent, resolved_by,
                metadata, resolved_at, created_at, updated_at
            """,
            (status, resolved_by, Jsonb(metadata or {}), approval_id),
        )
        result = ok_result("approval", approval_row(cur.fetchone()))
        remember_idempotency(
            cur, "resolve_approval", workspace_id, idempotency_key, payload, result
        )
    conn.commit()
    return result


def get_run(conn: Connection, run_id: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cleanup_expired_task_leases_cursor(cur, run_id)
        run = get_run_row_cursor(cur, run_id)
        if not run:
            return error_result(
                "RUN_NOT_FOUND", "Run was not found.", related_ids={"run_id": run_id}
            )
        return {
            "schema": RUN_SCHEMA,
            "status": "ok",
            "code": None,
            "run": run,
            "tasks": list_tasks_cursor(cur, run_id),
            "workers": list_workers_cursor(cur, run_id),
            "leases": list_leases_cursor(cur, run_id),
            "command_evidence": list_command_evidence_cursor(cur, run_id, limit=50),
            "review_findings": list_review_findings_cursor(cur, run_id, limit=50),
            "approval_requests": list_approvals_cursor(cur, run_id, limit=50),
            "decisions": list_decisions_cursor(cur, run_id, limit=50),
        }


def list_runs(
    conn: Connection,
    workspace_id_or_uri: str,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    workspace_id = resolve_workspace_id(conn, workspace_id_or_uri)
    if not workspace_id:
        return error_result(
            "WORKSPACE_NOT_FOUND",
            "Workspace was not found.",
            related_ids={"workspace_id_or_uri": workspace_id_or_uri},
        )
    with conn.cursor() as cur:
        filters = ["workspace_id = %s::uuid"]
        params: list[Any] = [workspace_id]
        if status:
            filters.append("status = %s")
            params.append(status)
        params.append(limit)
        cur.execute(
            f"""
            SELECT
                id::text, workspace_id::text, goal_id::text, title, risk_level, status,
                created_by_agent, metadata, created_at, updated_at
            FROM orchestration_runs
            WHERE {" AND ".join(filters)}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            tuple(params),
        )
        runs = [run_row(row) for row in cur.fetchall()]
    return {"schema": RUN_SCHEMA + ".list", "status": "ok", "code": None, "runs": runs}


def get_claimable_tasks(
    conn: Connection,
    run_id: str | None = None,
    workspace_id_or_uri: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        workspace_id = None
        if run_id:
            run = get_run_row_cursor(cur, run_id)
            if not run:
                return error_result(
                    "RUN_NOT_FOUND", "Run was not found.", related_ids={"run_id": run_id}
                )
            workspace_id = run["workspace_id"]
            cleanup_expired_task_leases_cursor(cur, run_id)
        elif workspace_id_or_uri:
            workspace_id = resolve_workspace_id_cursor(cur, workspace_id_or_uri)
            if not workspace_id:
                return error_result(
                    "WORKSPACE_NOT_FOUND",
                    "Workspace was not found.",
                    related_ids={"workspace_id_or_uri": workspace_id_or_uri},
                )
            cleanup_expired_task_leases_cursor(cur, workspace_id=workspace_id)
        else:
            return error_result("RUN_NOT_FOUND", "run_id or workspace_id_or_uri is required.")

        filters = ["t.status = 'ready'"]
        params: list[Any] = []
        if run_id:
            filters.append("t.run_id = %s::uuid")
            params.append(run_id)
        if workspace_id:
            filters.append("t.workspace_id = %s::uuid")
            params.append(workspace_id)
        dependency_filter = ""
        if task_graph_table_exists_cursor(cur):
            dependency_filter = """
              AND NOT EXISTS (
                  SELECT 1
                  FROM orchestration_task_edges e
                  JOIN orchestration_tasks dep ON dep.id = e.from_task_id
                  WHERE e.to_task_id = t.id
                    AND e.edge_type = 'blocks'
                    AND dep.status <> 'done'
              )
            """
        params.append(limit)
        cur.execute(
            f"""
            SELECT
                t.id::text, t.workspace_id::text, t.run_id::text, t.title,
                t.description, t.status, t.priority, t.required_evidence,
                t.created_by_agent, t.metadata, t.created_at, t.updated_at
            FROM orchestration_tasks t
            WHERE {" AND ".join(filters)}
              AND NOT EXISTS (
                  SELECT 1 FROM task_leases l
                  WHERE l.task_id = t.id
                    AND l.released_at IS NULL
                    AND l.status IN ('active', 'renewed')
              )
              {dependency_filter}
            ORDER BY t.priority DESC, t.created_at
            LIMIT %s
            """,
            tuple(params),
        )
        tasks = [task_row(row) for row in cur.fetchall()]
    return {"schema": TASK_SCHEMA + ".claimable_list", "status": "ok", "code": None, "tasks": tasks}


def get_readiness_report(conn: Connection, run_id: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cleanup_expired_task_leases_cursor(cur, run_id)
        run = get_run_row_cursor(cur, run_id)
        if not run:
            return error_result(
                "RUN_NOT_FOUND", "Run was not found.", related_ids={"run_id": run_id}
            )
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'done')::int AS done_count,
                COUNT(*)::int AS task_count
            FROM orchestration_tasks
            WHERE run_id = %s::uuid
            """,
            (run_id,),
        )
        done_count, task_count = cur.fetchone()
        cur.execute(
            """
            SELECT COUNT(*)::int
            FROM command_evidence
            WHERE run_id = %s::uuid
            """,
            (run_id,),
        )
        command_count = cur.fetchone()[0]
        cur.execute(
            """
            SELECT id::text, severity, summary
            FROM review_findings
            WHERE run_id = %s::uuid
              AND status = 'open'
              AND upper(severity) = ANY(%s)
            ORDER BY created_at
            """,
            (run_id, sorted(BLOCKING_FINDING_SEVERITIES)),
        )
        blocking_findings = [
            {"finding_id": row[0], "severity": row[1], "summary": row[2]} for row in cur.fetchall()
        ]
        cur.execute(
            """
            SELECT id::text, risk_level, reason
            FROM approval_requests
            WHERE run_id = %s::uuid
              AND status = 'requested'
            ORDER BY created_at
            """,
            (run_id,),
        )
        pending_approvals = [
            {"approval_id": row[0], "risk_level": row[1], "reason": row[2]}
            for row in cur.fetchall()
        ]

    blocking_reasons: list[str] = []
    report_status = "ready"
    if blocking_findings:
        report_status = "not_ready"
        blocking_reasons.append("open P0/P1 review findings")
    elif run["risk_level"] in HIGH_RISK_LEVELS and pending_approvals:
        report_status = "needs_human_approval"
        blocking_reasons.append("pending high-risk approval")
    elif done_count == 0 or command_count == 0:
        report_status = "not_ready"
        if done_count == 0:
            blocking_reasons.append("no completed tasks")
        if command_count == 0:
            blocking_reasons.append("no command evidence")
    elif done_count < task_count:
        report_status = "not_ready"
        blocking_reasons.append("unfinished tasks")

    return {
        "schema": READINESS_SCHEMA,
        "status": report_status,
        "code": None,
        "run_id": run_id,
        "workspace_id": run["workspace_id"],
        "confidence": "medium" if report_status == "ready" else "low",
        "recommended_action": "proceed" if report_status == "ready" else "investigate",
        "blocking_reasons": blocking_reasons,
        "summary": {
            "task_count": task_count,
            "done_task_count": done_count,
            "command_evidence_count": command_count,
            "blocking_finding_count": len(blocking_findings),
            "pending_approval_count": len(pending_approvals),
        },
        "open_findings": blocking_findings,
        "pending_approvals": pending_approvals,
    }


def get_orchestrator_brief(
    conn: Connection,
    workspace_id_or_uri: str,
    limit: int = 25,
) -> dict[str, Any]:
    workspace_id = resolve_workspace_id(conn, workspace_id_or_uri)
    if not workspace_id:
        return error_result(
            "WORKSPACE_NOT_FOUND",
            "Workspace was not found.",
            related_ids={"workspace_id_or_uri": workspace_id_or_uri},
        )
    with conn.cursor() as cur:
        cleanup_expired_task_leases_cursor(cur, workspace_id=workspace_id)
        cur.execute(
            """
            SELECT
                id::text, workspace_id::text, goal_id::text, title, risk_level, status,
                created_by_agent, metadata, created_at, updated_at
            FROM orchestration_runs
            WHERE workspace_id = %s::uuid
              AND status IN ('active', 'planned', 'verifying', 'review', 'blocked')
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (workspace_id, limit),
        )
        runs = [run_row(row) for row in cur.fetchall()]
        claimable = get_claimable_tasks(conn, workspace_id_or_uri=workspace_id, limit=limit)
        cur.execute(
            """
            SELECT COUNT(*)::int
            FROM approval_requests
            WHERE workspace_id = %s::uuid
              AND status = 'requested'
            """,
            (workspace_id,),
        )
        pending_approval_count = cur.fetchone()[0]
        cur.execute(
            """
            SELECT COUNT(*)::int
            FROM review_findings
            WHERE workspace_id = %s::uuid
              AND status = 'open'
            """,
            (workspace_id,),
        )
        open_finding_count = cur.fetchone()[0]
    return {
        "schema": BRIEF_SCHEMA,
        "status": "ok",
        "code": None,
        "workspace_id": workspace_id,
        "active_runs": runs,
        "claimable_tasks": claimable.get("tasks", []),
        "pending_approval_count": pending_approval_count,
        "open_finding_count": open_finding_count,
    }


def get_run_handoff_package(conn: Connection, run_id: str, limit: int = 100) -> dict[str, Any]:
    with conn.cursor() as cur:
        cleanup_expired_task_leases_cursor(cur, run_id)
        run = get_run_row_cursor(cur, run_id)
        if not run:
            return error_result(
                "RUN_NOT_FOUND", "Run was not found.", related_ids={"run_id": run_id}
            )
        package = {
            "schema": RUN_HANDOFF_PACKAGE_SCHEMA,
            "status": "ok",
            "code": None,
            "run": run,
            "tasks": list_tasks_cursor(cur, run_id),
            "workers": list_workers_cursor(cur, run_id),
            "leases": list_leases_cursor(cur, run_id),
            "command_evidence": list_command_evidence_cursor(cur, run_id, limit=limit),
            "review_findings": list_review_findings_cursor(cur, run_id, limit=limit),
            "approval_requests": list_approvals_cursor(cur, run_id, limit=limit),
            "decisions": list_decisions_cursor(cur, run_id, limit=limit),
            "handoffs": list_run_handoffs_cursor(cur, run_id, limit=limit),
        }
    package["readiness"] = get_readiness_report(conn, run_id)
    return package


def summarize_run(conn: Connection, run_id: str) -> dict[str, Any]:
    package = get_run_handoff_package(conn, run_id)
    if package.get("status") == "error":
        return package
    summary = build_run_summary(package)
    markdown = format_run_summary_markdown(package, summary)
    return {
        "schema": RUN_SUMMARY_SCHEMA,
        "status": "ok",
        "code": None,
        "run_id": run_id,
        "summary": summary,
        "markdown": markdown,
    }


def build_run_summary(package: dict[str, Any]) -> dict[str, Any]:
    readiness = package.get("readiness") or {}
    tasks = package.get("tasks") or []
    command_evidence = package.get("command_evidence") or []
    findings = package.get("review_findings") or []
    approvals = package.get("approval_requests") or []
    decisions = package.get("decisions") or []
    handoffs = package.get("handoffs") or []
    completed_tasks = [task for task in tasks if task.get("status") == "done"]
    open_findings = [finding for finding in findings if finding.get("status") == "open"]
    pending_approvals = [
        approval for approval in approvals if approval.get("status") == "requested"
    ]
    next_actions = extract_next_actions(handoffs)
    return {
        "readiness_status": readiness.get("status"),
        "blocking_reasons": readiness.get("blocking_reasons") or [],
        "task_count": len(tasks),
        "completed_task_count": len(completed_tasks),
        "command_evidence_count": len(command_evidence),
        "open_finding_count": len(open_findings),
        "pending_approval_count": len(pending_approvals),
        "decision_count": len(decisions),
        "completed_tasks": compact_tasks(completed_tasks),
        "command_evidence": compact_command_evidence(command_evidence),
        "open_findings": compact_findings(open_findings),
        "pending_approvals": compact_approvals(pending_approvals),
        "decisions": compact_decisions(decisions),
        "next_actions": next_actions,
    }


def format_run_summary_markdown(package: dict[str, Any], summary: dict[str, Any]) -> str:
    run = package.get("run") or {}
    readiness = package.get("readiness") or {}
    lines = [
        f"# {run.get('title') or 'Geond Run Summary'}",
        "",
        f"- Run: `{run.get('run_id')}`",
        f"- Status: `{run.get('status')}`",
        f"- Risk: `{run.get('risk_level')}`",
        f"- Readiness: `{readiness.get('status')}`",
        "",
        "## Blocking Reasons",
    ]
    lines.extend(markdown_list(summary.get("blocking_reasons") or ["none"]))
    lines.extend(["", "## Completed Tasks"])
    lines.extend(
        markdown_list(
            f"{task['title']} (`{task['task_id']}`)" for task in summary.get("completed_tasks", [])
        )
    )
    lines.extend(["", "## Command Evidence"])
    lines.extend(
        markdown_list(
            f"{item['command']} - {item['status']} (`{item['command_evidence_id']}`)"
            for item in summary.get("command_evidence", [])
        )
    )
    lines.extend(["", "## Open Findings"])
    lines.extend(
        markdown_list(
            f"{item['severity']} {item['summary']} (`{item['finding_id']}`)"
            for item in summary.get("open_findings", [])
        )
    )
    lines.extend(["", "## Pending Approvals"])
    lines.extend(
        markdown_list(
            f"{item['risk_level']} {item['reason']} (`{item['approval_id']}`)"
            for item in summary.get("pending_approvals", [])
        )
    )
    lines.extend(["", "## Decisions"])
    lines.extend(
        markdown_list(
            f"{item['decision']} - {item['status']} (`{item['decision_id']}`)"
            for item in summary.get("decisions", [])
        )
    )
    lines.extend(["", "## Next Actions"])
    lines.extend(markdown_list(summary.get("next_actions") or ["none"]))
    return "\n".join(lines).rstrip() + "\n"


def markdown_list(items: Any) -> list[str]:
    values = list(items)
    return [f"- {value}" for value in values] if values else ["- none"]


def compact_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": task.get("task_id"),
            "title": task.get("title"),
            "status": task.get("status"),
        }
        for task in tasks
    ]


def compact_command_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "command_evidence_id": item.get("command_evidence_id"),
            "task_id": item.get("task_id"),
            "command": item.get("command"),
            "status": item.get("status"),
            "exit_code": item.get("exit_code"),
        }
        for item in items
    ]


def compact_findings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "finding_id": item.get("finding_id"),
            "task_id": item.get("task_id"),
            "severity": item.get("severity"),
            "summary": item.get("summary"),
            "status": item.get("status"),
        }
        for item in items
    ]


def compact_approvals(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "approval_id": item.get("approval_id"),
            "task_id": item.get("task_id"),
            "risk_level": item.get("risk_level"),
            "reason": item.get("reason"),
            "status": item.get("status"),
        }
        for item in items
    ]


def compact_decisions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "decision_id": item.get("decision_id"),
            "task_id": item.get("task_id"),
            "decision": item.get("decision"),
            "status": item.get("status"),
            "reason": item.get("reason"),
        }
        for item in items
    ]


def extract_next_actions(handoffs: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for handoff in handoffs:
        metadata = handoff.get("metadata") or {}
        if metadata.get("next_action"):
            actions.append(str(metadata["next_action"]))
        for step in handoff.get("next_steps") or []:
            if step:
                actions.append(str(step))
    return actions


def insert_run_child(
    conn: Connection,
    *,
    operation: str,
    run_id: str,
    task_id: str | None,
    payload: dict[str, Any],
    idempotency_key: str | None,
    inserter: Any,
) -> dict[str, Any]:
    full_payload = {"run_id": run_id, "task_id": task_id, **payload}
    with conn.cursor() as cur:
        run = get_run_row_cursor(cur, run_id)
        if not run:
            conn.commit()
            return error_result(
                "RUN_NOT_FOUND", "Run was not found.", related_ids={"run_id": run_id}
            )
        if task_id and not task_belongs_to_run_cursor(cur, task_id, run_id):
            conn.commit()
            return error_result(
                "TASK_NOT_FOUND",
                "Task was not found in this run.",
                related_ids={"run_id": run_id, "task_id": task_id},
            )
        hit = idempotency_result(cur, operation, run["workspace_id"], idempotency_key, full_payload)
        if hit is not None:
            conn.commit()
            return hit
        result = inserter(cur, run)
        remember_idempotency(
            cur, operation, run["workspace_id"], idempotency_key, full_payload, result
        )
    conn.commit()
    return result


def ensure_workspace(conn: Connection, workspace_id_or_uri: str) -> str:
    workspace_id = resolve_workspace_id(conn, workspace_id_or_uri)
    if workspace_id:
        return workspace_id
    return upsert_workspace(
        conn,
        root_uri=workspace_id_or_uri,
        name=workspace_id_or_uri.rstrip("/").rsplit("/", 1)[-1] or workspace_id_or_uri,
        metadata={"source": "orchestration"},
    )


def normalize_risk_level(value: str) -> str:
    normalized = (value or "medium").strip().lower()
    return normalized if normalized in {"low", "medium", "high", "critical"} else "medium"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def idempotency_result(
    cur: Cursor,
    operation: str,
    workspace_id: str | None,
    idempotency_key: str | None,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    if not idempotency_key:
        return None
    digest = payload_hash(payload)
    cur.execute(
        """
        SELECT payload_hash, response
        FROM idempotency_records
        WHERE operation = %s
          AND idempotency_key = %s
        LIMIT 1
        """,
        (operation, idempotency_key),
    )
    row = cur.fetchone()
    if not row:
        return None
    if row[0] == digest:
        response = dict(row[1])
        response["idempotent_replay"] = True
        return response
    return error_result(
        "IDEMPOTENCY_CONFLICT",
        "The same idempotency key was used with a different payload.",
        retryable=False,
        related_ids={"idempotency_key": idempotency_key, "workspace_id": workspace_id},
    )


def remember_idempotency(
    cur: Cursor,
    operation: str,
    workspace_id: str | None,
    idempotency_key: str | None,
    payload: dict[str, Any],
    response: dict[str, Any],
) -> None:
    if not idempotency_key:
        return
    cur.execute(
        """
        INSERT INTO idempotency_records (
            workspace_id, operation, idempotency_key, payload_hash, response
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (operation, idempotency_key) DO NOTHING
        """,
        (workspace_id, operation, idempotency_key, payload_hash(payload), Jsonb(response)),
    )


def ok_result(key: str, value: dict[str, Any]) -> dict[str, Any]:
    schema = value.get("schema") if isinstance(value, dict) else None
    return {"schema": schema, "status": "ok", "code": None, key: value}


def error_result(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    suggested_cli_command: str | None = None,
    related_ids: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "error",
        "code": code,
        "message": message,
        "retryable": retryable,
        "suggested_cli_command": suggested_cli_command,
        "related_ids": related_ids or {},
    }


def iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def goal_exists_cursor(cur: Cursor, workspace_id: str, goal_id: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM orchestration_goals
        WHERE id = %s::uuid
          AND workspace_id = %s::uuid
        LIMIT 1
        """,
        (goal_id, workspace_id),
    )
    return cur.fetchone() is not None


def get_run_row_cursor(cur: Cursor, run_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, goal_id::text, title, risk_level, status,
            created_by_agent, metadata, created_at, updated_at
        FROM orchestration_runs
        WHERE id = %s::uuid
        LIMIT 1
        """,
        (run_id,),
    )
    row = cur.fetchone()
    return run_row(row) if row else None


def get_task_row_cursor(cur: Cursor, task_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, title, description, status,
            priority, required_evidence, created_by_agent, metadata, created_at, updated_at
        FROM orchestration_tasks
        WHERE id = %s::uuid
        LIMIT 1
        """,
        (task_id,),
    )
    row = cur.fetchone()
    return task_row(row) if row else None


def task_belongs_to_run_cursor(cur: Cursor, task_id: str, run_id: str) -> bool:
    cur.execute(
        "SELECT 1 FROM orchestration_tasks WHERE id = %s::uuid AND run_id = %s::uuid",
        (task_id, run_id),
    )
    return cur.fetchone() is not None


def list_run_tasks_cursor(cur: Cursor, run_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, title, description, status,
            priority, required_evidence, created_by_agent, metadata, created_at, updated_at
        FROM orchestration_tasks
        WHERE run_id = %s::uuid
        ORDER BY priority DESC, created_at
        """,
        (run_id,),
    )
    return [task_row(row) for row in cur.fetchall()]


def task_graph_table_exists(conn: Connection) -> bool:
    with conn.cursor() as cur:
        return task_graph_table_exists_cursor(cur)


def task_graph_table_exists_cursor(cur: Cursor) -> bool:
    cur.execute("SELECT to_regclass('public.orchestration_task_edges')::text")
    return cur.fetchone()[0] == "orchestration_task_edges"


def get_worker_session_row_cursor(
    cur: Cursor, worker_session_id: str | None
) -> dict[str, Any] | None:
    if not worker_session_id:
        return None
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, agent_id::text, agent_name,
            status, session_external_id, last_heartbeat_at, metadata, created_at, updated_at
        FROM worker_sessions
        WHERE id = %s::uuid
        LIMIT 1
        """,
        (worker_session_id,),
    )
    row = cur.fetchone()
    return worker_row(row) if row else None


def get_task_lease_row_cursor(cur: Cursor, lease_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, task_id::text,
            worker_session_id::text, agent_id::text, status, expires_at,
            released_at, last_heartbeat_at, metadata, created_at, updated_at
        FROM task_leases
        WHERE id = %s::uuid
        LIMIT 1
        """,
        (lease_id,),
    )
    row = cur.fetchone()
    return lease_row(row) if row else None


def get_active_task_lease_cursor(cur: Cursor, task_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, task_id::text,
            worker_session_id::text, agent_id::text, status, expires_at,
            released_at, last_heartbeat_at, metadata, created_at, updated_at
        FROM task_leases
        WHERE task_id = %s::uuid
          AND released_at IS NULL
          AND status IN ('active', 'renewed')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (task_id,),
    )
    row = cur.fetchone()
    return lease_row(row) if row else None


def get_approval_row_cursor(cur: Cursor, approval_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, task_id::text,
            risk_level, status, reason, requested_by_agent, resolved_by,
            metadata, resolved_at, created_at, updated_at
        FROM approval_requests
        WHERE id = %s::uuid
        LIMIT 1
        """,
        (approval_id,),
    )
    row = cur.fetchone()
    return approval_row(row) if row else None


def get_review_finding_row_cursor(cur: Cursor, finding_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, task_id::text,
            severity, status, summary, reviewer, affected_refs, metadata,
            resolved_at, created_at, updated_at
        FROM review_findings
        WHERE id = %s::uuid
        LIMIT 1
        """,
        (finding_id,),
    )
    row = cur.fetchone()
    return finding_row(row) if row else None


def cleanup_expired_task_leases_cursor(
    cur: Cursor,
    run_id: str | None = None,
    workspace_id: str | None = None,
) -> int:
    filters = [
        "released_at IS NULL",
        "status IN ('active', 'renewed')",
        "expires_at IS NOT NULL",
        "expires_at <= now()",
    ]
    params: list[Any] = []
    if run_id:
        filters.append("run_id = %s::uuid")
        params.append(run_id)
    if workspace_id:
        filters.append("workspace_id = %s::uuid")
        params.append(workspace_id)
    cur.execute(
        f"""
        UPDATE task_leases
        SET status = 'expired',
            released_at = now(),
            updated_at = now()
        WHERE {" AND ".join(filters)}
        RETURNING task_id::text
        """,
        tuple(params),
    )
    expired_task_ids = [row[0] for row in cur.fetchall()]
    for task_id in expired_task_ids:
        cur.execute(
            """
            UPDATE orchestration_tasks
            SET status = 'ready', updated_at = now()
            WHERE id = %s::uuid
              AND status IN ('claimed', 'executing')
            """,
            (task_id,),
        )
    return len(expired_task_ids)


def insert_worker_session_cursor(
    cur: Cursor,
    *,
    workspace_id: str,
    run_id: str,
    agent_name: str,
    status: str = "active",
    session_external_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    agent_id = upsert_agent(cur.connection, agent_name)
    cur.execute(
        """
        INSERT INTO worker_sessions (
            workspace_id, run_id, agent_id, agent_name, status,
            session_external_id, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING
            id::text, workspace_id::text, run_id::text, agent_id::text, agent_name,
            status, session_external_id, last_heartbeat_at, metadata, created_at, updated_at
        """,
        (
            workspace_id,
            run_id,
            agent_id,
            agent_name,
            status,
            session_external_id,
            Jsonb(metadata or {}),
        ),
    )
    return worker_row(cur.fetchone())


def record_agent_action_cursor(
    cur: Cursor,
    *,
    workspace_id: str,
    agent_id: str | None,
    action_type: str,
    summary: str,
    intent: str | None = None,
    status: str = "recorded",
    metadata: dict[str, Any] | None = None,
) -> str:
    cur.execute(
        """
        INSERT INTO agent_actions (
            workspace_id, agent_id, action_type, intent, status, summary, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (
            workspace_id,
            agent_id,
            action_type,
            intent,
            status,
            summary,
            Jsonb(metadata or {}),
        ),
    )
    return cur.fetchone()[0]


def insert_review_finding_cursor(
    cur: Cursor,
    run: dict[str, Any],
    *,
    task_id: str | None,
    summary: str,
    severity: str,
    status: str,
    reviewer: str | None,
    affected_refs: list[dict[str, Any]] | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    cur.execute(
        """
        INSERT INTO review_findings (
            workspace_id, run_id, task_id, severity, status, summary,
            reviewer, affected_refs, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING
            id::text, workspace_id::text, run_id::text, task_id::text,
            severity, status, summary, reviewer, affected_refs, metadata,
            resolved_at, created_at, updated_at
        """,
        (
            run["workspace_id"],
            run["run_id"],
            task_id,
            severity,
            status,
            summary,
            reviewer,
            Jsonb(affected_refs or []),
            Jsonb(metadata or {}),
        ),
    )
    return ok_result("finding", finding_row(cur.fetchone()))


def insert_decision_cursor(
    cur: Cursor,
    run: dict[str, Any],
    *,
    task_id: str | None,
    decision: str,
    status: str,
    reason: str,
    decided_by: str | None,
    evidence_refs: list[dict[str, Any]] | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    cur.execute(
        """
        INSERT INTO orchestration_decisions (
            workspace_id, run_id, task_id, decision, status, reason,
            decided_by, evidence_refs, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING
            id::text, workspace_id::text, run_id::text, task_id::text, decision,
            status, reason, decided_by, evidence_refs, metadata, created_at
        """,
        (
            run["workspace_id"],
            run["run_id"],
            task_id,
            decision,
            status,
            reason,
            decided_by,
            Jsonb(evidence_refs or []),
            Jsonb(metadata or {}),
        ),
    )
    return ok_result("decision", decision_row(cur.fetchone()))


def insert_approval_cursor(
    cur: Cursor,
    run: dict[str, Any],
    *,
    task_id: str | None,
    reason: str,
    risk_level: str,
    requested_by_agent: str | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    cur.execute(
        """
        INSERT INTO approval_requests (
            workspace_id, run_id, task_id, risk_level, reason,
            requested_by_agent, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING
            id::text, workspace_id::text, run_id::text, task_id::text,
            risk_level, status, reason, requested_by_agent, resolved_by,
            metadata, resolved_at, created_at, updated_at
        """,
        (
            run["workspace_id"],
            run["run_id"],
            task_id,
            normalize_risk_level(risk_level),
            reason,
            requested_by_agent,
            Jsonb(metadata or {}),
        ),
    )
    return ok_result("approval", approval_row(cur.fetchone()))


def list_tasks_cursor(cur: Cursor, run_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, title, description, status,
            priority, required_evidence, created_by_agent, metadata, created_at, updated_at
        FROM orchestration_tasks
        WHERE run_id = %s::uuid
        ORDER BY priority DESC, created_at
        """,
        (run_id,),
    )
    return [task_row(row) for row in cur.fetchall()]


def list_workers_cursor(cur: Cursor, run_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, agent_id::text, agent_name,
            status, session_external_id, last_heartbeat_at, metadata, created_at, updated_at
        FROM worker_sessions
        WHERE run_id = %s::uuid
        ORDER BY last_heartbeat_at DESC
        """,
        (run_id,),
    )
    return [worker_row(row) for row in cur.fetchall()]


def list_leases_cursor(cur: Cursor, run_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, task_id::text,
            worker_session_id::text, agent_id::text, status, expires_at,
            released_at, last_heartbeat_at, metadata, created_at, updated_at
        FROM task_leases
        WHERE run_id = %s::uuid
        ORDER BY created_at DESC
        """,
        (run_id,),
    )
    return [lease_row(row) for row in cur.fetchall()]


def list_command_evidence_cursor(cur: Cursor, run_id: str, limit: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, task_id::text,
            worker_session_id::text, command, purpose, status, exit_code,
            stdout_summary, stderr_summary, log_path, metadata, created_at
        FROM command_evidence
        WHERE run_id = %s::uuid
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (run_id, limit),
    )
    return [command_row(row) for row in cur.fetchall()]


def list_review_findings_cursor(cur: Cursor, run_id: str, limit: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, task_id::text,
            severity, status, summary, reviewer, affected_refs, metadata,
            resolved_at, created_at, updated_at
        FROM review_findings
        WHERE run_id = %s::uuid
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (run_id, limit),
    )
    return [finding_row(row) for row in cur.fetchall()]


def list_approvals_cursor(cur: Cursor, run_id: str, limit: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, task_id::text,
            risk_level, status, reason, requested_by_agent, resolved_by,
            metadata, resolved_at, created_at, updated_at
        FROM approval_requests
        WHERE run_id = %s::uuid
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (run_id, limit),
    )
    return [approval_row(row) for row in cur.fetchall()]


def list_decisions_cursor(cur: Cursor, run_id: str, limit: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id::text, workspace_id::text, run_id::text, task_id::text, decision,
            status, reason, decided_by, evidence_refs, metadata, created_at
        FROM orchestration_decisions
        WHERE run_id = %s::uuid
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (run_id, limit),
    )
    return [decision_row(row) for row in cur.fetchall()]


def list_run_handoffs_cursor(cur: Cursor, run_id: str, limit: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            hs.id::text,
            hs.workspace_id::text,
            from_agent.name,
            to_agent.name,
            hs.to_agent_name,
            hs.status,
            hs.summary,
            hs.next_steps,
            hs.blocked_on,
            hs.metadata,
            hs.closed_at,
            hs.created_at
        FROM handoff_summaries hs
        LEFT JOIN agents from_agent ON from_agent.id = hs.from_agent_id
        LEFT JOIN agents to_agent ON to_agent.id = hs.to_agent_id
        WHERE hs.metadata->>'run_id' = %s
        ORDER BY hs.created_at DESC
        LIMIT %s
        """,
        (run_id, limit),
    )
    return [
        {
            "handoff_id": row[0],
            "workspace_id": row[1],
            "from_agent_name": row[2],
            "to_agent_name": row[3] or row[4],
            "status": row[5],
            "summary": row[6],
            "next_steps": row[7],
            "blocked_on": row[8],
            "metadata": row[9],
            "closed_at": iso(row[10]),
            "created_at": iso(row[11]),
        }
        for row in cur.fetchall()
    ]


def lease_is_active(lease: dict[str, Any]) -> bool:
    return lease.get("status") in ACTIVE_LEASE_STATUSES and lease.get("released_at") is None


def goal_row(row: Any) -> dict[str, Any]:
    return {
        "schema": GOAL_SCHEMA,
        "goal_id": row[0],
        "workspace_id": row[1],
        "title": row[2],
        "summary": row[3],
        "status": row[4],
        "created_by_agent": row[5],
        "metadata": row[6],
        "created_at": iso(row[7]),
        "updated_at": iso(row[8]),
    }


def run_row(row: Any) -> dict[str, Any]:
    return {
        "schema": RUN_SCHEMA,
        "run_id": row[0],
        "workspace_id": row[1],
        "goal_id": row[2],
        "title": row[3],
        "risk_level": row[4],
        "status": row[5],
        "created_by_agent": row[6],
        "metadata": row[7],
        "created_at": iso(row[8]),
        "updated_at": iso(row[9]),
    }


def task_row(row: Any) -> dict[str, Any]:
    return {
        "schema": TASK_SCHEMA,
        "task_id": row[0],
        "workspace_id": row[1],
        "run_id": row[2],
        "title": row[3],
        "description": row[4],
        "status": row[5],
        "priority": row[6],
        "required_evidence": row[7],
        "created_by_agent": row[8],
        "metadata": row[9],
        "created_at": iso(row[10]),
        "updated_at": iso(row[11]),
    }


def task_edge_row(row: Any) -> dict[str, Any]:
    return {
        "schema": TASK_GRAPH_SCHEMA + ".edge",
        "edge_id": row[0],
        "workspace_id": row[1],
        "run_id": row[2],
        "from_task_id": row[3],
        "to_task_id": row[4],
        "edge_type": row[5],
        "metadata": row[6],
        "created_at": iso(row[7]),
    }


def worker_row(row: Any) -> dict[str, Any]:
    return {
        "schema": WORKER_SCHEMA,
        "worker_session_id": row[0],
        "workspace_id": row[1],
        "run_id": row[2],
        "agent_id": row[3],
        "agent_name": row[4],
        "status": row[5],
        "session_external_id": row[6],
        "last_heartbeat_at": iso(row[7]),
        "metadata": row[8],
        "created_at": iso(row[9]),
        "updated_at": iso(row[10]),
    }


def lease_row(row: Any) -> dict[str, Any]:
    return {
        "schema": LEASE_SCHEMA,
        "lease_id": row[0],
        "workspace_id": row[1],
        "run_id": row[2],
        "task_id": row[3],
        "worker_session_id": row[4],
        "agent_id": row[5],
        "status": row[6],
        "expires_at": iso(row[7]),
        "released_at": iso(row[8]),
        "last_heartbeat_at": iso(row[9]),
        "metadata": row[10],
        "created_at": iso(row[11]),
        "updated_at": iso(row[12]),
    }


def command_row(row: Any) -> dict[str, Any]:
    return {
        "schema": COMMAND_SCHEMA,
        "command_evidence_id": row[0],
        "workspace_id": row[1],
        "run_id": row[2],
        "task_id": row[3],
        "worker_session_id": row[4],
        "command": row[5],
        "purpose": row[6],
        "status": row[7],
        "exit_code": row[8],
        "stdout_summary": row[9],
        "stderr_summary": row[10],
        "log_path": row[11],
        "metadata": row[12],
        "created_at": iso(row[13]),
    }


def finding_row(row: Any) -> dict[str, Any]:
    return {
        "schema": FINDING_SCHEMA,
        "finding_id": row[0],
        "workspace_id": row[1],
        "run_id": row[2],
        "task_id": row[3],
        "severity": row[4],
        "status": row[5],
        "summary": row[6],
        "reviewer": row[7],
        "affected_refs": row[8],
        "metadata": row[9],
        "resolved_at": iso(row[10]),
        "created_at": iso(row[11]),
        "updated_at": iso(row[12]),
    }


def approval_row(row: Any) -> dict[str, Any]:
    return {
        "schema": APPROVAL_SCHEMA,
        "approval_id": row[0],
        "workspace_id": row[1],
        "run_id": row[2],
        "task_id": row[3],
        "risk_level": row[4],
        "status": row[5],
        "reason": row[6],
        "requested_by_agent": row[7],
        "resolved_by": row[8],
        "metadata": row[9],
        "resolved_at": iso(row[10]),
        "created_at": iso(row[11]),
        "updated_at": iso(row[12]),
    }


def decision_row(row: Any) -> dict[str, Any]:
    return {
        "schema": DECISION_SCHEMA,
        "decision_id": row[0],
        "workspace_id": row[1],
        "run_id": row[2],
        "task_id": row[3],
        "decision": row[4],
        "status": row[5],
        "reason": row[6],
        "decided_by": row[7],
        "evidence_refs": row[8],
        "metadata": row[9],
        "created_at": iso(row[10]),
    }
