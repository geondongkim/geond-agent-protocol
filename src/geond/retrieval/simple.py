from __future__ import annotations

from typing import Any

from psycopg import Connection

from geond.config import Settings
from geond.redaction import sanitize_text
from geond.retrieval.evidence import evidence_ref
from geond.retrieval.narrative import (
    summarize_changeset as summarize_changeset_narrative,
)
from geond.retrieval.narrative import (
    summarize_explain_change as summarize_explain_change_narrative,
)
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
    evidence = evidence_ref(
        "message",
        target_id=message_id,
        workspace_id=workspace_id,
        workspace_uri=workspace_uri,
        source=source,
        locator={"session_external_id": session_external_id, "ordinal": ordinal},
        metadata={"workspace_name": workspace_name, "session_title": session_title},
        workspace_name=workspace_name,
        session_external_id=session_external_id,
        session_title=session_title,
        message_id=message_id,
        ordinal=ordinal,
    )
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


def explain_change(
    conn: Connection,
    file_path: str,
    limit: int = 10,
    *,
    include_narrative: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    pattern = f"%{file_path}%"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                fs.id::text,
                fs.workspace_id::text,
                fs.file_uri,
                fs.file_path,
                fs.content_hash,
                s.external_id,
                s.title,
                fs.metadata
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
            SELECT
                w.id::text,
                w.root_uri,
                s.source,
                s.external_id,
                s.title,
                m.id::text,
                m.ordinal,
                m.content
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            JOIN workspaces w ON w.id = s.workspace_id
            WHERE m.content ILIKE %s
            ORDER BY m.created_at DESC
            LIMIT %s
            """,
            (pattern, limit),
        )
        messages = cur.fetchall()

        cur.execute(
            """
            SELECT
                c.id::text,
                c.workspace_id::text,
                w.root_uri,
                c.git_commit,
                c.branch,
                c.intent,
                c.summary,
                c.created_at,
                cf.id::text,
                cf.file_path,
                cf.status,
                cf.additions,
                cf.deletions,
                cf.metadata,
                count(ce.id) AS linked_entity_count
            FROM change_files cf
            JOIN changesets c ON c.id = cf.changeset_id
            JOIN workspaces w ON w.id = c.workspace_id
            LEFT JOIN change_entities celnk ON celnk.change_file_id = cf.id
            LEFT JOIN code_entities ce ON ce.id = celnk.code_entity_id
            WHERE cf.file_path ILIKE %s
            GROUP BY c.id, w.root_uri, cf.id
            ORDER BY c.created_at DESC
            LIMIT %s
            """,
            (pattern, limit),
        )
        changesets = cur.fetchall()

        cur.execute(
            """
            SELECT DISTINCT
                ce.id::text,
                ce.workspace_id::text,
                ce.kind,
                ce.name,
                ce.qualified_name,
                ce.file_path,
                ce.start_line,
                ce.end_line,
                ce.signature,
                c.id::text,
                c.summary,
                cf.file_path,
                cf.status,
                celnk.match_type,
                celnk.confidence,
                celnk.metadata
            FROM change_files cf
            JOIN changesets c ON c.id = cf.changeset_id
            JOIN change_entities celnk ON celnk.change_file_id = cf.id
            JOIN code_entities ce ON ce.id = celnk.code_entity_id
            WHERE cf.file_path ILIKE %s
            ORDER BY c.id::text, ce.start_line NULLS LAST, ce.qualified_name NULLS LAST
            LIMIT %s
            """,
            (pattern, max(limit * 5, limit)),
        )
        touched_entities = cur.fetchall()

    result: dict[str, Any] = {
        "file_path": file_path,
        "snapshots": [
            {
                "snapshot_id": row[0],
                "workspace_id": row[1],
                "file_uri": row[2],
                "file_path": row[3],
                "content_hash": row[4],
                "session_external_id": row[5],
                "session_title": row[6],
                "metadata": row[7],
                "evidence": evidence_ref(
                    "file_snapshot",
                    target_id=row[0],
                    workspace_id=row[1],
                    locator={"file_uri": row[2], "file_path": row[3]},
                    snapshot_id=row[0],
                    file_uri=row[2],
                    file_path=row[3],
                ),
            }
            for row in snapshots
        ],
        "related_messages": [
            {
                "workspace_id": row[0],
                "workspace_uri": row[1],
                "source": row[2],
                "session_external_id": row[3],
                "session_title": row[4],
                "message_id": row[5],
                "ordinal": row[6],
                "snippet": make_snippet(row[7]),
                "evidence": evidence_ref(
                    "message",
                    target_id=row[5],
                    workspace_id=row[0],
                    workspace_uri=row[1],
                    source=row[2],
                    locator={"session_external_id": row[3], "ordinal": row[6]},
                    session_external_id=row[3],
                    message_id=row[5],
                    ordinal=row[6],
                ),
            }
            for row in messages
        ],
        "changesets": [
            {
                "changeset_id": row[0],
                "workspace_id": row[1],
                "workspace_uri": row[2],
                "git_commit": row[3],
                "branch": row[4],
                "intent": row[5],
                "summary": row[6],
                "created_at": row[7].isoformat() if row[7] else None,
                "change_file_id": row[8],
                "file_path": row[9],
                "status": row[10],
                "additions": row[11],
                "deletions": row[12],
                "metadata": row[13],
                "linked_entity_count": row[14],
                "evidence": evidence_ref(
                    "changeset",
                    target_id=row[0],
                    workspace_id=row[1],
                    workspace_uri=row[2],
                    locator={
                        "change_file_id": row[8],
                        "file_path": row[9],
                        "git_commit": row[3],
                    },
                    metadata={"status": row[10], "file_metadata": row[13]},
                    changeset_id=row[0],
                    change_file_id=row[8],
                    file_path=row[9],
                ),
            }
            for row in changesets
        ],
        "touched_entities": [
            {
                "entity_id": row[0],
                "workspace_id": row[1],
                "kind": row[2],
                "name": row[3],
                "qualified_name": row[4],
                "file_path": row[5],
                "start_line": row[6],
                "end_line": row[7],
                "signature": row[8],
                "changeset_id": row[9],
                "changeset_summary": row[10],
                "change_file_path": row[11],
                "change_status": row[12],
                "match_type": row[13],
                "confidence": row[14],
                "metadata": row[15],
                "evidence": evidence_ref(
                    "code_entity",
                    target_id=row[0],
                    workspace_id=row[1],
                    locator={
                        "qualified_name": row[4],
                        "file_path": row[5],
                        "start_line": row[6],
                        "end_line": row[7],
                        "changeset_id": row[9],
                    },
                    metadata={
                        "change_file_path": row[11],
                        "match_type": row[13],
                        "confidence": row[14],
                        "link_metadata": row[15],
                    },
                    entity_id=row[0],
                    qualified_name=row[4],
                    file_path=row[5],
                    changeset_id=row[9],
                ),
            }
            for row in touched_entities
        ],
    }

    if include_narrative:
        result["narrative"] = summarize_explain_change_narrative(result, settings=settings)
    return result


def get_changeset_detail(
    conn: Connection,
    changeset_ref: str,
    *,
    include_narrative: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Look up one changeset by its UUID or by git commit prefix.

    Returns a structured record with files, touched code entities, and a
    `geond.evidence.v1` evidence ref. Set `include_narrative=True` to attach a
    deterministic narrative summary that cites the evidence refs.
    """

    ref = (changeset_ref or "").strip()
    if not ref:
        raise ValueError("changeset_ref is required")

    with conn.cursor() as cur:
        is_uuid = len(ref) == 36 and ref.count("-") == 4
        if is_uuid:
            cur.execute(
                """
                SELECT
                    c.id::text,
                    c.workspace_id::text,
                    w.root_uri,
                    c.git_commit,
                    c.branch,
                    c.intent,
                    c.summary,
                    c.metadata,
                    c.created_at
                FROM changesets c
                JOIN workspaces w ON w.id = c.workspace_id
                WHERE c.id = %s::uuid
                """,
                (ref,),
            )
        else:
            cur.execute(
                """
                SELECT
                    c.id::text,
                    c.workspace_id::text,
                    w.root_uri,
                    c.git_commit,
                    c.branch,
                    c.intent,
                    c.summary,
                    c.metadata,
                    c.created_at
                FROM changesets c
                JOIN workspaces w ON w.id = c.workspace_id
                WHERE c.git_commit = %s OR c.git_commit LIKE %s
                ORDER BY c.created_at DESC
                LIMIT 1
                """,
                (ref, f"{ref}%"),
            )
        row = cur.fetchone()
        if row is None:
            return {"changeset_ref": ref, "found": False}

        changeset_id = row[0]
        workspace_id = row[1]
        workspace_uri = row[2]
        git_commit = row[3]

        cur.execute(
            """
            SELECT
                cf.id::text,
                cf.file_path,
                cf.status,
                cf.additions,
                cf.deletions,
                cf.metadata
            FROM change_files cf
            WHERE cf.changeset_id = %s::uuid
            ORDER BY cf.file_path
            """,
            (changeset_id,),
        )
        file_rows = cur.fetchall()

        cur.execute(
            """
            SELECT DISTINCT
                ce.id::text,
                ce.kind,
                ce.name,
                ce.qualified_name,
                ce.file_path,
                ce.start_line,
                ce.end_line,
                celnk.match_type,
                celnk.confidence,
                celnk.metadata
            FROM change_entities celnk
            JOIN code_entities ce ON ce.id = celnk.code_entity_id
            WHERE celnk.changeset_id = %s::uuid
            ORDER BY ce.file_path, ce.start_line NULLS LAST, ce.qualified_name NULLS LAST
            """,
            (changeset_id,),
        )
        entity_rows = cur.fetchall()

    record = {
        "changeset_ref": ref,
        "found": True,
        "changeset_id": changeset_id,
        "workspace_id": workspace_id,
        "workspace_uri": workspace_uri,
        "git_commit": git_commit,
        "branch": row[4],
        "intent": row[5],
        "summary": row[6],
        "metadata": row[7],
        "created_at": row[8].isoformat() if row[8] else None,
        "evidence": evidence_ref(
            "changeset",
            target_id=changeset_id,
            workspace_id=workspace_id,
            workspace_uri=workspace_uri,
            locator={"git_commit": git_commit, "changeset_id": changeset_id},
            metadata={"branch": row[4], "intent": row[5]},
            changeset_id=changeset_id,
        ),
        "files": [
            {
                "change_file_id": file_row[0],
                "file_path": file_row[1],
                "status": file_row[2],
                "additions": file_row[3],
                "deletions": file_row[4],
                "metadata": file_row[5],
                "evidence": evidence_ref(
                    "change_file",
                    target_id=file_row[0],
                    workspace_id=workspace_id,
                    workspace_uri=workspace_uri,
                    locator={
                        "change_file_id": file_row[0],
                        "file_path": file_row[1],
                        "changeset_id": changeset_id,
                    },
                    metadata={"status": file_row[2]},
                ),
            }
            for file_row in file_rows
        ],
        "touched_entities": [
            {
                "entity_id": entity_row[0],
                "kind": entity_row[1],
                "name": entity_row[2],
                "qualified_name": entity_row[3],
                "file_path": entity_row[4],
                "start_line": entity_row[5],
                "end_line": entity_row[6],
                "match_type": entity_row[7],
                "confidence": entity_row[8],
                "metadata": entity_row[9],
                "evidence": evidence_ref(
                    "code_entity",
                    target_id=entity_row[0],
                    workspace_id=workspace_id,
                    workspace_uri=workspace_uri,
                    locator={
                        "qualified_name": entity_row[3],
                        "file_path": entity_row[4],
                        "start_line": entity_row[5],
                        "end_line": entity_row[6],
                        "changeset_id": changeset_id,
                    },
                    metadata={
                        "match_type": entity_row[7],
                        "confidence": entity_row[8],
                        "link_metadata": entity_row[9],
                    },
                ),
            }
            for entity_row in entity_rows
        ],
    }

    if include_narrative:
        record["narrative"] = summarize_changeset_narrative(record, settings=settings)
    return record


def get_symbol_context(conn: Connection, symbol: str, limit: int = 10) -> list[dict[str, Any]]:
    pattern = f"%{symbol}%"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id::text,
                workspace_id::text,
                kind,
                name,
                qualified_name,
                file_path,
                start_line,
                end_line,
                signature,
                metadata
            FROM code_entities
            WHERE name ILIKE %s OR qualified_name ILIKE %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (pattern, pattern, limit),
        )
        rows = cur.fetchall()
        entity_ids = [row[0] for row in rows]
        changesets_by_entity: dict[str, list[dict[str, Any]]] = {
            entity_id: [] for entity_id in entity_ids
        }
        if entity_ids:
            cur.execute(
                """
                SELECT
                    celnk.code_entity_id::text,
                    c.id::text,
                    c.workspace_id::text,
                    w.root_uri,
                    c.git_commit,
                    c.branch,
                    c.intent,
                    c.summary,
                    c.created_at,
                    cf.id::text,
                    cf.file_path,
                    cf.status,
                    celnk.match_type,
                    celnk.confidence,
                    celnk.metadata
                FROM change_entities celnk
                JOIN changesets c ON c.id = celnk.changeset_id
                JOIN workspaces w ON w.id = c.workspace_id
                JOIN change_files cf ON cf.id = celnk.change_file_id
                WHERE celnk.code_entity_id = ANY(%s::uuid[])
                ORDER BY c.created_at DESC
                """,
                (entity_ids,),
            )
            for row in cur.fetchall():
                changesets_by_entity.setdefault(row[0], []).append(
                    {
                        "changeset_id": row[1],
                        "workspace_id": row[2],
                        "workspace_uri": row[3],
                        "git_commit": row[4],
                        "branch": row[5],
                        "intent": row[6],
                        "summary": row[7],
                        "created_at": row[8].isoformat() if row[8] else None,
                        "change_file_id": row[9],
                        "file_path": row[10],
                        "status": row[11],
                        "match_type": row[12],
                        "confidence": row[13],
                        "metadata": row[14],
                        "evidence": evidence_ref(
                            "changeset",
                            target_id=row[1],
                            workspace_id=row[2],
                            workspace_uri=row[3],
                            locator={
                                "change_file_id": row[9],
                                "file_path": row[10],
                                "git_commit": row[4],
                            },
                            metadata={
                                "status": row[11],
                                "match_type": row[12],
                                "confidence": row[13],
                                "link_metadata": row[14],
                            },
                            changeset_id=row[1],
                            change_file_id=row[9],
                            file_path=row[10],
                        ),
                    }
                )

    return [
        {
            "entity_id": row[0],
            "workspace_id": row[1],
            "kind": row[2],
            "name": row[3],
            "qualified_name": row[4],
            "file_path": row[5],
            "start_line": row[6],
            "end_line": row[7],
            "signature": row[8],
            "metadata": row[9],
            "related_changesets": changesets_by_entity.get(row[0], []),
            "evidence": evidence_ref(
                "code_entity",
                target_id=row[0],
                workspace_id=row[1],
                locator={
                    "qualified_name": row[4],
                    "file_path": row[5],
                    "start_line": row[6],
                    "end_line": row[7],
                },
                entity_id=row[0],
                qualified_name=row[4],
                file_path=row[5],
            ),
        }
        for row in rows
    ]


def make_snippet(content: str, limit: int = SNIPPET_CHARS) -> str:
    sanitized, _ = sanitize_text(content)
    if len(sanitized) <= limit:
        return sanitized
    return sanitized[:limit]
