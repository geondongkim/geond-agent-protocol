from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from geond.code_graph.python_indexer import IndexedPythonFile
from geond.storage.changesets import link_changesets_to_code_entities_cursor


def store_code_index(
    conn: Connection,
    workspace_id: str,
    indexed_files: list[IndexedPythonFile],
) -> dict[str, Any]:
    file_paths = [item.file_path for item in indexed_files]
    entity_id_by_qualified_name: dict[str, str] = {}
    default_export_id_by_alias: dict[str, str] = {}
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
                entity_id = cur.fetchone()[0]
                entity_id_by_qualified_name[entity.qualified_name] = entity_id
                if entity.metadata.get("default_export") and entity.qualified_name:
                    module_name = entity.qualified_name.rsplit(".", 1)[0]
                    default_export_id_by_alias[f"{module_name}.default"] = entity_id
                entity_count += 1

        required_qualified_names = {
            qualified_name
            for indexed_file in indexed_files
            for edge in indexed_file.edges
            for qualified_name in (edge.source_qualified_name, edge.target_qualified_name)
        }
        missing_qualified_names = sorted(
            required_qualified_names - set(entity_id_by_qualified_name)
        )
        if missing_qualified_names:
            cur.execute(
                """
                SELECT qualified_name, id::text
                FROM code_entities
                WHERE workspace_id = %s
                  AND qualified_name = ANY(%s)
                """,
                (workspace_id, missing_qualified_names),
            )
            for qualified_name, entity_id in cur.fetchall():
                entity_id_by_qualified_name.setdefault(qualified_name, entity_id)

            default_aliases = [
                qualified_name
                for qualified_name in missing_qualified_names
                if qualified_name.endswith(".default")
                and qualified_name not in default_export_id_by_alias
            ]
            if default_aliases:
                cur.execute(
                    """
                    SELECT qualified_name, id::text
                    FROM code_entities
                    WHERE workspace_id = %s
                      AND metadata->>'default_export' = 'true'
                    """,
                    (workspace_id,),
                )
                default_modules = {
                    qualified_name.removesuffix(".default") for qualified_name in default_aliases
                }
                for qualified_name, entity_id in cur.fetchall():
                    module_name = qualified_name.rsplit(".", 1)[0]
                    if module_name in default_modules:
                        default_export_id_by_alias.setdefault(f"{module_name}.default", entity_id)

        for indexed_file in indexed_files:
            for edge in indexed_file.edges:
                source_id = entity_id_by_qualified_name.get(edge.source_qualified_name)
                target_id = entity_id_by_qualified_name.get(
                    edge.target_qualified_name
                ) or default_export_id_by_alias.get(edge.target_qualified_name)
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

        linked_entities = 0
        if file_paths:
            linked_entities = link_changesets_to_code_entities_cursor(
                cur,
                workspace_id,
                file_paths=file_paths,
            )

    conn.commit()
    return {
        "indexed_files": len(indexed_files),
        "file_paths": file_paths,
        "entities": entity_count,
        "edges": edge_count,
        "linked_change_entities": linked_entities,
        "errors": [
            {"file_path": item.file_path, "errors": item.errors}
            for item in indexed_files
            if item.errors
        ],
    }
