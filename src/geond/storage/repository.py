from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from geond.adapters.codex import SOURCE as CODEX_SOURCE
from geond.adapters.codex import ParsedCodexSession
from geond.adapters.vscode_copilot import SOURCE, ParsedCopilotSession


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
                    Jsonb(event.raw),
                ),
            )

        for message in session.messages:
            source_id = f"{CODEX_SOURCE}:{session.session_id}:{message.ordinal}"
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
                    message.content,
                    Jsonb(message.metadata),
                ),
            )

    conn.commit()
    return session_row_id


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
                    Jsonb(line.raw),
                ),
            )
            event_id = cur.fetchone()[0]
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
                    infer_role(line.kind, line.content),
                    line.ordinal,
                    line.content,
                    Jsonb({"kind": line.kind, "source": "chatSessions"}),
                ),
            )

        for event in session.transcript_events:
            source_id = f"{SOURCE}:{session.session_id}:transcript:{event.ordinal}"
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
                    Jsonb(event.raw),
                ),
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
