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
    reexport_target_by_alias: dict[str, str] = {}
    wildcard_reexports: list[tuple[str, str]] = []
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
                if entity.kind == "reexport":
                    add_reexport_metadata(
                        entity.qualified_name,
                        entity.metadata,
                        reexport_target_by_alias,
                        wildcard_reexports,
                    )
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

            cur.execute(
                """
                SELECT qualified_name, metadata
                FROM code_entities
                WHERE workspace_id = %s
                  AND kind = 'reexport'
                """,
                (workspace_id,),
            )
            for qualified_name, metadata in cur.fetchall():
                add_reexport_metadata(
                    qualified_name,
                    metadata,
                    reexport_target_by_alias,
                    wildcard_reexports,
                )

            reexport_target_names = collect_reexport_target_names(
                missing_qualified_names,
                reexport_target_by_alias,
                wildcard_reexports,
            )
            if reexport_target_names:
                cur.execute(
                    """
                    SELECT qualified_name, id::text
                    FROM code_entities
                    WHERE workspace_id = %s
                      AND qualified_name = ANY(%s)
                    """,
                    (workspace_id, reexport_target_names),
                )
                for qualified_name, entity_id in cur.fetchall():
                    entity_id_by_qualified_name.setdefault(qualified_name, entity_id)

            default_aliases = [
                qualified_name
                for qualified_name in [*missing_qualified_names, *reexport_target_names]
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
                target_id = resolve_entity_id(
                    edge.target_qualified_name,
                    entity_id_by_qualified_name,
                    default_export_id_by_alias,
                    reexport_target_by_alias,
                    wildcard_reexports,
                )
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


def store_lsp_references(
    conn: Connection,
    workspace_id: str,
    references: list[dict[str, Any]],
    replace: bool = True,
) -> dict[str, Any]:
    inserted = 0
    skipped: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        if replace:
            cur.execute(
                """
                DELETE FROM code_edges
                WHERE workspace_id = %s
                  AND edge_type = 'references'
                  AND metadata->>'source' = 'lsp'
                """,
                (workspace_id,),
            )
        for index, item in enumerate(references):
            if not isinstance(item, dict):
                skipped.append({"index": index, "reason": "invalid_reference_shape"})
                continue
            target_id = resolve_lsp_target_entity(cur, workspace_id, item)
            source_id = resolve_lsp_source_entity(cur, workspace_id, item)
            if not target_id or not source_id:
                skipped.append(
                    {
                        "index": index,
                        "reason": "unresolved_source_or_target",
                        "source_resolved": bool(source_id),
                        "target_resolved": bool(target_id),
                    }
                )
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
                VALUES (%s, %s, %s, 'references', %s, %s)
                """,
                (
                    workspace_id,
                    source_id,
                    target_id,
                    lsp_reference_confidence(item),
                    Jsonb(lsp_reference_metadata(item)),
                ),
            )
            inserted += 1
    conn.commit()
    return {"references": inserted, "skipped": skipped, "replace": replace}


def resolve_lsp_target_entity(cur: Any, workspace_id: str, item: dict[str, Any]) -> str | None:
    qualified_name = item.get("target_qualified_name") or item.get("qualified_name")
    if isinstance(qualified_name, str) and qualified_name.strip():
        return entity_id_by_qualified_name(cur, workspace_id, qualified_name.strip())
    target = item.get("target") if isinstance(item.get("target"), dict) else item
    return enclosing_entity_id(
        cur,
        workspace_id,
        str(target.get("file_path") or target.get("target_file_path") or ""),
        target.get("start_line") or target.get("target_start_line"),
    )


def resolve_lsp_source_entity(cur: Any, workspace_id: str, item: dict[str, Any]) -> str | None:
    qualified_name = item.get("source_qualified_name")
    if isinstance(qualified_name, str) and qualified_name.strip():
        return entity_id_by_qualified_name(cur, workspace_id, qualified_name.strip())
    reference = item.get("reference") if isinstance(item.get("reference"), dict) else item
    return enclosing_entity_id(
        cur,
        workspace_id,
        str(reference.get("file_path") or reference.get("source_file_path") or ""),
        reference.get("start_line") or reference.get("line") or reference.get("source_start_line"),
    )


def entity_id_by_qualified_name(cur: Any, workspace_id: str, qualified_name: str) -> str | None:
    cur.execute(
        """
        SELECT id::text
        FROM code_entities
        WHERE workspace_id = %s
          AND qualified_name = %s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (workspace_id, qualified_name),
    )
    row = cur.fetchone()
    return row[0] if row else None


