from __future__ import annotations

from typing import Any

from psycopg import Connection

from geond.retrieval.simple import get_symbol_context, make_snippet
from geond.storage.repository import (
    list_active_file_reservations,
    list_active_symbol_reservations,
    list_handoff_summaries,
    list_reservation_events,
    resolve_workspace_id,
)


def list_sessions(conn: Connection, limit: int = 50) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                w.id::text,
                w.root_uri,
                w.name,
                s.id::text,
                s.source,
                s.external_id,
                s.title,
                s.created_at,
                s.updated_at,
                count(m.id) AS message_count
            FROM sessions s
            JOIN workspaces w ON w.id = s.workspace_id
            LEFT JOIN messages m ON m.session_id = s.id
            GROUP BY w.id, w.root_uri, w.name, s.id, s.source, s.external_id, s.title
            ORDER BY s.updated_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return [
        {
            "workspace_id": row[0],
            "workspace_uri": row[1],
            "workspace_name": row[2],
            "session_id": row[3],
            "source": row[4],
            "external_id": row[5],
            "title": row[6],
            "created_at": row[7].isoformat() if row[7] else None,
            "updated_at": row[8].isoformat() if row[8] else None,
            "message_count": row[9],
        }
        for row in rows
    ]


def get_session_resource(
    conn: Connection,
    session_external_id: str,
    limit: int = 50,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                w.id::text,
                w.root_uri,
                w.name,
                s.id::text,
                s.source,
                s.external_id,
                s.title,
                s.metadata
            FROM sessions s
            JOIN workspaces w ON w.id = s.workspace_id
            WHERE s.external_id = %s OR s.id::text = %s
            ORDER BY s.updated_at DESC
            LIMIT 1
            """,
            (session_external_id, session_external_id),
        )
        session = cur.fetchone()
        if not session:
            return {"session_external_id": session_external_id, "messages": []}

        cur.execute(
            """
            SELECT id::text, role, ordinal, content, metadata, created_at
            FROM messages
            WHERE session_id = %s
            ORDER BY ordinal ASC
            LIMIT %s
            """,
            (session[3], limit),
        )
        messages = cur.fetchall()

    return {
        "workspace_id": session[0],
        "workspace_uri": session[1],
        "workspace_name": session[2],
        "session_id": session[3],
        "source": session[4],
        "external_id": session[5],
        "title": session[6],
        "metadata": session[7],
        "messages": [
            {
                "message_id": row[0],
                "role": row[1],
                "ordinal": row[2],
                "snippet": make_snippet(row[3]),
                "metadata": row[4],
                "created_at": row[5].isoformat() if row[5] else None,
            }
            for row in messages
        ],
    }


def get_symbol_resource(conn: Connection, symbol: str, limit: int = 25) -> dict[str, Any]:
    entities = get_symbol_context(conn, symbol, limit=limit)
    return {"symbol": symbol, "entities": entities}


def list_changesets(conn: Connection, limit: int = 50) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                c.id::text,
                w.id::text,
                w.root_uri,
                c.git_commit,
                c.branch,
                c.intent,
                c.summary,
                c.created_at,
                count(DISTINCT cf.id) AS file_count,
                count(DISTINCT celnk.code_entity_id) AS linked_entity_count
            FROM changesets c
            JOIN workspaces w ON w.id = c.workspace_id
            LEFT JOIN change_files cf ON cf.changeset_id = c.id
            LEFT JOIN change_entities celnk ON celnk.changeset_id = c.id
            GROUP BY c.id, w.id, w.root_uri
            ORDER BY c.created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return [
        {
            "changeset_id": row[0],
            "workspace_id": row[1],
            "workspace_uri": row[2],
            "git_commit": row[3],
            "branch": row[4],
            "intent": row[5],
            "summary": row[6],
            "created_at": row[7].isoformat() if row[7] else None,
            "file_count": row[8],
            "linked_entity_count": row[9],
        }
        for row in rows
    ]


def get_workspace_reservations(conn: Connection, workspace_id: str) -> dict[str, Any]:
    resolved_workspace_id = resolve_workspace_id(conn, workspace_id)
    if not resolved_workspace_id:
        return {"workspace_id": workspace_id, "file_reservations": [], "symbol_reservations": []}
    return {
        "workspace_id": resolved_workspace_id,
        "file_reservations": list_active_file_reservations(conn, resolved_workspace_id),
        "symbol_reservations": list_active_symbol_reservations(conn, resolved_workspace_id),
        "recent_events": list_reservation_events(conn, resolved_workspace_id, limit=25),
    }


def get_workspace_handoffs(
    conn: Connection,
    workspace_id: str,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    resolved_workspace_id = resolve_workspace_id(conn, workspace_id)
    if not resolved_workspace_id:
        return {"workspace_id": workspace_id, "handoffs": []}
    return {
        "workspace_id": resolved_workspace_id,
        "handoffs": list_handoff_summaries(
            conn,
            workspace_id_or_uri=resolved_workspace_id,
            status=status,
            limit=limit,
        ),
    }


def get_workspace_timeline(
    conn: Connection,
    workspace_id: str,
    limit: int = 100,
) -> dict[str, Any]:
    resolved_workspace_id = resolve_workspace_id(conn, workspace_id)
    if not resolved_workspace_id:
        return {"workspace_id": workspace_id, "events": []}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, root_uri, name
            FROM workspaces
            WHERE id::text = %s
            LIMIT 1
            """,
            (resolved_workspace_id,),
        )
        workspace = cur.fetchone()

        cur.execute(
            """
            SELECT item_kind, item_id, source, title, occurred_at
            FROM (
                SELECT
                    'session' AS item_kind,
                    s.external_id AS item_id,
                    s.source AS source,
                    s.title AS title,
                    s.updated_at AS occurred_at
                FROM sessions s
                WHERE s.workspace_id = %s
                UNION ALL
                SELECT
                    'agent_action' AS item_kind,
                    aa.id::text AS item_id,
                    a.name AS source,
                    aa.summary AS title,
                    aa.created_at AS occurred_at
                FROM agent_actions aa
                LEFT JOIN agents a ON a.id = aa.agent_id
                WHERE aa.workspace_id = %s
                UNION ALL
                SELECT
                    'file_reservation' AS item_kind,
                    fr.id::text AS item_id,
                    a.name AS source,
                    fr.file_path || ' ' || fr.purpose AS title,
                    fr.created_at AS occurred_at
                FROM file_reservations fr
                LEFT JOIN agents a ON a.id = fr.agent_id
                WHERE fr.workspace_id = %s
                UNION ALL
                SELECT
                    'symbol_reservation' AS item_kind,
                    sr.id::text AS item_id,
                    a.name AS source,
                    coalesce(sr.qualified_name, sr.symbol) || ' ' || sr.purpose AS title,
                    sr.created_at AS occurred_at
                FROM symbol_reservations sr
                LEFT JOIN agents a ON a.id = sr.agent_id
                WHERE sr.workspace_id = %s
                UNION ALL
                SELECT
                    'reservation_event' AS item_kind,
                    re.id::text AS item_id,
                    a.name AS source,
                    re.reservation_kind || ':' || re.action || ' ' || re.subject AS title,
                    re.created_at AS occurred_at
                FROM reservation_events re
                LEFT JOIN agents a ON a.id = re.agent_id
                WHERE re.workspace_id = %s
                UNION ALL
                SELECT
                    'handoff_summary' AS item_kind,
                    hs.id::text AS item_id,
                    a.name AS source,
                    hs.summary AS title,
                    hs.created_at AS occurred_at
                FROM handoff_summaries hs
                LEFT JOIN agents a ON a.id = hs.from_agent_id
                WHERE hs.workspace_id = %s
            ) timeline
            ORDER BY occurred_at DESC
            LIMIT %s
            """,
            (
                workspace[0],
                workspace[0],
                workspace[0],
                workspace[0],
                workspace[0],
                workspace[0],
                limit,
            ),
        )
        rows = cur.fetchall()

    return {
        "workspace_id": workspace[0],
        "workspace_uri": workspace[1],
        "workspace_name": workspace[2],
        "events": [
            {
                "kind": row[0],
                "id": row[1],
                "source": row[2],
                "title": row[3],
                "occurred_at": row[4].isoformat() if row[4] else None,
            }
            for row in rows
        ],
    }


