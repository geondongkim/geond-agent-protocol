from __future__ import annotations

from typing import Any

from psycopg import Connection, Error

from geond.storage.repository import (
    list_active_file_reservations,
    list_active_symbol_reservations,
    list_handoff_summaries,
    resolve_workspace_id,
)
from geond.storage.resources import get_workspace_lineage


def get_agent_activity_events(
    conn: Connection,
    workspace_id_or_uri: str,
    limit: int = 100,
) -> dict[str, Any]:
    workspace = resolve_dashboard_workspace(conn, workspace_id_or_uri)
    if workspace is None:
        return {
            "workspace_id": workspace_id_or_uri,
            "events": [],
        }
    workspace_id, workspace_uri, workspace_name = workspace
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT kind, item_id, agent_name, title, status, artifact_type,
                   artifact_id, metadata, occurred_at
            FROM (
                SELECT
                    'session' AS kind,
                    s.id::text AS item_id,
                    NULL::text AS agent_name,
                    s.title AS title,
                    s.source AS status,
                    'session' AS artifact_type,
                    s.id::text AS artifact_id,
                    jsonb_build_object('external_id', s.external_id, 'metadata', s.metadata)
                        AS metadata,
                    s.updated_at AS occurred_at
                FROM sessions s
                WHERE s.workspace_id = %s
                UNION ALL
                SELECT
                    'agent_action' AS kind,
                    aa.id::text AS item_id,
                    a.name AS agent_name,
                    coalesce(NULLIF(aa.summary, ''), aa.intent, aa.action_type) AS title,
                    aa.status AS status,
                    'agent_action' AS artifact_type,
                    aa.id::text AS artifact_id,
                    jsonb_build_object(
                        'action_type', aa.action_type,
                        'intent', aa.intent,
                        'metadata', aa.metadata
                    ) AS metadata,
                    aa.created_at AS occurred_at
                FROM agent_actions aa
                LEFT JOIN agents a ON a.id = aa.agent_id
                WHERE aa.workspace_id = %s
                UNION ALL
                SELECT
                    'file_reservation' AS kind,
                    fr.id::text AS item_id,
                    a.name AS agent_name,
                    fr.file_path || ' ' || fr.purpose AS title,
                    CASE
                        WHEN fr.released_at IS NOT NULL THEN 'released'
                        WHEN fr.expires_at IS NOT NULL AND fr.expires_at <= now() THEN 'expired'
                        ELSE 'active'
                    END AS status,
                    'file_reservation' AS artifact_type,
                    fr.id::text AS artifact_id,
                    jsonb_build_object(
                        'file_path', fr.file_path,
                        'purpose', fr.purpose,
                        'expires_at', fr.expires_at,
                        'released_at', fr.released_at,
                        'metadata', fr.metadata
                    ) AS metadata,
                    fr.created_at AS occurred_at
                FROM file_reservations fr
                LEFT JOIN agents a ON a.id = fr.agent_id
                WHERE fr.workspace_id = %s
                UNION ALL
                SELECT
                    'symbol_reservation' AS kind,
                    sr.id::text AS item_id,
                    a.name AS agent_name,
                    coalesce(sr.qualified_name, sr.symbol) || ' ' || sr.purpose AS title,
                    CASE
                        WHEN sr.released_at IS NOT NULL THEN 'released'
                        WHEN sr.expires_at IS NOT NULL AND sr.expires_at <= now() THEN 'expired'
                        ELSE 'active'
                    END AS status,
                    'symbol_reservation' AS artifact_type,
                    sr.id::text AS artifact_id,
                    jsonb_build_object(
                        'symbol', sr.symbol,
                        'qualified_name', sr.qualified_name,
                        'file_path', sr.file_path,
                        'purpose', sr.purpose,
                        'expires_at', sr.expires_at,
                        'released_at', sr.released_at,
                        'metadata', sr.metadata
                    ) AS metadata,
                    sr.created_at AS occurred_at
                FROM symbol_reservations sr
                LEFT JOIN agents a ON a.id = sr.agent_id
                WHERE sr.workspace_id = %s
                UNION ALL
                SELECT
                    'reservation_event' AS kind,
                    re.id::text AS item_id,
                    a.name AS agent_name,
                    re.reservation_kind || ':' || re.action || ' ' || re.subject AS title,
                    re.action AS status,
                    re.reservation_kind || '_reservation' AS artifact_type,
                    re.reservation_id::text AS artifact_id,
                    jsonb_build_object(
                        'subject', re.subject,
                        'reservation_kind', re.reservation_kind,
                        'metadata', re.metadata
                    ) AS metadata,
                    re.created_at AS occurred_at
                FROM reservation_events re
                LEFT JOIN agents a ON a.id = re.agent_id
                WHERE re.workspace_id = %s
                UNION ALL
                SELECT
                    'handoff_summary' AS kind,
                    hs.id::text AS item_id,
                    a.name AS agent_name,
                    hs.summary AS title,
                    hs.status AS status,
                    'handoff_summary' AS artifact_type,
                    hs.id::text AS artifact_id,
                    jsonb_build_object(
                        'to_agent_name', coalesce(to_agent.name, hs.to_agent_name),
                        'next_steps', hs.next_steps,
                        'blocked_on', hs.blocked_on,
                        'closed_at', hs.closed_at,
                        'metadata', hs.metadata
                    ) AS metadata,
                    hs.created_at AS occurred_at
                FROM handoff_summaries hs
                LEFT JOIN agents a ON a.id = hs.from_agent_id
                LEFT JOIN agents to_agent ON to_agent.id = hs.to_agent_id
                WHERE hs.workspace_id = %s
                UNION ALL
                SELECT
                    'changeset' AS kind,
                    c.id::text AS item_id,
                    NULL::text AS agent_name,
                    coalesce(NULLIF(c.summary, ''), c.intent, c.git_commit, c.id::text) AS title,
                    c.branch AS status,
                    'changeset' AS artifact_type,
                    c.id::text AS artifact_id,
                    jsonb_build_object(
                        'git_commit', c.git_commit,
                        'intent', c.intent,
                        'metadata', c.metadata
                    ) AS metadata,
                    c.created_at AS occurred_at
                FROM changesets c
                WHERE c.workspace_id = %s
                UNION ALL
                SELECT
                    'benchmark_run' AS kind,
                    br.id::text AS item_id,
                    NULL::text AS agent_name,
                    coalesce(NULLIF(br.label, ''), br.mode || ' benchmark') AS title,
                    br.mode AS status,
                    'benchmark_run' AS artifact_type,
                    br.id::text AS artifact_id,
                    jsonb_build_object(
                        'provider', br.provider,
                        'model', br.model,
                        'repeat', br.repeat,
                        'metadata', br.metadata,
                        'result', br.result
                    ) AS metadata,
                    br.created_at AS occurred_at
                FROM benchmark_runs br
                WHERE br.workspace_id = %s
            ) events
            ORDER BY occurred_at DESC
            LIMIT %s
            """,
            (
                workspace_id,
                workspace_id,
                workspace_id,
                workspace_id,
                workspace_id,
                workspace_id,
                workspace_id,
                workspace_id,
                limit,
            ),
        )
        rows = cur.fetchall()
    return {
        "workspace_id": workspace_id,
        "workspace_uri": workspace_uri,
        "workspace_name": workspace_name,
        "events": [activity_event(row) for row in rows],
    }


def get_dashboard_overview(
    conn: Connection,
    workspace_id_or_uri: str,
    limit: int = 25,
) -> dict[str, Any]:
    workspace = resolve_dashboard_workspace(conn, workspace_id_or_uri)
    if workspace is None:
        return {
            "workspace_id": workspace_id_or_uri,
            "status": "not_found",
            "counts": {},
            "recent_activity": [],
        }
    workspace_id, workspace_uri, workspace_name = workspace
    counts = dashboard_counts(conn, workspace_id)
    lineage = get_workspace_lineage(conn, workspace_id, limit=max(limit, 25))
    activity = get_agent_activity_events(conn, workspace_id, limit=limit)
    active_files = list_active_file_reservations(conn, workspace_id)
    active_symbols = list_active_symbol_reservations(conn, workspace_id)
    open_handoffs = list_handoff_summaries(conn, workspace_id, status="open", limit=limit)
    return {
        "workspace_id": workspace_id,
        "workspace_uri": workspace_uri,
        "workspace_name": workspace_name,
        "status": "ok",
        "counts": counts,
        "recent_activity": activity["events"],
        "active_reservations": {
            "files": active_files,
            "symbols": active_symbols,
        },
        "open_handoffs": open_handoffs,
        "lineage": {
            "node_count": len(lineage.get("nodes", [])),
            "edge_count": len(lineage.get("edges", [])),
        },
    }


def get_dashboard_sessions(
    conn: Connection,
    workspace_id_or_uri: str,
    limit: int = 30,
    message_limit: int = 4,
) -> dict[str, Any]:
    workspace = resolve_dashboard_workspace(conn, workspace_id_or_uri)
    if workspace is None:
        return {
            "workspace_id": workspace_id_or_uri,
            "status": "not_found",
            "sessions": [],
        }
    workspace_id, workspace_uri, workspace_name = workspace
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id::text, s.source, s.external_id, s.title, s.metadata,
                   s.started_at, s.ended_at, s.created_at, s.updated_at,
                   count(m.id) AS message_count,
                   max(m.created_at) AS latest_message_at
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            WHERE s.workspace_id = %s
            GROUP BY s.id
            ORDER BY coalesce(max(m.created_at), s.updated_at, s.created_at) DESC
            LIMIT %s
            """,
            (workspace_id, limit),
        )
        session_rows = cur.fetchall()

        messages_by_session: dict[str, list[dict[str, Any]]] = {}
        readable_by_session: dict[str, list[dict[str, Any]]] = {}
        session_ids = [row[0] for row in session_rows]
        if session_ids:
            cur.execute(
                """
                SELECT session_id::text, id::text, role, ordinal,
                       left(content, 700) AS content, metadata, created_at
                FROM (
                    SELECT m.*,
                           row_number() OVER (
                               PARTITION BY m.session_id
                               ORDER BY m.ordinal DESC
                           ) AS message_rank
                    FROM messages m
                    WHERE m.session_id = ANY(%s::uuid[])
                ) ranked
                WHERE message_rank <= %s
                ORDER BY session_id, ordinal ASC
                """,
                (session_ids, message_limit),
            )
            for row in cur.fetchall():
                message = {
                    "message_id": row[1],
                    "role": row[2],
                    "ordinal": row[3],
                    "content": row[4],
                    "metadata": row[5],
                    "created_at": row[6].isoformat() if row[6] else None,
                }
                messages_by_session.setdefault(row[0], []).append(message)

            candidate_limit = min(max(message_limit * 4, 20), 80)
            cur.execute(
                """
                SELECT session_id::text, id::text, role, ordinal, metadata, created_at
                FROM (
                    SELECT m.*,
                           row_number() OVER (
                               PARTITION BY m.session_id
                               ORDER BY m.ordinal DESC
                           ) AS message_rank
                    FROM messages m
                    WHERE m.session_id = ANY(%s::uuid[])
                      AND m.role NOT IN ('metadata', 'assistant_or_tool')
                ) ranked
                WHERE message_rank <= %s
                ORDER BY session_id, ordinal ASC
                """,
                (session_ids, candidate_limit),
            )
            for row in cur.fetchall():
                content, content_unavailable = fetch_dashboard_message_content(conn, row[1])
                message = {
                    "message_id": row[1],
                    "role": row[2],
                    "ordinal": row[3],
                    "content": content,
                    "metadata": row[4],
                    "created_at": row[5].isoformat() if row[5] else None,
                    "content_unavailable": content_unavailable,
                }
                if is_readable_dashboard_message(message):
                    readable_by_session.setdefault(row[0], []).append(message)

    sessions = []
    for row in session_rows:
        metadata = row[4] or {}
        session_messages = messages_by_session.get(row[0], [])
        readable_messages = readable_by_session.get(row[0], [])
        sessions.append(
            {
                "session_id": row[0],
                "source": row[1],
                "external_id": row[2],
                "title": row[3],
                "agent_name": dashboard_session_agent(row[1], metadata),
                "metadata": metadata,
                "started_at": row[5].isoformat() if row[5] else None,
                "ended_at": row[6].isoformat() if row[6] else None,
                "created_at": row[7].isoformat() if row[7] else None,
                "updated_at": row[8].isoformat() if row[8] else None,
                "message_count": int(row[9] or 0),
                "latest_message_at": row[10].isoformat() if row[10] else None,
                "messages": session_messages[-message_limit:],
                "readable_messages": readable_messages[-message_limit:],
                "readable_excerpt_count": len(readable_messages),
                "inspected_message_count": len(session_messages),
            }
        )
    return {
        "workspace_id": workspace_id,
        "workspace_uri": workspace_uri,
        "workspace_name": workspace_name,
        "status": "ok",
        "sessions": sessions,
    }