def enclosing_entity_id(
    cur: Any,
    workspace_id: str,
    file_path: str,
    line: Any,
) -> str | None:
    if not file_path or line is None:
        return None
    try:
        line_number = int(line)
    except (TypeError, ValueError):
        return None
    cur.execute(
        """
        SELECT id::text
        FROM code_entities
        WHERE workspace_id = %s
          AND file_path = %s
          AND start_line <= %s
          AND end_line >= %s
        ORDER BY (end_line - start_line) ASC NULLS LAST, created_at DESC
        LIMIT 1
        """,
        (workspace_id, file_path, line_number, line_number),
    )
    row = cur.fetchone()
    return row[0] if row else None


def lsp_reference_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
    metadata["source"] = "lsp"
    provider = item.get("provider") or metadata.get("provider")
    if provider:
        metadata["provider"] = provider
    metadata["reference"] = item.get("reference") or {
        key: item.get(key)
        for key in ("file_path", "start_line", "end_line", "line", "character")
        if key in item
    }
    return metadata


def lsp_reference_confidence(item: dict[str, Any]) -> float:
    try:
        return float(item.get("confidence") or 0.95)
    except (TypeError, ValueError):
        return 0.95


def add_reexport_metadata(
    reexport_qualified_name: str,
    metadata: dict[str, Any],
    reexport_target_by_alias: dict[str, str],
    wildcard_reexports: list[tuple[str, str]],
) -> None:
    bindings = metadata.get("reexported_bindings") or {}
    if isinstance(bindings, dict):
        for alias, target in bindings.items():
            if isinstance(alias, str) and isinstance(target, str):
                reexport_target_by_alias.setdefault(alias, target)

    exporter_module = reexport_qualified_name.split(":reexport:", 1)[0]
    modules = metadata.get("reexported_modules") or []
    if isinstance(modules, list):
        for target_module in modules:
            if isinstance(target_module, str):
                wildcard_reexports.append((exporter_module, target_module))


def collect_reexport_target_names(
    qualified_names: list[str],
    reexport_target_by_alias: dict[str, str],
    wildcard_reexports: list[tuple[str, str]],
) -> list[str]:
    targets: set[str] = set()
    for qualified_name in qualified_names:
        current = qualified_name
        seen: set[str] = set()
        for _ in range(5):
            next_name = reexport_target_by_alias.get(current) or resolve_wildcard_reexport_target(
                current, wildcard_reexports
            )
            if not next_name or next_name in seen:
                break
            seen.add(next_name)
            targets.add(next_name)
            current = next_name
    return sorted(targets)


def resolve_entity_id(
    qualified_name: str,
    entity_id_by_qualified_name: dict[str, str],
    default_export_id_by_alias: dict[str, str],
    reexport_target_by_alias: dict[str, str],
    wildcard_reexports: list[tuple[str, str]],
) -> str | None:
    current = qualified_name
    seen: set[str] = set()
    for _ in range(6):
        entity_id = entity_id_by_qualified_name.get(current) or default_export_id_by_alias.get(
            current
        )
        if entity_id:
            return entity_id
        if current in seen:
            return None
        seen.add(current)
        next_name = reexport_target_by_alias.get(current) or resolve_wildcard_reexport_target(
            current, wildcard_reexports
        )
        if not next_name:
            return None
        current = next_name
    return None


def resolve_wildcard_reexport_target(
    qualified_name: str,
    wildcard_reexports: list[tuple[str, str]],
) -> str | None:
    candidates = set()
    for alias_module, target_module in wildcard_reexports:
        prefix = f"{alias_module}."
        if qualified_name.startswith(prefix):
            candidates.add(f"{target_module}.{qualified_name.removeprefix(prefix)}")
    if len(candidates) == 1:
        return next(iter(candidates))
    return None
