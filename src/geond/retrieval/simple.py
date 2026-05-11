from __future__ import annotations

from typing import Any

from psycopg import Connection

from geond.redaction import sanitize_text
from geond.storage.embeddings import vector_to_sql

SNIPPET_CHARS = 1200


def search_dev_memory(
    conn: Connection,
    query: str,
    limit: int = 10,
    workspace_uri: str | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    pattern = f"%{query}%"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                w.id::text,
                w.root_uri,
                w.name,
                s.source,
                s.external_id,
                s.title,
                m.id::text,
                m.role,
                m.ordinal,
                m.content,
                ts_rank(
                    to_tsvector('simple', left(m.content, 50000)),
                    plainto_tsquery('simple', %s)
                ) AS rank
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            JOIN workspaces w ON w.id = s.workspace_id
            WHERE (
                m.content ILIKE %s
                OR to_tsvector('simple', left(m.content, 50000)) @@ plainto_tsquery('simple', %s)
            )
              AND (%s::text IS NULL OR w.root_uri = %s::text)
              AND (%s::text IS NULL OR s.source = %s::text)
            ORDER BY rank DESC NULLS LAST, m.created_at DESC
            LIMIT %s
            """,
            (query, pattern, query, workspace_uri, workspace_uri, source, source, limit),
        )
        rows = cur.fetchall()
    return [
        message_result(
            mode="keyword",
            workspace_id=row[0],
            workspace_uri=row[1],
            workspace_name=row[2],
            source=row[3],
            session_external_id=row[4],
            session_title=row[5],
            message_id=row[6],
            role=row[7],
            ordinal=row[8],
            snippet=make_snippet(row[9]),
            rank=float(row[10] or 0),
        )
        for row in rows
    ]


def vector_search_dev_memory(
    conn: Connection,
    query_vector: list[float],
    model: str,
    limit: int = 10,
    workspace_uri: str | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    vector_literal = vector_to_sql(query_vector)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                w.id::text,
                w.root_uri,
                w.name,
                s.source,
                s.external_id,
                s.title,
                m.id::text,
                m.role,
                m.ordinal,
                m.content,
                e.embedding <=> %s::vector AS distance
            FROM embeddings e
            JOIN messages m ON m.id = e.target_id
            JOIN sessions s ON s.id = m.session_id
            JOIN workspaces w ON w.id = s.workspace_id
            WHERE e.target_table = 'messages'
              AND e.model = %s
              AND e.embedding IS NOT NULL
              AND (%s::text IS NULL OR w.root_uri = %s::text)
              AND (%s::text IS NULL OR s.source = %s::text)
            ORDER BY e.embedding <=> %s::vector ASC
            LIMIT %s
            """,
            (
                vector_literal,
                model,
                workspace_uri,
                workspace_uri,
                source,
                source,
                vector_literal,
                limit,
            ),
        )
        rows = cur.fetchall()
    return [
        message_result(
            mode="vector",
            workspace_id=row[0],
            workspace_uri=row[1],
            workspace_name=row[2],
            source=row[3],
            session_external_id=row[4],
            session_title=row[5],
            message_id=row[6],
            role=row[7],
            ordinal=row[8],
            snippet=make_snippet(row[9]),
            distance=float(row[10]),
            score=1.0 - float(row[10]),
        )
        for row in rows
    ]