def is_readable_dashboard_message(message: dict[str, Any]) -> bool:
    if message.get("content_unavailable"):
        return False
    role = str(message.get("role") or "").lower()
    content = str(message.get("content") or "").strip()
    if not content or role == "metadata":
        return False
    if content.startswith("toolInvocationSerialized"):
        return False
    if content.startswith("thinking"):
        return False
    if "toolInvocationSerialized" in content:
        return False
    if "".join(content.split()).isdigit():
        return False
    context_prefixes = (
        "<attachments>",
        "<context>",
        "<environment_info>",
        "<workspace_info>",
        "<todoList>",
        "<reminderInstructions>",
    )
    if content.startswith(context_prefixes):
        return False
    return "The following browser pages are currently shared with you" not in content


def fetch_dashboard_message_content(conn: Connection, message_id: str) -> tuple[str, bool]:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT left(content, 700) FROM messages WHERE id::text = %s",
                (message_id,),
            )
            row = cur.fetchone()
    except Error:
        conn.rollback()
        return "[stored content unavailable]", True
    return (row[0] if row else ""), False


def dashboard_session_agent(source: str, metadata: dict[str, Any]) -> str:
    originator = str(metadata.get("originator") or "").lower()
    normalized_source = source.lower()
    if "codex" in normalized_source or "codex" in originator:
        return "codex"
    if "claude" in normalized_source or "claude" in originator:
        return "claude"
    if "copilot" in normalized_source or "vscode" in normalized_source:
        return "copilot"
    if originator:
        return originator
    return normalized_source or "unknown"


