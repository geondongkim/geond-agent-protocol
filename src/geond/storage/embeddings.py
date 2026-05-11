from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from geond.config import get_settings
from geond.embeddings import EmbeddingProvider, content_hash


def embed_pending_messages(
    conn: Connection,
    provider: EmbeddingProvider,
    limit: int = 100,
    batch_size: int = 32,
) -> int:
    total = 0
    while total < limit:
        batch_limit = min(batch_size, limit - total)
        rows = fetch_messages_without_embedding(conn, provider.model, batch_limit)
        if not rows:
            break

        max_chars = get_settings().embedding_max_chars
        texts = [prepare_embedding_text(row[3], max_chars=max_chars) for row in rows]
        vectors = provider.embed(texts)
        with conn.cursor() as cur:
            for (message_id, workspace_id, target_kind, content), embedded_text, vector in zip(
                rows, texts, vectors, strict=True
            ):
                cur.execute(
                    """
                    INSERT INTO embeddings (
                        workspace_id,
                        target_table,
                        target_id,
                        target_kind,
                        model,
                        dimensions,
                        content_hash,
                        embedding,
                        metadata
                    )
                    VALUES (%s, 'messages', %s, %s, %s, %s, %s, %s::vector, %s)
                    ON CONFLICT (target_table, target_id, model)
                    DO UPDATE SET content_hash = EXCLUDED.content_hash,
                                  embedding = EXCLUDED.embedding,
                                  dimensions = EXCLUDED.dimensions,
                                  metadata = EXCLUDED.metadata
                    """,
                    (
                        workspace_id,
                        message_id,
                        target_kind,
                        provider.model,
                        provider.dimensions,
                        content_hash(embedded_text),
                        vector_to_sql(vector),
                        Jsonb(
                            {
                                "source": "embed_pending_messages",
                                "truncated": len(content) > len(embedded_text),
                                "embedded_char_count": len(embedded_text),
                            }
                        ),
                    ),
                )
        conn.commit()
        total += len(rows)
    return total


def fetch_messages_without_embedding(
    conn: Connection,
    model: str,
    limit: int,
) -> list[tuple[str, str, str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.id::text, s.workspace_id::text, m.role, m.content
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE length(trim(m.content)) > 0
              AND NOT EXISTS (
                  SELECT 1
                  FROM embeddings e
                  WHERE e.target_table = 'messages'
                    AND e.target_id = m.id
                    AND e.model = %s
              )
            ORDER BY m.created_at ASC, m.ordinal ASC
            LIMIT %s
            """,
            (model, limit),
        )
        return cur.fetchall()


def vector_to_sql(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.12g}" for value in vector) + "]"


def prepare_embedding_text(text: str, max_chars: int) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars]


def embedding_stats(conn: Connection) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT model, dimensions, count(*)
            FROM embeddings
            GROUP BY model, dimensions
            ORDER BY model, dimensions
            """
        )
        rows = cur.fetchall()
    return {"models": [{"model": row[0], "dimensions": row[1], "count": row[2]} for row in rows]}
