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


def get_dashboard_workspaces(conn: Connection, limit: int = 100) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT w.id::text, w.root_uri, w.name, w.metadata, w.created_at,
                   COALESCE((
                       SELECT jsonb_agg(
                           jsonb_build_object(
                               'alias_uri', wa.alias_uri,
                               'reason', wa.reason,
                               'last_seen_at', wa.last_seen_at
                           )
                           ORDER BY wa.last_seen_at DESC
                       )
                       FROM workspace_aliases wa
                       WHERE wa.workspace_id = w.id
                   ), '[]'::jsonb) AS aliases,
                   COALESCE((
                       SELECT count(*)
                       FROM sessions s
                       WHERE s.workspace_id = w.id
                   ), 0) AS session_count,
                   COALESCE((
                       SELECT count(*)
                       FROM messages m
                       JOIN sessions s ON s.id = m.session_id
                       WHERE s.workspace_id = w.id
                   ), 0) AS message_count,
                   COALESCE((
                       SELECT count(*)
                       FROM changesets c
                       WHERE c.workspace_id = w.id
                   ), 0) AS changeset_count,
                   COALESCE((
                       SELECT count(*)
                       FROM handoff_summaries hs
                       WHERE hs.workspace_id = w.id AND hs.status = 'open'
                   ), 0) AS open_handoff_count,
                   COALESCE((
                       SELECT count(*)
                       FROM file_reservations fr
                       WHERE fr.workspace_id = w.id
                         AND fr.released_at IS NULL
                         AND (fr.expires_at IS NULL OR fr.expires_at > now())
                   ), 0) + COALESCE((
                       SELECT count(*)
                       FROM symbol_reservations sr
                       WHERE sr.workspace_id = w.id
                         AND sr.released_at IS NULL
                         AND (sr.expires_at IS NULL OR sr.expires_at > now())
                   ), 0) AS active_claim_count,
                   COALESCE((
                       SELECT array_agg(DISTINCT s.source ORDER BY s.source)
                       FROM sessions s
                       WHERE s.workspace_id = w.id
                   ), ARRAY[]::text[]) AS session_sources,
                   (
                       SELECT max(ts)
                       FROM (
                           SELECT w.created_at AS ts
                           UNION ALL
                           SELECT s.updated_at FROM sessions s WHERE s.workspace_id = w.id
                           UNION ALL
                           SELECT m.created_at
                           FROM messages m
                           JOIN sessions s ON s.id = m.session_id
                           WHERE s.workspace_id = w.id
                           UNION ALL
                           SELECT c.created_at FROM changesets c WHERE c.workspace_id = w.id
                           UNION ALL
                           SELECT hs.created_at
                           FROM handoff_summaries hs
                           WHERE hs.workspace_id = w.id
                       ) latest
                   ) AS latest_activity_at
            FROM workspaces w
            ORDER BY latest_activity_at DESC NULLS LAST, w.name ASC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return {
        "status": "ok",
        "workspaces": [dashboard_workspace_summary(row) for row in rows],
    }


def dashboard_workspace_summary(row: tuple[Any, ...]) -> dict[str, Any]:
    sources = list(row[11] or [])
    agents = sorted({dashboard_session_agent(source, {}) for source in sources})
    return {
        "workspace_id": row[0],
        "workspace_uri": row[1],
        "workspace_name": row[2],
        "metadata": row[3] or {},
        "created_at": row[4].isoformat() if row[4] else None,
        "aliases": row[5] or [],
        "session_count": int(row[6] or 0),
        "message_count": int(row[7] or 0),
        "changeset_count": int(row[8] or 0),
        "open_handoff_count": int(row[9] or 0),
        "active_claim_count": int(row[10] or 0),
        "session_sources": sources,
        "agents": agents,
        "latest_activity_at": row[12].isoformat() if row[12] else None,
    }


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