def hybrid_search_dev_memory(
    conn: Connection,
    query: str,
    query_vector: list[float],
    model: str,
    limit: int = 10,
    workspace_uri: str | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    keyword_results = search_dev_memory(
        conn,
        query,
        limit=limit,
        workspace_uri=workspace_uri,
        source=source,
    )
    vector_results = vector_search_dev_memory(
        conn,
        query_vector,
        model=model,
        limit=limit,
        workspace_uri=workspace_uri,
        source=source,
    )
    merged: dict[str, dict[str, Any]] = {}

    for rank, item in enumerate(keyword_results):
        key = item["message_id"]
        merged[key] = {
            **item,
            "mode": "hybrid",
            "keyword_rank": rank + 1,
            "vector_rank": None,
            "hybrid_score": 1.0 / (rank + 1),
        }

    for rank, item in enumerate(vector_results):
        key = item["message_id"]
        contribution = 1.0 / (rank + 1)
        if key in merged:
            merged[key]["vector_rank"] = rank + 1
            merged[key]["distance"] = item["distance"]
            merged[key]["hybrid_score"] += contribution
        else:
            merged[key] = {
                **item,
                "mode": "hybrid",
                "keyword_rank": None,
                "vector_rank": rank + 1,
                "hybrid_score": contribution,
            }

    return sorted(merged.values(), key=lambda item: item["hybrid_score"], reverse=True)[:limit]


def message_result(
    *,
    mode: str,
    workspace_id: str,
    workspace_uri: str,
    workspace_name: str,
    source: str,
    session_external_id: str,
    session_title: str,
    message_id: str,
    role: str,
    ordinal: int,
    snippet: str,
    **scores: Any,
) -> dict[str, Any]:
    evidence = {
        "kind": "message",
        "workspace_id": workspace_id,
        "workspace_uri": workspace_uri,
        "workspace_name": workspace_name,
        "source": source,
        "session_external_id": session_external_id,
        "session_title": session_title,
        "message_id": message_id,
        "ordinal": ordinal,
    }
    return {
        "mode": mode,
        "workspace_id": workspace_id,
        "workspace_uri": workspace_uri,
        "workspace_name": workspace_name,
        "source": source,
        "session_external_id": session_external_id,
        "session_title": session_title,
        "message_id": message_id,
        "role": role,
        "ordinal": ordinal,
        "snippet": snippet,
        "evidence": evidence,
        **scores,
    }


def explain_change(conn: Connection, file_path: str, limit: int = 10) -> dict[str, Any]:
    pattern = f"%{file_path}%"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT fs.file_uri, fs.content_hash, s.external_id, s.title, fs.metadata
            FROM file_snapshots fs
            LEFT JOIN sessions s ON s.id = fs.session_id
            WHERE fs.file_uri ILIKE %s OR fs.file_path ILIKE %s
            ORDER BY fs.captured_at DESC
            LIMIT %s
            """,
            (pattern, pattern, limit),
        )
        snapshots = cur.fetchall()

        cur.execute(
            """
            SELECT s.external_id, s.title, m.ordinal, m.content
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE m.content ILIKE %s
            ORDER BY m.created_at DESC
            LIMIT %s
            """,
            (pattern, limit),
        )
        messages = cur.fetchall()

    return {
        "file_path": file_path,
        "snapshots": [
            {
                "file_uri": row[0],
                "content_hash": row[1],
                "session_external_id": row[2],
                "session_title": row[3],
                "metadata": row[4],
            }
            for row in snapshots
        ],
        "related_messages": [
            {
                "session_external_id": row[0],
                "session_title": row[1],
                "ordinal": row[2],
                "snippet": make_snippet(row[3]),
            }
            for row in messages
        ],
    }


def get_symbol_context(conn: Connection, symbol: str, limit: int = 10) -> list[dict[str, Any]]:
    pattern = f"%{symbol}%"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT kind, name, qualified_name, file_path, start_line, end_line, signature, metadata
            FROM code_entities
            WHERE name ILIKE %s OR qualified_name ILIKE %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (pattern, pattern, limit),
        )
        rows = cur.fetchall()
    return [
        {
            "kind": row[0],
            "name": row[1],
            "qualified_name": row[2],
            "file_path": row[3],
            "start_line": row[4],
            "end_line": row[5],
            "signature": row[6],
            "metadata": row[7],
        }
        for row in rows
    ]


def make_snippet(content: str, limit: int = SNIPPET_CHARS) -> str:
    sanitized, _ = sanitize_text(content)
    if len(sanitized) <= limit:
        return sanitized
    return sanitized[:limit]
