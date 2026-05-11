from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from geond.code_graph.python_indexer import IndexedPythonFile


def store_code_index(
    conn: Connection,
    workspace_id: str,
    indexed_files: list[IndexedPythonFile],
) -> dict[str, Any]:
    file_paths = [item.file_path for item in indexed_files]
    entity_id_by_qualified_name: dict[str, str] = {}
    entity_count = 0
    edge_count = 0

    with conn.cursor() as cur:
        if file_paths:
            cur.execute(
                """
                DELETE FROM code_edges e
                USING code_entities source, code_entities target
                WHERE e.workspace_id = %s
                  AND e.source_entity_id = source.id
                  AND e.target_entity_id = target.id
                  AND (
                      source.file_path = ANY(%s)
                      OR target.file_path = ANY(%s)
                  )
                """,
                (workspace_id, file_paths, file_paths),
            )
            cur.execute(
                """
                DELETE FROM code_entities
                WHERE workspace_id = %s
                  AND file_path = ANY(%s)
                """,
                (workspace_id, file_paths),
            )

        for indexed_file in indexed_files:
            for entity in indexed_file.entities:
                cur.execute(
                    """
                    INSERT INTO code_entities (
                        workspace_id,
                        kind,
                        name,
                        qualified_name,
                        file_path,
                        start_line,
                        end_line,
                        signature,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id::text
                    """,
                    (
                        workspace_id,
                        entity.kind,
                        entity.name,
                        entity.qualified_name,
                        entity.file_path,
                        entity.start_line,
                        entity.end_line,
                        entity.signature,
                        Jsonb(entity.metadata),
                    ),
                )
                entity_id_by_qualified_name[entity.qualified_name] = cur.fetchone()[0]
                entity_count += 1

        for indexed_file in indexed_files:
            for edge in indexed_file.edges:
                source_id = entity_id_by_qualified_name.get(edge.source_qualified_name)
                target_id = entity_id_by_qualified_name.get(edge.target_qualified_name)
                if not source_id or not target_id:
                    continue
                cur.execute(
                    """
                    INSERT INTO code_edges (
                        workspace_id,
                        source_entity_id,
                        target_entity_id,
                        edge_type,
                        confidence,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        workspace_id,
                        source_id,
                        target_id,
                        edge.edge_type,
                        edge.confidence,
                        Jsonb(edge.metadata),
                    ),
                )
                edge_count += 1

    conn.commit()
    return {
        "indexed_files": len(indexed_files),
        "file_paths": file_paths,
        "entities": entity_count,
        "edges": edge_count,
        "errors": [
            {"file_path": item.file_path, "errors": item.errors}
            for item in indexed_files
            if item.errors
        ],
    }
