from __future__ import annotations

from typing import Any

from psycopg import Connection


def search_dev_memory(conn: Connection, query: str, limit: int = 10) -> list[dict[str, Any]]:
    pattern = f"%{query}%"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                s.external_id,
                s.title,
                m.role,
                m.ordinal,
                left(m.content, 1200) AS snippet,
                ts_rank(to_tsvector('simple', m.content), plainto_tsquery('simple', %s)) AS rank
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE m.content ILIKE %s
               OR to_tsvector('simple', m.content) @@ plainto_tsquery('simple', %s)
            ORDER BY rank DESC NULLS LAST, m.created_at DESC
            LIMIT %s
            """,
            (query, pattern, query, limit),
        )
        rows = cur.fetchall()
    return [
        {
            "session_external_id": row[0],
            "session_title": row[1],
            "role": row[2],
            "ordinal": row[3],
            "snippet": row[4],
            "rank": float(row[5] or 0),
        }
        for row in rows
    ]


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
            SELECT s.external_id, s.title, m.ordinal, left(m.content, 1200)
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
                "snippet": row[3],
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
