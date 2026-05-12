from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from psycopg import Connection
from psycopg.cursor import Cursor
from psycopg.types.json import Jsonb

HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


@dataclass(frozen=True)
class PatchLineRange:
    hunk_index: int
    old_start_line: int
    old_end_line: int
    new_start_line: int
    new_end_line: int
    changed_start_line: int
    changed_end_line: int
    change_kind: str
    deleted_start_line: int | None = None
    deleted_end_line: int | None = None

    def to_metadata(self) -> dict[str, int | str]:
        metadata: dict[str, int | str] = {
            "hunk_index": self.hunk_index,
            "old_start_line": self.old_start_line,
            "old_end_line": self.old_end_line,
            "new_start_line": self.new_start_line,
            "new_end_line": self.new_end_line,
            "changed_start_line": self.changed_start_line,
            "changed_end_line": self.changed_end_line,
            "change_kind": self.change_kind,
        }
        if self.deleted_start_line is not None:
            metadata["deleted_start_line"] = self.deleted_start_line
        if self.deleted_end_line is not None:
            metadata["deleted_end_line"] = self.deleted_end_line
        return metadata


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
    line_range_links = link_changesets_to_code_entities_by_line_range(
        cur,
        workspace_id,
        changeset_ids=changeset_ids,
        file_paths=file_paths,
    )
    file_path_links = link_changesets_to_code_entities_by_file_path(
        cur,
        workspace_id,
        changeset_ids=changeset_ids,
        file_paths=file_paths,
    )
    return line_range_links + file_path_links


def link_changesets_to_code_entities_by_line_range(
    cur: Cursor,
    workspace_id: str,
    changeset_ids: list[str] | None = None,
    file_paths: list[str] | None = None,
) -> int:
    filters = ["c.workspace_id = %s", "cf.patch IS NOT NULL", "cf.patch <> ''"]
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
        SELECT c.id::text, cf.id::text, cf.file_path, cf.patch
        FROM changesets c
        JOIN change_files cf ON cf.changeset_id = c.id
        WHERE {where_clause}
        """,
        params,
    )

    linked = 0
    for changeset_id, change_file_id, file_path, patch in cur.fetchall():
        line_ranges = parse_unified_diff_line_ranges(str(patch or ""))
        if not line_ranges:
            continue
        for line_range in line_ranges:
            linked += link_one_line_range(
                cur,
                workspace_id=workspace_id,
                changeset_id=changeset_id,
                change_file_id=change_file_id,
                file_path=file_path,
                line_range=line_range,
            )
    return linked


def link_one_line_range(
    cur: Cursor,
    workspace_id: str,
    changeset_id: str,
    change_file_id: str,
    file_path: str,
    line_range: PatchLineRange,
) -> int:
    metadata = {
        "link_source": "patch_hunk_line_range",
        **line_range.to_metadata(),
    }
    cur.execute(
        """
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
            %s,
            %s,
            %s,
            ce.id,
            'line_range',
            CASE WHEN ce.kind = 'module' THEN 0.85 ELSE 1.0 END,
            %s
        FROM code_entities ce
        WHERE ce.workspace_id = %s
          AND ce.start_line IS NOT NULL
          AND ce.start_line <= %s
          AND coalesce(ce.end_line, ce.start_line) >= %s
          AND (
              replace(btrim(%s, '/'), chr(92), '/') =
                  replace(btrim(ce.file_path, '/'), chr(92), '/')
              OR replace(btrim(%s, '/'), chr(92), '/') LIKE
                  ('%%/' || replace(btrim(ce.file_path, '/'), chr(92), '/'))
              OR replace(btrim(ce.file_path, '/'), chr(92), '/') LIKE
                  ('%%/' || replace(btrim(%s, '/'), chr(92), '/'))
          )
        ON CONFLICT (change_file_id, code_entity_id)
        DO UPDATE SET
            match_type = CASE
                WHEN change_entities.confidence < EXCLUDED.confidence
                    THEN EXCLUDED.match_type
                ELSE change_entities.match_type
            END,
            confidence = GREATEST(change_entities.confidence, EXCLUDED.confidence),
            metadata = change_entities.metadata || EXCLUDED.metadata
        RETURNING id
        """,
        (
            workspace_id,
            changeset_id,
            change_file_id,
            Jsonb(metadata),
            workspace_id,
            line_range.changed_end_line,
            line_range.changed_start_line,
            file_path,
            file_path,
            file_path,
        ),
    )
    return len(cur.fetchall())


def link_changesets_to_code_entities_by_file_path(
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
              cf.patch IS NULL
              OR cf.patch = ''
              OR NOT EXISTS (
                  SELECT 1
                  FROM change_entities existing
                  WHERE existing.change_file_id = cf.id
                    AND existing.match_type = 'line_range'
              )
          )
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


def parse_unified_diff_line_ranges(patch: str) -> list[PatchLineRange]:
    ranges: list[PatchLineRange] = []
    current: dict[str, Any] | None = None

    for raw_line in patch.splitlines():
        header = HUNK_HEADER_RE.match(raw_line)
        if header:
            if current is not None:
                ranges.append(make_line_range(current))
            old_start = int(header.group("old_start"))
            old_count = int(header.group("old_count") or "1")
            new_start = int(header.group("new_start"))
            new_count = int(header.group("new_count") or "1")
            current = {
                "hunk_index": len(ranges),
                "old_start": old_start,
                "old_end": old_start + max(old_count, 1) - 1,
                "new_start": new_start,
                "new_end": new_start + max(new_count, 1) - 1,
                "old_line": old_start,
                "new_line": new_start,
                "changed_new_lines": [],
                "deleted_old_lines": [],
                "deleted_new_anchor_lines": [],
            }
            continue

        if current is None:
            continue
        if raw_line.startswith("\\"):
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            current["changed_new_lines"].append(current["new_line"])
            current["new_line"] += 1
            continue
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            current["deleted_old_lines"].append(current["old_line"])
            current["deleted_new_anchor_lines"].append(current["new_line"])
            current["old_line"] += 1
            continue
        current["old_line"] += 1
        current["new_line"] += 1

    if current is not None:
        ranges.append(make_line_range(current))
    return ranges


def make_line_range(hunk: dict[str, Any]) -> PatchLineRange:
    changed_lines = hunk["changed_new_lines"]
    deleted_lines = hunk["deleted_old_lines"]
    deleted_anchor_lines = hunk["deleted_new_anchor_lines"]
    if changed_lines:
        changed_start = min(changed_lines)
        changed_end = max(changed_lines)
        change_kind = "modified" if deleted_lines else "added"
    else:
        changed_start = min(deleted_anchor_lines) if deleted_anchor_lines else hunk["new_start"]
        changed_end = max(deleted_anchor_lines) if deleted_anchor_lines else hunk["new_start"]
        change_kind = "deletion_only" if deleted_lines else "context_only"
    return PatchLineRange(
        hunk_index=hunk["hunk_index"],
        old_start_line=hunk["old_start"],
        old_end_line=hunk["old_end"],
        new_start_line=hunk["new_start"],
        new_end_line=hunk["new_end"],
        changed_start_line=changed_start,
        changed_end_line=changed_end,
        change_kind=change_kind,
        deleted_start_line=min(deleted_lines) if deleted_lines else None,
        deleted_end_line=max(deleted_lines) if deleted_lines else None,
    )