def get_workspace_lineage(
    conn: Connection,
    workspace_id: str,
    limit: int = 100,
) -> dict[str, Any]:
    resolved_workspace_id = resolve_workspace_id(conn, workspace_id)
    if not resolved_workspace_id:
        return {"workspace_id": workspace_id, "nodes": [], "edges": []}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, root_uri, name
            FROM workspaces
            WHERE id::text = %s
            LIMIT 1
            """,
            (resolved_workspace_id,),
        )
        workspace = cur.fetchone()

        cur.execute(
            """
            SELECT
                node_kind,
                node_id,
                source_id,
                target_id,
                source,
                title,
                status,
                metadata,
                occurred_at
            FROM (
                SELECT
                    'session' AS node_kind,
                    s.id::text AS node_id,
                    NULL::text AS source_id,
                    NULL::text AS target_id,
                    s.source AS source,
                    s.title AS title,
                    NULL::text AS status,
                    jsonb_build_object(
                        'external_id', s.external_id,
                        'metadata', s.metadata
                    ) AS metadata,
                    s.updated_at AS occurred_at
                FROM sessions s
                WHERE s.workspace_id = %s
                UNION ALL
                SELECT
                    'agent_action' AS node_kind,
                    aa.id::text AS node_id,
                    aa.session_id::text AS source_id,
                    NULL::text AS target_id,
                    a.name AS source,
                    coalesce(aa.summary, aa.intent, aa.action_type) AS title,
                    aa.status AS status,
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
                    'handoff_summary' AS node_kind,
                    hs.id::text AS node_id,
                    from_agent.name AS source_id,
                    coalesce(to_agent.name, hs.to_agent_name) AS target_id,
                    from_agent.name AS source,
                    hs.summary AS title,
                    hs.status AS status,
                    jsonb_build_object(
                        'next_steps', hs.next_steps,
                        'blocked_on', hs.blocked_on,
                        'metadata', hs.metadata
                    ) AS metadata,
                    hs.created_at AS occurred_at
                FROM handoff_summaries hs
                LEFT JOIN agents from_agent ON from_agent.id = hs.from_agent_id
                LEFT JOIN agents to_agent ON to_agent.id = hs.to_agent_id
                WHERE hs.workspace_id = %s
                UNION ALL
                SELECT
                    'changeset' AS node_kind,
                    c.id::text AS node_id,
                    c.session_id::text AS source_id,
                    NULL::text AS target_id,
                    c.branch AS source,
                    coalesce(NULLIF(c.summary, ''), c.intent, c.git_commit, c.id::text) AS title,
                    NULL::text AS status,
                    jsonb_build_object(
                        'git_commit', c.git_commit,
                        'branch', c.branch,
                        'intent', c.intent,
                        'metadata', c.metadata
                    ) AS metadata,
                    c.created_at AS occurred_at
                FROM changesets c
                WHERE c.workspace_id = %s
                UNION ALL
                SELECT
                    'benchmark_run' AS node_kind,
                    br.id::text AS node_id,
                    NULL::text AS source_id,
                    NULL::text AS target_id,
                    br.provider AS source,
                    coalesce(NULLIF(br.label, ''), br.mode || ' benchmark') AS title,
                    br.mode AS status,
                    jsonb_build_object(
                        'model', br.model,
                        'repeat', br.repeat,
                        'metadata', br.metadata,
                        'result', br.result
                    ) AS metadata,
                    br.created_at AS occurred_at
                FROM benchmark_runs br
                WHERE br.workspace_id = %s
            ) lineage
            ORDER BY occurred_at ASC
            LIMIT %s
            """,
            (
                resolved_workspace_id,
                resolved_workspace_id,
                resolved_workspace_id,
                resolved_workspace_id,
                resolved_workspace_id,
                limit,
            ),
        )
        rows = cur.fetchall()

    nodes = [lineage_node(row) for row in rows]
    nodes.extend(agent_lineage_nodes(nodes))
    edges = explicit_lineage_edges(nodes)
    edges.extend(temporal_lineage_edges(nodes))
    return {
        "workspace_id": workspace[0],
        "workspace_uri": workspace[1],
        "workspace_name": workspace[2],
        "nodes": nodes,
        "edges": edges,
    }


def lineage_node(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": f"{row[0]}:{row[1]}",
        "kind": row[0],
        "raw_id": row[1],
        "source_id": row[2],
        "target_id": row[3],
        "source": row[4],
        "title": row[5],
        "status": row[6],
        "metadata": row[7],
        "occurred_at": row[8].isoformat() if row[8] else None,
    }


def agent_lineage_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = sorted(
        {
            str(value)
            for node in nodes
            for value in (node.get("source"), node.get("source_id"), node.get("target_id"))
            if value and node["kind"] in {"agent_action", "handoff_summary"}
        }
    )
    return [
        {
            "id": f"agent:{name}",
            "kind": "agent",
            "raw_id": name,
            "source_id": None,
            "target_id": None,
            "source": None,
            "title": name,
            "status": None,
            "metadata": {},
            "occurred_at": None,
        }
        for name in names
    ]


def explicit_lineage_edges(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    session_nodes = {node["raw_id"]: node for node in nodes if node["kind"] == "session"}
    agent_nodes: dict[str, str] = {}
    edges: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("source"):
            agent_nodes.setdefault(str(node["source"]), f"agent:{node['source']}")
        if node.get("target_id"):
            agent_nodes.setdefault(str(node["target_id"]), f"agent:{node['target_id']}")
    for node in nodes:
        if node["kind"] in {"agent_action", "changeset"} and node.get("source_id"):
            session = session_nodes.get(node["source_id"])
            if session:
                edges.append(lineage_edge(session["id"], node["id"], "session_contains"))
        if node["kind"] == "handoff_summary":
            if node.get("source_id"):
                edges.append(
                    lineage_edge(agent_nodes[str(node["source_id"])], node["id"], "handoff_from")
                )
            if node.get("target_id"):
                edges.append(
                    lineage_edge(node["id"], agent_nodes[str(node["target_id"])], "handoff_to")
                )
    return edges


def temporal_lineage_edges(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = [node for node in nodes if node.get("occurred_at")]
    return [
        lineage_edge(previous["id"], current["id"], "precedes")
        for previous, current in zip(ordered, ordered[1:], strict=False)
    ]


def lineage_edge(source: str, target: str, kind: str) -> dict[str, str]:
    return {"source": source, "target": target, "kind": kind}