def resolve_dashboard_workspace(
    conn: Connection,
    workspace_id_or_uri: str,
) -> tuple[str, str, str] | None:
    workspace_id = resolve_workspace_id(conn, workspace_id_or_uri)
    if not workspace_id:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, root_uri, name
            FROM workspaces
            WHERE id::text = %s
            LIMIT 1
            """,
            (workspace_id,),
        )
        row = cur.fetchone()
    return row if row else None


def dashboard_counts(conn: Connection, workspace_id: str) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT count(*) FROM sessions WHERE workspace_id = %s) AS sessions,
                (
                    SELECT count(*)
                    FROM agent_actions
                    WHERE workspace_id = %s
                ) AS agent_actions,
                (
                    SELECT count(*)
                    FROM file_reservations
                    WHERE workspace_id = %s
                      AND released_at IS NULL
                      AND (expires_at IS NULL OR expires_at > now())
                ) AS active_file_reservations,
                (
                    SELECT count(*)
                    FROM symbol_reservations
                    WHERE workspace_id = %s
                      AND released_at IS NULL
                      AND (expires_at IS NULL OR expires_at > now())
                ) AS active_symbol_reservations,
                (
                    SELECT count(*)
                    FROM handoff_summaries
                    WHERE workspace_id = %s AND status = 'open'
                ) AS open_handoffs,
                (SELECT count(*) FROM changesets WHERE workspace_id = %s) AS changesets,
                (SELECT count(*) FROM benchmark_runs WHERE workspace_id = %s) AS benchmark_runs,
                (
                    SELECT count(DISTINCT agent_id)
                    FROM (
                        SELECT agent_id FROM agent_actions WHERE workspace_id = %s
                        UNION ALL
                        SELECT agent_id FROM file_reservations WHERE workspace_id = %s
                        UNION ALL
                        SELECT agent_id FROM symbol_reservations WHERE workspace_id = %s
                        UNION ALL
                        SELECT from_agent_id AS agent_id
                        FROM handoff_summaries
                        WHERE workspace_id = %s
                    ) agent_refs
                    WHERE agent_id IS NOT NULL
                ) AS agents
            """,
            (
                workspace_id,
                workspace_id,
                workspace_id,
                workspace_id,
                workspace_id,
                workspace_id,
                workspace_id,
                workspace_id,
                workspace_id,
                workspace_id,
                workspace_id,
            ),
        )
        row = cur.fetchone()
    names = [
        "sessions",
        "agent_actions",
        "active_file_reservations",
        "active_symbol_reservations",
        "open_handoffs",
        "changesets",
        "benchmark_runs",
        "agents",
    ]
    return {name: int(value or 0) for name, value in zip(names, row, strict=True)}


def activity_event(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "kind": row[0],
        "id": row[1],
        "agent_name": row[2],
        "title": row[3],
        "status": row[4],
        "artifact_type": row[5],
        "artifact_id": row[6],
        "metadata": row[7],
        "occurred_at": row[8].isoformat() if row[8] else None,
    }
