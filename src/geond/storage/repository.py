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
            reservation_ids.append(cur.fetchone()[0])
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
            """,
            params,
        )
        released = cur.rowcount
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
            """,
            query_params,
        )
        renewed = cur.rowcount
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
            reservation_ids.append(cur.fetchone()[0])
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
            """,
            params,
        )
        released = cur.rowcount
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
            """,
            query_params,
        )
        renewed = cur.rowcount
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
        filters.append("(w.id::text = %s OR w.root_uri = %s)")
        params.extend([workspace_id_or_uri, workspace_id_or_uri])
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
        """,
        params,
    )
    file_count = cur.rowcount
    cur.execute(
        f"""
        UPDATE symbol_reservations
        SET released_at = now(),
            metadata = metadata || '{{"release_reason": "expired"}}'::jsonb
        WHERE released_at IS NULL
          AND expires_at IS NOT NULL
          AND expires_at <= now()
          {workspace_filter}
        """,
        params,
    )
    symbol_count = cur.rowcount
    return {"file_reservations": file_count, "symbol_reservations": symbol_count}


def cleanup_expired_reservations_for_workspace(
    conn: Connection,
    workspace_id: str | None = None,
) -> dict[str, int]:
    with conn.cursor() as cur:
        result = cleanup_expired_reservations(cur, workspace_id)
    conn.commit()
    return result
