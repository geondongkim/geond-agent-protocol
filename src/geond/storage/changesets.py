from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.cursor import Cursor


def link_changesets_to_code_entities(
    conn: Connection,
    workspace_id: str,
    changeset_ids: list[str] | None = None,
    file_paths: list[str] | None = None,
) -> int:
    with conn.cursor() as cur:
        linked = link_changesets_to_code_entities_cursor(
            cur,
            workspace_id,
            changeset_ids=changeset_ids,
            file_paths=file_paths,
        )
    conn.commit()
    return linked


def link_changesets_to_code_entities_cursor(
    cur: Cursor,
    workspace_id: str,
    changeset_ids: list[str] | None = None,
    file_paths: list[str] | None = None,
) -> int:
    filters = ["c.workspace_id = %s"]
    params: list[Any] = [workspace_id]
    if changeset_ids:
        filters.append("c.id = ANY(%s::uuid[])")
        params.append(changeset_ids)
    if file_paths:
        filters.append("cf.file_path = ANY(%s::text[])")
        params.append(file_paths)

    where_clause = " AND ".join(filters)
    cur.execute(
        f"""
        INSERT INTO change_entities (
            workspace_id,
            changeset_id,
            change_file_id,
            code_entity_id,
            match_type,
            confidence,
            metadata
        )
        SELECT
            c.workspace_id,
            c.id,
            cf.id,
            ce.id,
            'file_path',
            0.8,
            jsonb_build_object('link_source', 'file_path')
        FROM changesets c
        JOIN change_files cf ON cf.changeset_id = c.id
        JOIN code_entities ce ON ce.workspace_id = c.workspace_id
        WHERE {where_clause}
          AND (
              replace(btrim(cf.file_path, '/'), chr(92), '/') =
                  replace(btrim(ce.file_path, '/'), chr(92), '/')
              OR replace(btrim(cf.file_path, '/'), chr(92), '/') LIKE
                  ('%%/' || replace(btrim(ce.file_path, '/'), chr(92), '/'))
              OR replace(btrim(ce.file_path, '/'), chr(92), '/') LIKE
                  ('%%/' || replace(btrim(cf.file_path, '/'), chr(92), '/'))
          )
        ON CONFLICT (change_file_id, code_entity_id) DO NOTHING
        RETURNING id
        """,
        params,
    )
    return len(cur.fetchall())