def get_dashboard_project_activity(
    conn: Connection,
    workspace_id_or_uri: str,
    limit: int = 120,
) -> dict[str, Any]:
    workspace = resolve_dashboard_workspace(conn, workspace_id_or_uri)
    if workspace is None:
        return {
            "workspace_id": workspace_id_or_uri,
            "status": "not_found",
            "files": [],
        }
    workspace_id, workspace_uri, workspace_name = workspace
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH paths AS (
                SELECT file_path FROM code_entities
                WHERE workspace_id = %s AND file_path IS NOT NULL AND file_path <> ''
                UNION
                SELECT cf.file_path
                FROM change_files cf
                JOIN changesets c ON c.id = cf.changeset_id
                WHERE c.workspace_id = %s AND cf.file_path IS NOT NULL AND cf.file_path <> ''
                UNION
                SELECT fr.file_path FROM file_reservations fr
                WHERE fr.workspace_id = %s AND fr.file_path IS NOT NULL AND fr.file_path <> ''
                UNION
                SELECT fs.file_path FROM file_snapshots fs
                WHERE fs.workspace_id = %s AND fs.file_path IS NOT NULL AND fs.file_path <> ''
            )
            SELECT p.file_path,
                   COALESCE((
                       SELECT count(*)
                       FROM code_entities ce
                       WHERE ce.workspace_id = %s AND ce.file_path = p.file_path
                   ), 0) AS symbol_count,
                   COALESCE((
                       SELECT count(DISTINCT c.id)
                       FROM change_files cf
                       JOIN changesets c ON c.id = cf.changeset_id
                       WHERE c.workspace_id = %s AND cf.file_path = p.file_path
                   ), 0) AS changeset_count,
                   (
                       SELECT max(c.created_at)
                       FROM change_files cf
                       JOIN changesets c ON c.id = cf.changeset_id
                       WHERE c.workspace_id = %s AND cf.file_path = p.file_path
                   ) AS latest_changed_at,
                   COALESCE((
                       SELECT count(*)
                       FROM file_reservations fr
                       WHERE fr.workspace_id = %s
                         AND fr.file_path = p.file_path
                         AND fr.released_at IS NULL
                         AND (fr.expires_at IS NULL OR fr.expires_at > now())
                   ), 0) AS active_file_claims,
                   COALESCE((
                       SELECT array_agg(DISTINCT a.name ORDER BY a.name)
                       FROM file_reservations fr
                       LEFT JOIN agents a ON a.id = fr.agent_id
                       WHERE fr.workspace_id = %s
                         AND fr.file_path = p.file_path
                         AND fr.released_at IS NULL
                         AND (fr.expires_at IS NULL OR fr.expires_at > now())
                   ), ARRAY[]::text[]) AS active_agents
            FROM paths p
            ORDER BY active_file_claims DESC,
                     latest_changed_at DESC NULLS LAST,
                     changeset_count DESC,
                     p.file_path ASC
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
                workspace_id,
                limit,
            ),
        )
        rows = cur.fetchall()
    files = []
    for row in rows:
        status = "active" if row[4] else "changed" if row[2] else "indexed"
        files.append(
            {
                "file_path": row[0],
                "symbol_count": int(row[1] or 0),
                "changeset_count": int(row[2] or 0),
                "latest_changed_at": row[3].isoformat() if row[3] else None,
                "active_file_claims": int(row[4] or 0),
                "active_agents": list(row[5] or []),
                "status": status,
            }
        )
    return {
        "workspace_id": workspace_id,
        "workspace_uri": workspace_uri,
        "workspace_name": workspace_name,
        "status": "ok",
        "files": files,
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
                   max(m.created_at) AS latest_message_at,
                   count(m.id) FILTER (
                       WHERE lower(coalesce(m.role, '')) IN ('user', 'human')
                   ) AS user_message_count,
                   count(m.id) FILTER (
                       WHERE lower(coalesce(m.role, '')) = 'assistant'
                   ) AS assistant_message_count,
                   count(m.id) FILTER (
                       WHERE lower(coalesce(m.role, '')) = 'metadata_or_text'
                   ) AS captured_prompt_count,
                   count(m.id) FILTER (
                       WHERE lower(coalesce(m.role, '')) NOT IN (
                           'user', 'human', 'assistant', 'metadata_or_text'
                       )
                   ) AS technical_message_count
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
                SELECT session_id::text, id::text, role, ordinal, metadata, created_at
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
        message_count = int(row[9] or 0)
        role_counts = {
            "user": int(row[11] or 0),
            "agent": int(row[12] or 0),
            "captured": int(row[13] or 0),
            "technical": int(row[14] or 0),
        }
        if readable_messages:
            conversation_signal = "readable"
        elif session_messages:
            conversation_signal = "technical_recent"
        elif message_count:
            conversation_signal = "unreadable"
        else:
            conversation_signal = "empty"
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
                "message_count": message_count,
                "latest_message_at": row[10].isoformat() if row[10] else None,
                "role_counts": role_counts,
                "user_message_count": role_counts["user"],
                "assistant_message_count": role_counts["agent"],
                "captured_prompt_count": role_counts["captured"],
                "technical_message_count": role_counts["technical"],
                "conversation_signal": conversation_signal,
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
    metadata = row[7] or {}
    agent_name = row[2]
    if row[0] == "session" and not agent_name:
        agent_name = dashboard_session_agent(row[4] or "", metadata.get("metadata") or {})
    return {
        "kind": row[0],
        "id": row[1],
        "agent_name": agent_name,
        "title": row[3],
        "status": row[4],
        "artifact_type": row[5],
        "artifact_id": row[6],
        "metadata": metadata,
        "occurred_at": row[8].isoformat() if row[8] else None,
    }
