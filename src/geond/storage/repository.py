from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.cursor import Cursor
from psycopg.types.json import Jsonb

from geond.adapters.claude_code import SOURCE as CLAUDE_CODE_SOURCE
from geond.adapters.claude_code import ParsedClaudeCodeSession
from geond.adapters.codex import SOURCE as CODEX_SOURCE
from geond.adapters.codex import ParsedCodexSession
from geond.adapters.vscode_copilot import SOURCE, ParsedCopilotSession
from geond.redaction import RedactionFinding, redact_text, redact_value
from geond.storage.changesets import link_changesets_to_code_entities_cursor


def upsert_workspace(
    conn: Connection,
    root_uri: str,
    name: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT w.id::text
            FROM workspace_aliases wa
            JOIN workspaces w ON w.id = wa.workspace_id
            WHERE wa.alias_uri = %s
            LIMIT 1
            """,
            (root_uri,),
        )
        alias_row = cur.fetchone()
        if alias_row:
            workspace_id = alias_row[0]
            cur.execute(
                """
                UPDATE workspaces
                SET name = %s,
                    metadata = metadata || %s
                WHERE id = %s::uuid
                """,
                (name, Jsonb(metadata or {}), workspace_id),
            )
            cur.execute(
                """
                UPDATE workspace_aliases
                SET last_seen_at = now()
                WHERE workspace_id = %s::uuid
                  AND alias_uri = %s
                """,
                (workspace_id, root_uri),
            )
            conn.commit()
            return workspace_id

        cur.execute(
            """
            INSERT INTO workspaces (root_uri, name, metadata)
            VALUES (%s, %s, %s)
            ON CONFLICT (root_uri)
            DO UPDATE SET name = EXCLUDED.name, metadata = workspaces.metadata || EXCLUDED.metadata
            RETURNING id::text
            """,
            (root_uri, name, Jsonb(metadata or {})),
        )
        workspace_id = cur.fetchone()[0]
    conn.commit()
    return workspace_id


def resolve_workspace_id_cursor(cur: Cursor, workspace_id_or_uri: str) -> str | None:
    cur.execute(
        """
        SELECT id::text
        FROM workspaces
        WHERE id::text = %s OR root_uri = %s
        LIMIT 1
        """,
        (workspace_id_or_uri, workspace_id_or_uri),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        """
        SELECT workspace_id::text
        FROM workspace_aliases
        WHERE alias_uri = %s
        LIMIT 1
        """,
        (workspace_id_or_uri,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def resolve_workspace_id(conn: Connection, workspace_id_or_uri: str) -> str | None:
    with conn.cursor() as cur:
        return resolve_workspace_id_cursor(cur, workspace_id_or_uri)


def register_workspace_alias(
    conn: Connection,
    workspace_id_or_uri: str,
    alias_uri: str,
    reason: str = "moved",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        workspace_id = resolve_workspace_id_cursor(cur, workspace_id_or_uri)
        if not workspace_id:
            raise ValueError("workspace_id_or_uri does not resolve to a workspace")

        cur.execute(
            """
            SELECT id::text
            FROM workspaces
            WHERE root_uri = %s
            LIMIT 1
            """,
            (alias_uri,),
        )
        root_row = cur.fetchone()
        if root_row and root_row[0] != workspace_id:
            raise ValueError("alias_uri is already the root_uri of another workspace")

        cur.execute(
            """
            SELECT workspace_id::text
            FROM workspace_aliases
            WHERE alias_uri = %s
            LIMIT 1
            """,
            (alias_uri,),
        )
        alias_row = cur.fetchone()
        if alias_row and alias_row[0] != workspace_id:
            raise ValueError("alias_uri is already registered for another workspace")

        cur.execute(
            """
            INSERT INTO workspace_aliases (workspace_id, alias_uri, reason, metadata)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (alias_uri)
            DO UPDATE SET reason = EXCLUDED.reason,
                          metadata = workspace_aliases.metadata || EXCLUDED.metadata,
                          last_seen_at = now()
            RETURNING id::text, workspace_id::text, alias_uri, reason, metadata, last_seen_at
            """,
            (workspace_id, alias_uri, reason or "alias", Jsonb(metadata or {})),
        )
        row = cur.fetchone()
    conn.commit()
    return {
        "alias_id": row[0],
        "workspace_id": row[1],
        "alias_uri": row[2],
        "reason": row[3],
        "metadata": row[4],
        "last_seen_at": row[5].isoformat() if row[5] else None,
    }


def list_workspace_aliases(
    conn: Connection,
    workspace_id_or_uri: str | None = None,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        workspace_id = (
            resolve_workspace_id_cursor(cur, workspace_id_or_uri) if workspace_id_or_uri else None
        )
        if workspace_id_or_uri and not workspace_id:
            return []
        workspace_filter = "WHERE wa.workspace_id = %s::uuid" if workspace_id else ""
        params: tuple[Any, ...] = (workspace_id,) if workspace_id else ()
        cur.execute(
            f"""
            SELECT
                wa.id::text,
                wa.workspace_id::text,
                w.root_uri,
                w.name,
                wa.alias_uri,
                wa.reason,
                wa.metadata,
                wa.created_at,
                wa.last_seen_at
            FROM workspace_aliases wa
            JOIN workspaces w ON w.id = wa.workspace_id
            {workspace_filter}
            ORDER BY wa.last_seen_at DESC
            """,
            params,
        )
        rows = cur.fetchall()
    return [
        {
            "alias_id": row[0],
            "workspace_id": row[1],
            "workspace_uri": row[2],
            "workspace_name": row[3],
            "alias_uri": row[4],
            "reason": row[5],
            "metadata": row[6],
            "created_at": row[7].isoformat() if row[7] else None,
            "last_seen_at": row[8].isoformat() if row[8] else None,
        }
        for row in rows
    ]


def record_workspace_fingerprints(
    conn: Connection,
    workspace_id_or_uri: str,
    fingerprints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        workspace_id = resolve_workspace_id_cursor(cur, workspace_id_or_uri)
        if not workspace_id:
            raise ValueError("workspace_id_or_uri does not resolve to a workspace")

        rows: list[tuple[Any, ...]] = []
        for fingerprint in normalize_fingerprints(fingerprints):
            cur.execute(
                """
                INSERT INTO workspace_fingerprints (
                    workspace_id,
                    fingerprint_type,
                    fingerprint_value,
                    metadata
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (workspace_id, fingerprint_type, fingerprint_value)
                DO UPDATE SET metadata = workspace_fingerprints.metadata || EXCLUDED.metadata,
                              last_seen_at = now()
                RETURNING
                    id::text,
                    workspace_id::text,
                    fingerprint_type,
                    fingerprint_value,
                    metadata,
                    last_seen_at
                """,
                (
                    workspace_id,
                    fingerprint["fingerprint_type"],
                    fingerprint["fingerprint_value"],
                    Jsonb(fingerprint.get("metadata") or {}),
                ),
            )
            rows.append(cur.fetchone())
    conn.commit()
    return [workspace_fingerprint_result(row) for row in rows]


def suggest_workspace_aliases(
    conn: Connection,
    alias_uri: str,
    fingerprints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized = normalize_fingerprints(fingerprints)
    if not normalized:
        return []

    with conn.cursor() as cur:
        existing_workspace_id = resolve_workspace_id_cursor(cur, alias_uri)
        matches_by_workspace: dict[str, dict[str, Any]] = {}
        total_weight = sum(fingerprint_weight(item["fingerprint_type"]) for item in normalized)
        for fingerprint in normalized:
            cur.execute(
                """
                SELECT
                    wf.workspace_id::text,
                    w.root_uri,
                    w.name,
                    wf.fingerprint_type,
                    wf.fingerprint_value,
                    wf.metadata,
                    wf.last_seen_at
                FROM workspace_fingerprints wf
                JOIN workspaces w ON w.id = wf.workspace_id
                WHERE wf.fingerprint_type = %s
                  AND wf.fingerprint_value = %s
                ORDER BY wf.last_seen_at DESC
                """,
                (fingerprint["fingerprint_type"], fingerprint["fingerprint_value"]),
            )
            for row in cur.fetchall():
                workspace_id = row[0]
                match = matches_by_workspace.setdefault(
                    workspace_id,
                    {
                        "workspace_id": workspace_id,
                        "workspace_uri": row[1],
                        "workspace_name": row[2],
                        "alias_uri": alias_uri,
                        "already_resolves": existing_workspace_id == workspace_id,
                        "matched_weight": 0.0,
                        "matched_fingerprints": [],
                    },
                )
                match["matched_weight"] += fingerprint_weight(row[3])
                match["matched_fingerprints"].append(
                    {
                        "fingerprint_type": row[3],
                        "fingerprint_value": row[4],
                        "metadata": row[5],
                        "last_seen_at": row[6].isoformat() if row[6] else None,
                    }
                )

    suggestions = []
    for match in matches_by_workspace.values():
        confidence = match["matched_weight"] / total_weight if total_weight else 0.0
        match["confidence"] = round(confidence, 4)
        match["matched_count"] = len(match["matched_fingerprints"])
        del match["matched_weight"]
        suggestions.append(match)
    ranked = sorted(
        suggestions,
        key=lambda item: (item["already_resolves"], item["confidence"], item["matched_count"]),
        reverse=True,
    )
    annotate_workspace_alias_suggestions(ranked, normalized)
    return ranked


def annotate_workspace_alias_suggestions(
    suggestions: list[dict[str, Any]],
    normalized_fingerprints: list[dict[str, Any]],
) -> None:
    if not suggestions:
        return
    input_keys = {
        (item["fingerprint_type"], item["fingerprint_value"]) for item in normalized_fingerprints
    }
    top_confidence = suggestions[0]["confidence"]
    competing_top_matches = sum(
        1
        for item in suggestions
        if not item["already_resolves"] and item["confidence"] == top_confidence
    )
    for item in suggestions:
        matched_keys = {
            (fingerprint["fingerprint_type"], fingerprint["fingerprint_value"])
            for fingerprint in item["matched_fingerprints"]
        }
        item["matched_fingerprint_types"] = sorted(
            {fingerprint_type for fingerprint_type, _ in matched_keys}
        )
        item["unmatched_fingerprint_count"] = len(input_keys - matched_keys)
        item["competing_top_matches"] = (
            competing_top_matches if item["confidence"] == top_confidence else 0
        )
        item["recommendation"] = workspace_alias_recommendation(
            item,
            competing_top_matches,
        )


def workspace_alias_recommendation(
    suggestion: dict[str, Any],
    competing_top_matches: int,
) -> str:
    if suggestion["already_resolves"]:
        return "already_resolves"
    if suggestion["confidence"] >= 0.75 and competing_top_matches == 1:
        return "register_best_candidate"
    if suggestion["confidence"] >= 0.75 and competing_top_matches > 1:
        return "ambiguous_top_candidates"
    return "review_partial_match"


def normalize_fingerprints(fingerprints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for fingerprint in fingerprints:
        fingerprint_type = str(fingerprint.get("fingerprint_type") or "").strip()
        fingerprint_value = str(fingerprint.get("fingerprint_value") or "").strip()
        if not fingerprint_type or not fingerprint_value:
            continue
        key = (fingerprint_type, fingerprint_value)
        if key in seen:
            continue
        seen.add(key)
        metadata = (
            fingerprint.get("metadata") if isinstance(fingerprint.get("metadata"), dict) else {}
        )
        normalized.append(
            {
                "fingerprint_type": fingerprint_type,
                "fingerprint_value": fingerprint_value,
                "metadata": metadata,
            }
        )
    return normalized


def fingerprint_weight(fingerprint_type: str) -> float:
    if fingerprint_type == "git:remote:first-commit":
        return 2.0
    if fingerprint_type.startswith("file:sha256:"):
        return 0.75
    if fingerprint_type.startswith("package:") and fingerprint_type.endswith(":name-sha256"):
        return 0.5
    return 1.0


def workspace_fingerprint_result(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "fingerprint_id": row[0],
        "workspace_id": row[1],
        "fingerprint_type": row[2],
        "fingerprint_value": row[3],
        "metadata": row[4],
        "last_seen_at": row[5].isoformat() if row[5] else None,
    }


def record_changeset(
    conn: Connection,
    workspace_id: str,
    files: list[dict[str, Any]],
    git_commit: str | None = None,
    branch: str | None = None,
    intent: str | None = None,
    summary: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not files:
        raise ValueError("at least one changed file is required")

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO changesets (
                workspace_id,
                git_commit,
                branch,
                intent,
                summary,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id::text
            """,
            (
                workspace_id,
                git_commit,
                branch,
                intent,
                summary,
                Jsonb(metadata or {}),
            ),
        )
        changeset_id = cur.fetchone()[0]
        changed_files: list[dict[str, Any]] = []
        for item in files:
            file_path = str(item.get("file_path") or "").strip()
            if not file_path:
                raise ValueError("changed file entries require file_path")
            cur.execute(
                """
                INSERT INTO change_files (
                    changeset_id,
                    file_path,
                    status,
                    additions,
                    deletions,
                    patch,
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id::text
                """,
                (
                    changeset_id,
                    file_path,
                    item.get("status") or "modified",
                    item.get("additions"),
                    item.get("deletions"),
                    item.get("patch"),
                    Jsonb(item.get("metadata") or {}),
                ),
            )
            changed_files.append(
                {
                    "change_file_id": cur.fetchone()[0],
                    "file_path": file_path,
                    "status": item.get("status") or "modified",
                }
            )

        linked_entities = link_changesets_to_code_entities_cursor(
            cur,
            workspace_id,
            changeset_ids=[changeset_id],
        )

    conn.commit()
    return {
        "changeset_id": changeset_id,
        "files": changed_files,
        "linked_change_entities": linked_entities,
    }


def store_codex_session(conn: Connection, workspace_id: str, session: ParsedCodexSession) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sessions (workspace_id, source, external_id, title, metadata)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, source, external_id)
            DO UPDATE SET title = EXCLUDED.title,
                          metadata = sessions.metadata || EXCLUDED.metadata,
                          updated_at = now()
            RETURNING id::text
            """,
            (
                workspace_id,
                CODEX_SOURCE,
                session.session_id,
                session.title,
                Jsonb(session.metadata),
            ),
        )
        session_row_id = cur.fetchone()[0]

        for event in session.events:
            source_id = f"{CODEX_SOURCE}:{session.session_id}:{event.ordinal}"
            redacted_raw, findings = redact_value(event.raw)
            cur.execute(
                """
                INSERT INTO events (
                    workspace_id,
                    session_id,
                    source,
                    source_id,
                    event_type,
                    occurred_at,
                    payload
                )
                VALUES (%s, %s, %s, %s, %s, %s::timestamptz, %s)
                ON CONFLICT (source, source_id)
                DO UPDATE SET event_type = EXCLUDED.event_type,
                              occurred_at = EXCLUDED.occurred_at,
                              payload = EXCLUDED.payload
                RETURNING id::text
                """,
                (
                    workspace_id,
                    session_row_id,
                    CODEX_SOURCE,
                    source_id,
                    event.event_type,
                    event.timestamp,
                    Jsonb(redacted_raw),
                ),
            )
            insert_redaction_findings(cur, workspace_id, CODEX_SOURCE, source_id, findings)

        for message in session.messages:
            source_id = f"{CODEX_SOURCE}:{session.session_id}:{message.ordinal}"
            redacted_content, content_findings = redact_text(message.content)
            redacted_metadata, metadata_findings = redact_value(message.metadata)
            cur.execute(
                """
                SELECT id::text FROM events
                WHERE source = %s AND source_id = %s
                """,
                (CODEX_SOURCE, source_id),
            )
            row = cur.fetchone()
            raw_event_id = row[0] if row else None
            cur.execute(
                """
                INSERT INTO messages (session_id, raw_event_id, role, ordinal, content, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, ordinal)
                DO UPDATE SET content = EXCLUDED.content,
                              metadata = EXCLUDED.metadata,
                              raw_event_id = EXCLUDED.raw_event_id,
                              role = EXCLUDED.role
                """,
                (
                    session_row_id,
                    raw_event_id,
                    message.role,
                    message.ordinal,
                    redacted_content,
                    Jsonb(redacted_metadata),
                ),
            )
            insert_redaction_findings(
                cur,
                workspace_id,
                CODEX_SOURCE,
                f"{source_id}:message",
                content_findings + metadata_findings,
            )

        delete_stale_message_rows(
            cur,
            session_row_id=session_row_id,
            current_ordinals=[message.ordinal for message in session.messages],
        )

    conn.commit()
    return session_row_id


def store_claude_code_session(
    conn: Connection,
    workspace_id: str,
    session: ParsedClaudeCodeSession,
) -> str:
    title = title_from_claude_metadata(session)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sessions (workspace_id, source, external_id, title, metadata)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, source, external_id)
            DO UPDATE SET title = EXCLUDED.title,
                          metadata = sessions.metadata || EXCLUDED.metadata,
                          updated_at = now()
            RETURNING id::text
            """,
            (
                workspace_id,
                CLAUDE_CODE_SOURCE,
                session.session_id,
                title,
                Jsonb(session.metadata),
            ),
        )
        session_row_id = cur.fetchone()[0]

        for event in session.events:
            source_id = f"{CLAUDE_CODE_SOURCE}:{session.session_id}:{event.ordinal}"
            redacted_raw, findings = redact_value(event.raw)
            cur.execute(
                """
                INSERT INTO events (
                    workspace_id,
                    session_id,
                    source,
                    source_id,
                    event_type,
                    occurred_at,
                    payload
                )
                VALUES (%s, %s, %s, %s, %s, %s::timestamptz, %s)
                ON CONFLICT (source, source_id)
                DO UPDATE SET event_type = EXCLUDED.event_type,
                              occurred_at = EXCLUDED.occurred_at,
                              payload = EXCLUDED.payload
                """,
                (
                    workspace_id,
                    session_row_id,
                    CLAUDE_CODE_SOURCE,
                    source_id,
                    event.record_type,
                    event.timestamp,
                    Jsonb(redacted_raw),
                ),
            )
            insert_redaction_findings(cur, workspace_id, CLAUDE_CODE_SOURCE, source_id, findings)

        for message in session.messages:
            source_id = f"{CLAUDE_CODE_SOURCE}:{session.session_id}:{message.ordinal}"
            redacted_content, content_findings = redact_text(message.content)
            metadata = {
                **message.metadata,
                "source": CLAUDE_CODE_SOURCE,
                "timestamp": message.timestamp,
                "tool_calls": message.tool_calls,
            }
            redacted_metadata, metadata_findings = redact_value(metadata)
            cur.execute(
                """
                SELECT id::text FROM events
                WHERE source = %s AND source_id = %s
                """,
                (CLAUDE_CODE_SOURCE, source_id),
            )
            row = cur.fetchone()
            raw_event_id = row[0] if row else None
            cur.execute(
                """
                INSERT INTO messages (session_id, raw_event_id, role, ordinal, content, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, ordinal)
                DO UPDATE SET content = EXCLUDED.content,
                              metadata = EXCLUDED.metadata,
                              raw_event_id = EXCLUDED.raw_event_id,
                              role = EXCLUDED.role
                """,
                (
                    session_row_id,
                    raw_event_id,
                    message.role,
                    message.ordinal,
                    redacted_content,
                    Jsonb(redacted_metadata),
                ),
            )
            insert_redaction_findings(
                cur,
                workspace_id,
                CLAUDE_CODE_SOURCE,
                f"{source_id}:message",
                content_findings + metadata_findings,
            )

        delete_stale_message_rows(
            cur,
            session_row_id=session_row_id,
            current_ordinals=[message.ordinal for message in session.messages],
        )

    conn.commit()
    return session_row_id


def title_from_claude_metadata(session: ParsedClaudeCodeSession) -> str:
    cwd = session.metadata.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        normalized = cwd.replace("\\", "/").rstrip("/")
        if normalized:
            return normalized.rsplit("/", 1)[-1] or session.session_id
    return session.session_id


def store_vscode_session(conn: Connection, workspace_id: str, session: ParsedCopilotSession) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sessions (workspace_id, source, external_id, title, metadata)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, source, external_id)
            DO UPDATE SET title = EXCLUDED.title,
                          metadata = sessions.metadata || EXCLUDED.metadata,
                          updated_at = now()
            RETURNING id::text
            """,
            (
                workspace_id,
                SOURCE,
                session.session_id,
                session.title,
                Jsonb(
                    {
                        "index_entry": session.index_entry,
                        "has_editing_context": session.has_editing_context,
                        "editing_content_count": session.editing_session.content_count,
                    }
                ),
            ),
        )
        session_row_id = cur.fetchone()[0]

        for line in session.chat_lines:
            source_id = f"{SOURCE}:{session.session_id}:chat:{line.ordinal}"
            redacted_raw, event_findings = redact_value(line.raw)
            redacted_content, content_findings = redact_text(line.content)
            cur.execute(
                """
                INSERT INTO events (
                    workspace_id,
                    session_id,
                    source,
                    source_id,
                    event_type,
                    payload
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (source, source_id)
                DO UPDATE SET payload = EXCLUDED.payload
                RETURNING id::text
                """,
                (
                    workspace_id,
                    session_row_id,
                    SOURCE,
                    source_id,
                    f"chat.kind.{line.kind}",
                    Jsonb(redacted_raw),
                ),
            )
            event_id = cur.fetchone()[0]
            insert_redaction_findings(cur, workspace_id, SOURCE, source_id, event_findings)
            cur.execute(
                """
                INSERT INTO messages (session_id, raw_event_id, role, ordinal, content, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, ordinal)
                DO UPDATE SET content = EXCLUDED.content,
                              metadata = EXCLUDED.metadata,
                              raw_event_id = EXCLUDED.raw_event_id
                """,
                (
                    session_row_id,
                    event_id,
                    infer_role(line.kind, redacted_content),
                    line.ordinal,
                    redacted_content,
                    Jsonb({"kind": line.kind, "source": "chatSessions"}),
                ),
            )
            insert_redaction_findings(
                cur,
                workspace_id,
                SOURCE,
                f"{source_id}:message",
                content_findings,
            )

        for event in session.transcript_events:
            source_id = f"{SOURCE}:{session.session_id}:transcript:{event.ordinal}"
            redacted_raw, findings = redact_value(event.raw)
            cur.execute(
                """
                INSERT INTO events (
                    workspace_id,
                    session_id,
                    source,
                    source_id,
                    event_type,
                    payload
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (source, source_id)
                DO UPDATE SET payload = EXCLUDED.payload
                """,
                (
                    workspace_id,
                    session_row_id,
                    SOURCE,
                    source_id,
                    event.event_type,
                    Jsonb(redacted_raw),
                ),
            )
            insert_redaction_findings(cur, workspace_id, SOURCE, source_id, findings)

        delete_stale_message_rows(
            cur,
            session_row_id=session_row_id,
            current_ordinals=[line.ordinal for line in session.chat_lines],
        )

        if session.editing_session.state:
            initial_contents = session.editing_session.state.get("initialFileContents", [])
            for file_uri, content_hash in initial_contents:
                cur.execute(
                    """
                    INSERT INTO file_snapshots (
                        workspace_id,
                        session_id,
                        file_uri,
                        content_hash,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (workspace_id, file_uri, content_hash)
                    DO UPDATE SET metadata = file_snapshots.metadata || EXCLUDED.metadata
                    """,
                    (
                        workspace_id,
                        session_row_id,
                        file_uri,
                        content_hash,
                        Jsonb({"source": "chatEditingSessions", "session_id": session.session_id}),
                    ),
                )

    conn.commit()
    return session_row_id


def infer_role(kind: int | None, content: str) -> str:
    if kind == 2:
        return "assistant_or_tool"
    if kind == 1 and content and len(content) > 20:
        return "metadata_or_text"
    return "metadata"


def insert_redaction_findings(
    cur: Cursor,
    workspace_id: str,
    source: str,
    source_id: str,
    findings: list[RedactionFinding],
) -> None:
    for finding in findings:
        cur.execute(
            """
            INSERT INTO redaction_findings (
                workspace_id,
                source,
                source_id,
                finding_type,
                action,
                metadata
            )
            SELECT %s, %s, %s, %s, %s, %s
            WHERE NOT EXISTS (
                SELECT 1
                FROM redaction_findings
                WHERE workspace_id = %s
                  AND source = %s
                  AND source_id = %s
                  AND finding_type = %s
                  AND metadata->>'path' = %s
            )
            """,
            (
                workspace_id,
                source,
                source_id,
                finding.finding_type,
                finding.action,
                Jsonb({"path": finding.path, **finding.metadata}),
                workspace_id,
                source,
                source_id,
                finding.finding_type,
                finding.path,
            ),
        )


def delete_stale_message_rows(
    cur: Cursor,
    session_row_id: str,
    current_ordinals: list[int],
) -> None:
    if current_ordinals:
        ordinal_filter = "AND NOT (m.ordinal = ANY(%s))"
        params = (session_row_id, current_ordinals)
    else:
        ordinal_filter = ""
        params = (session_row_id,)

    cur.execute(
        f"""
        DELETE FROM embeddings e
        USING messages m
        WHERE e.target_table = 'messages'
          AND e.target_id = m.id
          AND m.session_id = %s
          {ordinal_filter}
        """,
        params,
    )
    cur.execute(
        f"""
        DELETE FROM messages m
        WHERE m.session_id = %s
          {ordinal_filter}
        """,
        params,
    )


def record_agent_action(
    conn: Connection,
    workspace_id: str,
    agent_name: str,
    action_type: str,
    summary: str,
    intent: str | None = None,
    status: str = "recorded",
    metadata: dict[str, Any] | None = None,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agents (name, kind)
            VALUES (%s, %s)
            ON CONFLICT (name, kind) DO UPDATE SET name = EXCLUDED.name
            RETURNING id::text
            """,
            (agent_name, "coding-agent"),
        )
        agent_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO agent_actions (
                workspace_id,
                agent_id,
                action_type,
                intent,
                status,
                summary,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id::text
            """,
            (
                workspace_id,
                agent_id,
                action_type,
                intent,
                status,
                summary,
                Jsonb(metadata or {}),
            ),
        )
        action_id = cur.fetchone()[0]
    conn.commit()
    return action_id


def upsert_agent(conn: Connection, agent_name: str, kind: str = "coding-agent") -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agents (name, kind)
            VALUES (%s, %s)
            ON CONFLICT (name, kind) DO UPDATE SET name = EXCLUDED.name
            RETURNING id::text
            """,
            (agent_name, kind),
        )
        agent_id = cur.fetchone()[0]
    return agent_id


def reserve_files(
    conn: Connection,
    workspace_id: str,
    agent_name: str,
    file_paths: list[str],
    purpose: str = "",
    ttl_minutes: int | None = 120,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cleanup_expired_reservations(cur, workspace_id)
        agent_id = upsert_agent(conn, agent_name)
        conflicts = active_file_reservations(cur, workspace_id, file_paths)
        reservation_ids: list[str] = []
        for file_path in file_paths:
            cur.execute(
                """
                INSERT INTO file_reservations (
                    workspace_id,
                    agent_id,
                    file_path,
                    purpose,
                    expires_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    CASE
                        WHEN %s::integer IS NULL THEN NULL
                        ELSE now() + make_interval(mins => %s::integer)
                    END
                )
                RETURNING id::text
                """,
                (workspace_id, agent_id, file_path, purpose, ttl_minutes, ttl_minutes),
            )
            reservation_id = cur.fetchone()[0]
            reservation_ids.append(reservation_id)
            record_reservation_event_cursor(
                cur,
                workspace_id=workspace_id,
                reservation_kind="file",
                reservation_id=reservation_id,
                agent_id=agent_id,
                action="created",
                subject=file_path,
                metadata={
                    "purpose": purpose,
                    "ttl_minutes": ttl_minutes,
                    "conflict_count": len(conflicts),
                },
            )
    conn.commit()
    return {
        "reservation_ids": reservation_ids,
        "conflicts": conflicts,
    }


def release_reservation(
    conn: Connection,
    workspace_id: str,
    reservation_id: str | None = None,
    file_path: str | None = None,
    agent_name: str | None = None,
) -> int:
    if not reservation_id and not file_path:
        raise ValueError("reservation_id or file_path is required")

    with conn.cursor() as cur:
        agent_filter = ""
        params: list[Any] = [workspace_id]
        if reservation_id:
            target_filter = "id::text = %s"
            params.append(reservation_id)
        else:
            target_filter = "file_path = %s"
            params.append(file_path)

        if agent_name:
            agent_filter = """
              AND agent_id IN (
                  SELECT id FROM agents WHERE name = %s AND kind = 'coding-agent'
              )
            """
            params.append(agent_name)

        cur.execute(
            f"""
            UPDATE file_reservations
            SET released_at = now()
            WHERE workspace_id = %s
              AND released_at IS NULL
              AND {target_filter}
              {agent_filter}
            RETURNING id::text, agent_id::text, file_path, purpose
            """,
            params,
        )
        rows = cur.fetchall()
        for row in rows:
            record_reservation_event_cursor(
                cur,
                workspace_id=workspace_id,
                reservation_kind="file",
                reservation_id=row[0],
                agent_id=row[1],
                action="released",
                subject=row[2],
                metadata={"purpose": row[3], "released_by": agent_name},
            )
        released = len(rows)
    conn.commit()
    return released


def renew_reservation(
    conn: Connection,
    workspace_id: str,
    reservation_id: str | None = None,
    file_path: str | None = None,
    agent_name: str | None = None,
    ttl_minutes: int | None = 120,
) -> int:
    if not reservation_id and not file_path:
        raise ValueError("reservation_id or file_path is required")

    with conn.cursor() as cur:
        cleanup_expired_reservations(cur, workspace_id)
        agent_filter = ""
        target_params: list[Any] = [workspace_id]
        if reservation_id:
            target_filter = "id::text = %s"
            target_params.append(reservation_id)
        else:
            target_filter = "file_path = %s"
            target_params.append(file_path)

        if agent_name:
            agent_filter = """
              AND agent_id IN (
                  SELECT id FROM agents WHERE name = %s AND kind = 'coding-agent'
              )
            """
            target_params.append(agent_name)

        query_params = [ttl_minutes, ttl_minutes, ttl_minutes, *target_params]
        cur.execute(
            f"""
            UPDATE file_reservations
            SET expires_at = CASE
                    WHEN %s::integer IS NULL THEN NULL
                    ELSE now() + make_interval(mins => %s::integer)
                END,
                metadata = metadata || jsonb_build_object(
                    'renewed_at', now(),
                    'renewal_ttl_minutes', %s::integer
                )
            WHERE workspace_id = %s
              AND released_at IS NULL
              AND {target_filter}
              {agent_filter}
            RETURNING id::text, agent_id::text, file_path, purpose, expires_at
            """,
            query_params,
        )
        rows = cur.fetchall()
        for row in rows:
            record_reservation_event_cursor(
                cur,
                workspace_id=workspace_id,
                reservation_kind="file",
                reservation_id=row[0],
                agent_id=row[1],
                action="renewed",
                subject=row[2],
                metadata={
                    "purpose": row[3],
                    "renewed_by": agent_name,
                    "ttl_minutes": ttl_minutes,
                    "expires_at": row[4].isoformat() if row[4] else None,
                },
            )
        renewed = len(rows)
    conn.commit()
    return renewed


def active_file_reservations(
    cur: Cursor,
    workspace_id: str,
    file_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    cleanup_expired_reservations(cur, workspace_id)
    path_filter = ""
    params: list[Any] = [workspace_id]
    if file_paths:
        path_filter = "AND fr.file_path = ANY(%s)"
        params.append(file_paths)

    cur.execute(
        f"""
        SELECT
            fr.id::text,
            fr.file_path,
            fr.purpose,
            fr.expires_at,
            fr.created_at,
            a.name
        FROM file_reservations fr
        LEFT JOIN agents a ON a.id = fr.agent_id
        WHERE fr.workspace_id = %s
          AND fr.released_at IS NULL
          AND (fr.expires_at IS NULL OR fr.expires_at > now())
          {path_filter}
        ORDER BY fr.created_at DESC
        """,
        params,
    )
    rows = cur.fetchall()
    return [
        {
            "reservation_id": row[0],
            "file_path": row[1],
            "purpose": row[2],
            "expires_at": row[3].isoformat() if row[3] else None,
            "created_at": row[4].isoformat() if row[4] else None,
            "agent_name": row[5],
        }
        for row in rows
    ]


def list_active_file_reservations(
    conn: Connection,
    workspace_id: str,
    file_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        return active_file_reservations(cur, workspace_id, file_paths)


def reserve_symbols(
    conn: Connection,
    workspace_id: str,
    agent_name: str,
    symbols: list[str],
    purpose: str = "",
    ttl_minutes: int | None = 120,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cleanup_expired_reservations(cur, workspace_id)
        agent_id = upsert_agent(conn, agent_name)
        targets = resolve_symbol_targets(cur, workspace_id, symbols)
        conflicts = active_symbol_reservations(cur, workspace_id, symbols)
        reservation_ids: list[str] = []
        for symbol in symbols:
            target = targets.get(symbol, {})
            cur.execute(
                """
                INSERT INTO symbol_reservations (
                    workspace_id,
                    agent_id,
                    symbol,
                    qualified_name,
                    file_path,
                    purpose,
                    expires_at,
                    metadata
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    CASE
                        WHEN %s::integer IS NULL THEN NULL
                        ELSE now() + make_interval(mins => %s::integer)
                    END,
                    %s
                )
                RETURNING id::text
                """,
                (
                    workspace_id,
                    agent_id,
                    symbol,
                    target.get("qualified_name"),
                    target.get("file_path"),
                    purpose,
                    ttl_minutes,
                    ttl_minutes,
                    Jsonb({"requested_symbol": symbol}),
                ),
            )
            reservation_id = cur.fetchone()[0]
            reservation_ids.append(reservation_id)
            record_reservation_event_cursor(
                cur,
                workspace_id=workspace_id,
                reservation_kind="symbol",
                reservation_id=reservation_id,
                agent_id=agent_id,
                action="created",
                subject=target.get("qualified_name") or symbol,
                metadata={
                    "purpose": purpose,
                    "ttl_minutes": ttl_minutes,
                    "requested_symbol": symbol,
                    "qualified_name": target.get("qualified_name"),
                    "file_path": target.get("file_path"),
                    "conflict_count": len(conflicts),
                },
            )
    conn.commit()
    return {
        "reservation_ids": reservation_ids,
        "conflicts": conflicts,
        "resolved_symbols": targets,
    }


def resolve_symbol_targets(
    cur: Cursor,
    workspace_id: str,
    symbols: list[str],
) -> dict[str, dict[str, str | None]]:
    if not symbols:
        return {}

    cur.execute(
        """
        SELECT name, qualified_name, file_path
        FROM code_entities
        WHERE workspace_id = %s
          AND (name = ANY(%s) OR qualified_name = ANY(%s))
        ORDER BY qualified_name NULLS LAST, file_path
        """,
        (workspace_id, symbols, symbols),
    )
    targets: dict[str, dict[str, str | None]] = {}
    for name, qualified_name, file_path in cur.fetchall():
        for requested in {name, qualified_name} & set(symbols):
            if requested not in targets or requested == qualified_name:
                targets[requested] = {
                    "qualified_name": qualified_name,
                    "file_path": file_path,
                }
    return targets


def release_symbol_reservation(
    conn: Connection,
    workspace_id: str,
    reservation_id: str | None = None,
    symbol: str | None = None,
    agent_name: str | None = None,
) -> int:
    if not reservation_id and not symbol:
        raise ValueError("reservation_id or symbol is required")

    with conn.cursor() as cur:
        agent_filter = ""
        params: list[Any] = [workspace_id]
        if reservation_id:
            target_filter = "id::text = %s"
            params.append(reservation_id)
        else:
            target_filter = "(symbol = %s OR qualified_name = %s)"
            params.extend([symbol, symbol])

        if agent_name:
            agent_filter = """
              AND agent_id IN (
                  SELECT id FROM agents WHERE name = %s AND kind = 'coding-agent'
              )
            """
            params.append(agent_name)

        cur.execute(
            f"""
            UPDATE symbol_reservations
            SET released_at = now()
            WHERE workspace_id = %s
              AND released_at IS NULL
              AND {target_filter}
              {agent_filter}
            RETURNING id::text, agent_id::text, symbol, qualified_name, file_path, purpose
            """,
            params,
        )
        rows = cur.fetchall()
        for row in rows:
            record_reservation_event_cursor(
                cur,
                workspace_id=workspace_id,
                reservation_kind="symbol",
                reservation_id=row[0],
                agent_id=row[1],
                action="released",
                subject=row[3] or row[2],
                metadata={
                    "symbol": row[2],
                    "qualified_name": row[3],
                    "file_path": row[4],
                    "purpose": row[5],
                    "released_by": agent_name,
                },
            )
        released = len(rows)
    conn.commit()
    return released


def renew_symbol_reservation(
    conn: Connection,
    workspace_id: str,
    reservation_id: str | None = None,
    symbol: str | None = None,
    agent_name: str | None = None,
    ttl_minutes: int | None = 120,
) -> int:
    if not reservation_id and not symbol:
        raise ValueError("reservation_id or symbol is required")

    with conn.cursor() as cur:
        cleanup_expired_reservations(cur, workspace_id)
        agent_filter = ""
        target_params: list[Any] = [workspace_id]
        if reservation_id:
            target_filter = "id::text = %s"
            target_params.append(reservation_id)
        else:
            target_filter = "(symbol = %s OR qualified_name = %s)"
            target_params.extend([symbol, symbol])

        if agent_name:
            agent_filter = """
              AND agent_id IN (
                  SELECT id FROM agents WHERE name = %s AND kind = 'coding-agent'
              )
            """
            target_params.append(agent_name)

        query_params = [ttl_minutes, ttl_minutes, ttl_minutes, *target_params]
        cur.execute(
            f"""
            UPDATE symbol_reservations
            SET expires_at = CASE
                    WHEN %s::integer IS NULL THEN NULL
                    ELSE now() + make_interval(mins => %s::integer)
                END,
                metadata = metadata || jsonb_build_object(
                    'renewed_at', now(),
                    'renewal_ttl_minutes', %s::integer
                )
            WHERE workspace_id = %s
              AND released_at IS NULL
              AND {target_filter}
              {agent_filter}
            RETURNING
                id::text,
                agent_id::text,
                symbol,
                qualified_name,
                file_path,
                purpose,
                expires_at
            """,
            query_params,
        )
        rows = cur.fetchall()
        for row in rows:
            record_reservation_event_cursor(
                cur,
                workspace_id=workspace_id,
                reservation_kind="symbol",
                reservation_id=row[0],
                agent_id=row[1],
                action="renewed",
                subject=row[3] or row[2],
                metadata={
                    "symbol": row[2],
                    "qualified_name": row[3],
                    "file_path": row[4],
                    "purpose": row[5],
                    "renewed_by": agent_name,
                    "ttl_minutes": ttl_minutes,
                    "expires_at": row[6].isoformat() if row[6] else None,
                },
            )
        renewed = len(rows)
    conn.commit()
    return renewed


def active_symbol_reservations(
    cur: Cursor,
    workspace_id: str,
    symbols: list[str] | None = None,
) -> list[dict[str, Any]]:
    cleanup_expired_reservations(cur, workspace_id)
    symbol_filter = ""
    params: list[Any] = [workspace_id]
    if symbols:
        symbol_filter = "AND (sr.symbol = ANY(%s) OR sr.qualified_name = ANY(%s))"
        params.extend([symbols, symbols])

    cur.execute(
        f"""
        SELECT
            sr.id::text,
            sr.symbol,
            sr.qualified_name,
            sr.file_path,
            sr.purpose,
            sr.expires_at,
            sr.created_at,
            a.name
        FROM symbol_reservations sr
        LEFT JOIN agents a ON a.id = sr.agent_id
        WHERE sr.workspace_id = %s
          AND sr.released_at IS NULL
          AND (sr.expires_at IS NULL OR sr.expires_at > now())
          {symbol_filter}
        ORDER BY sr.created_at DESC
        """,
        params,
    )
    rows = cur.fetchall()
    return [
        {
            "reservation_id": row[0],
            "symbol": row[1],
            "qualified_name": row[2],
            "file_path": row[3],
            "purpose": row[4],
            "expires_at": row[5].isoformat() if row[5] else None,
            "created_at": row[6].isoformat() if row[6] else None,
            "agent_name": row[7],
        }
        for row in rows
    ]


def list_active_symbol_reservations(
    conn: Connection,
    workspace_id: str,
    symbols: list[str] | None = None,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        return active_symbol_reservations(cur, workspace_id, symbols)


def record_reservation_event_cursor(
    cur: Cursor,
    workspace_id: str,
    reservation_kind: str,
    reservation_id: str | None,
    agent_id: str | None,
    action: str,
    subject: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO reservation_events (
            workspace_id,
            reservation_kind,
            reservation_id,
            agent_id,
            action,
            subject,
            metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            workspace_id,
            reservation_kind,
            reservation_id,
            agent_id,
            action,
            subject,
            Jsonb(metadata or {}),
        ),
    )


def list_reservation_events(
    conn: Connection,
    workspace_id_or_uri: str | None = None,
    reservation_kind: str | None = None,
    action: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        workspace_id = (
            resolve_workspace_id_cursor(cur, workspace_id_or_uri) if workspace_id_or_uri else None
        )
        if workspace_id_or_uri and not workspace_id:
            return []
        filters: list[str] = []
        params: list[Any] = []
        if workspace_id:
            filters.append("re.workspace_id = %s::uuid")
            params.append(workspace_id)
        if reservation_kind:
            filters.append("re.reservation_kind = %s")
            params.append(reservation_kind)
        if action:
            filters.append("re.action = %s")
            params.append(action)
        where_clause = "WHERE " + " AND ".join(filters) if filters else ""
        params.append(limit)
        cur.execute(
            f"""
            SELECT
                re.id::text,
                re.workspace_id::text,
                w.root_uri,
                re.reservation_kind,
                re.reservation_id::text,
                a.name,
                re.action,
                re.subject,
                re.metadata,
                re.created_at
            FROM reservation_events re
            JOIN workspaces w ON w.id = re.workspace_id
            LEFT JOIN agents a ON a.id = re.agent_id
            {where_clause}
            ORDER BY re.created_at DESC
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
    return [reservation_event_result(row) for row in rows]


def reservation_event_result(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "event_id": row[0],
        "workspace_id": row[1],
        "workspace_uri": row[2],
        "reservation_kind": row[3],
        "reservation_id": row[4],
        "agent_name": row[5],
        "action": row[6],
        "subject": row[7],
        "metadata": row[8],
        "created_at": row[9].isoformat() if row[9] else None,
    }


def record_handoff_summary(
    conn: Connection,
    workspace_id: str,
    from_agent_name: str,
    summary: str,
    to_agent_name: str | None = None,
    next_steps: list[str] | None = None,
    blocked_on: list[str] | None = None,
    status: str = "open",
    metadata: dict[str, Any] | None = None,
) -> str:
    with conn.cursor() as cur:
        from_agent_id = upsert_agent(conn, from_agent_name)
        to_agent_id = upsert_agent(conn, to_agent_name) if to_agent_name else None
        cur.execute(
            """
            INSERT INTO handoff_summaries (
                workspace_id,
                from_agent_id,
                to_agent_id,
                to_agent_name,
                status,
                summary,
                next_steps,
                blocked_on,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id::text
            """,
            (
                workspace_id,
                from_agent_id,
                to_agent_id,
                to_agent_name,
                status,
                summary,
                Jsonb(next_steps or []),
                Jsonb(blocked_on or []),
                Jsonb(metadata or {}),
            ),
        )
        handoff_id = cur.fetchone()[0]
    conn.commit()
    return handoff_id


def list_handoff_summaries(
    conn: Connection,
    workspace_id_or_uri: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if workspace_id_or_uri:
        filters.append(
            """
            (w.id::text = %s OR w.root_uri = %s OR EXISTS (
                SELECT 1
                FROM workspace_aliases wa
                WHERE wa.workspace_id = w.id
                  AND wa.alias_uri = %s
            ))
            """
        )
        params.extend([workspace_id_or_uri, workspace_id_or_uri, workspace_id_or_uri])
    if status:
        filters.append("hs.status = %s")
        params.append(status)
    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                hs.id::text,
                w.id::text,
                w.root_uri,
                from_agent.name,
                to_agent.name,
                hs.to_agent_name,
                hs.status,
                hs.summary,
                hs.next_steps,
                hs.blocked_on,
                hs.metadata,
                hs.closed_at,
                hs.created_at
            FROM handoff_summaries hs
            JOIN workspaces w ON w.id = hs.workspace_id
            LEFT JOIN agents from_agent ON from_agent.id = hs.from_agent_id
            LEFT JOIN agents to_agent ON to_agent.id = hs.to_agent_id
            {where_clause}
            ORDER BY hs.created_at DESC
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
    return [
        {
            "handoff_id": row[0],
            "workspace_id": row[1],
            "workspace_uri": row[2],
            "from_agent_name": row[3],
            "to_agent_name": row[4] or row[5],
            "status": row[6],
            "summary": row[7],
            "next_steps": row[8],
            "blocked_on": row[9],
            "metadata": row[10],
            "closed_at": row[11].isoformat() if row[11] else None,
            "created_at": row[12].isoformat() if row[12] else None,
        }
        for row in rows
    ]


def close_handoff_summary(
    conn: Connection,
    handoff_id: str,
    status: str = "closed",
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE handoff_summaries
            SET status = %s,
                closed_at = now()
            WHERE id::text = %s
              AND closed_at IS NULL
            """,
            (status, handoff_id),
        )
        closed = cur.rowcount
    conn.commit()
    return closed


def cleanup_expired_reservations(cur: Cursor, workspace_id: str | None = None) -> dict[str, int]:
    workspace_filter = "AND workspace_id = %s" if workspace_id else ""
    params = (workspace_id,) if workspace_id else ()
    cur.execute(
        f"""
        UPDATE file_reservations
        SET released_at = now(),
            metadata = metadata || '{{"release_reason": "expired"}}'::jsonb
        WHERE released_at IS NULL
          AND expires_at IS NOT NULL
          AND expires_at <= now()
          {workspace_filter}
        RETURNING workspace_id::text, id::text, agent_id::text, file_path, purpose
        """,
        params,
    )
    file_rows = cur.fetchall()
    for row in file_rows:
        record_reservation_event_cursor(
            cur,
            workspace_id=row[0],
            reservation_kind="file",
            reservation_id=row[1],
            agent_id=row[2],
            action="expired",
            subject=row[3],
            metadata={"purpose": row[4]},
        )
    cur.execute(
        f"""
        UPDATE symbol_reservations
        SET released_at = now(),
            metadata = metadata || '{{"release_reason": "expired"}}'::jsonb
        WHERE released_at IS NULL
          AND expires_at IS NOT NULL
          AND expires_at <= now()
          {workspace_filter}
        RETURNING
            workspace_id::text,
            id::text,
            agent_id::text,
            symbol,
            qualified_name,
            file_path,
            purpose
        """,
        params,
    )
    symbol_rows = cur.fetchall()
    for row in symbol_rows:
        record_reservation_event_cursor(
            cur,
            workspace_id=row[0],
            reservation_kind="symbol",
            reservation_id=row[1],
            agent_id=row[2],
            action="expired",
            subject=row[4] or row[3],
            metadata={
                "symbol": row[3],
                "qualified_name": row[4],
                "file_path": row[5],
                "purpose": row[6],
            },
        )
    return {"file_reservations": len(file_rows), "symbol_reservations": len(symbol_rows)}


def cleanup_expired_reservations_for_workspace(
    conn: Connection,
    workspace_id: str | None = None,
) -> dict[str, int]:
    with conn.cursor() as cur:
        result = cleanup_expired_reservations(cur, workspace_id)
    conn.commit()
    return result
