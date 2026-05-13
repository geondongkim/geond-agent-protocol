from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from geond.redaction import redact_text
from geond.storage.repository import resolve_workspace_id_cursor


def seed_sample_workspace(conn: Connection) -> dict[str, Any]:
    workspace_uri = "file:///sample/geond"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO workspaces (root_uri, name, metadata)
            VALUES (%s, %s, %s)
            ON CONFLICT (root_uri)
            DO UPDATE SET name = EXCLUDED.name,
                          metadata = workspaces.metadata || EXCLUDED.metadata
            RETURNING id::text
            """,
            (workspace_uri, "geond-sample", Jsonb({"source": "seed"})),
        )
        workspace_row = cur.fetchone()
        if workspace_row is None:
            raise RuntimeError("Failed to seed sample workspace")
        workspace_id = workspace_row[0]
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
                "seed",
                "sample-session",
                "Sample Geond memory",
                Jsonb({"source": "seed"}),
            ),
        )
        session_row = cur.fetchone()
        if session_row is None:
            raise RuntimeError("Failed to seed sample session")
        session_id = session_row[0]
        messages = [
            ("user", 0, "왜 service.py 파일이 바뀌었어?"),
            (
                "assistant",
                1,
                "service.py was changed to keep database initialization inside app_context.",
            ),
        ]
        for role, ordinal, content in messages:
            redacted_content, _ = redact_text(content)
            cur.execute(
                """
                INSERT INTO messages (session_id, role, ordinal, content, metadata)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (session_id, ordinal)
                DO UPDATE SET role = EXCLUDED.role,
                              content = EXCLUDED.content,
                              metadata = EXCLUDED.metadata
                """,
                (
                    session_id,
                    role,
                    ordinal,
                    redacted_content,
                    Jsonb({"source": "seed"}),
                ),
            )
        cur.execute(
            """
            INSERT INTO file_snapshots (
                workspace_id,
                session_id,
                file_uri,
                file_path,
                content_hash,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, file_uri, content_hash)
            DO UPDATE SET metadata = file_snapshots.metadata || EXCLUDED.metadata
            """,
            (
                workspace_id,
                session_id,
                "file:///sample/geond/service.py",
                "service.py",
                "sample-service-hash",
                Jsonb({"source": "seed"}),
            ),
        )
        cur.execute(
            """
            SELECT id::text
            FROM changesets
            WHERE workspace_id = %s
              AND git_commit = %s
            LIMIT 1
            """,
            (workspace_id, "sample-service-change"),
        )
        changeset_row = cur.fetchone()
        if changeset_row:
            changeset_id = changeset_row[0]
            cur.execute(
                """
                UPDATE changesets
                SET session_id = %s,
                    branch = %s,
                    intent = %s,
                    summary = %s,
                    metadata = changesets.metadata || %s
                WHERE id = %s::uuid
                """,
                (
                    session_id,
                    "main",
                    "explain sample service.py change",
                    "Kept database initialization inside app_context.",
                    Jsonb({"source": "seed"}),
                    changeset_id,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO changesets (
                    workspace_id,
                    session_id,
                    git_commit,
                    branch,
                    intent,
                    summary,
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id::text
                """,
                (
                    workspace_id,
                    session_id,
                    "sample-service-change",
                    "main",
                    "explain sample service.py change",
                    "Kept database initialization inside app_context.",
                    Jsonb({"source": "seed"}),
                ),
            )
            changeset_id = cur.fetchone()[0]

        cur.execute(
            """
            SELECT id::text
            FROM change_files
            WHERE changeset_id = %s::uuid
              AND file_path = %s
            LIMIT 1
            """,
            (changeset_id, "service.py"),
        )
        change_file_row = cur.fetchone()
        if change_file_row:
            cur.execute(
                """
                UPDATE change_files
                SET status = %s,
                    additions = %s,
                    deletions = %s,
                    patch = %s,
                    metadata = change_files.metadata || %s
                WHERE id = %s::uuid
                """,
                (
                    "modified",
                    2,
                    1,
                    SAMPLE_SERVICE_PATCH,
                    Jsonb({"source": "seed"}),
                    change_file_row[0],
                ),
            )
        else:
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
                """,
                (
                    changeset_id,
                    "service.py",
                    "modified",
                    2,
                    1,
                    SAMPLE_SERVICE_PATCH,
                    Jsonb({"source": "seed"}),
                ),
            )
    conn.commit()
    return {
        "workspace_id": workspace_id,
        "workspace_uri": workspace_uri,
        "session_id": session_id,
        "messages": len(messages),
    }


SAMPLE_SERVICE_PATCH = """diff --git a/service.py b/service.py
--- a/service.py
+++ b/service.py
@@ -1,3 +1,4 @@
 def create_app():
-    init_db()
+    with app.app_context():
+        init_db()
     return app
"""


def purge_workspace(conn: Connection, workspace_id_or_uri: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        workspace_id = resolve_workspace_id_cursor(cur, workspace_id_or_uri)
        if not workspace_id:
            return {
                "status": "not_found",
                "workspace_id_or_uri": workspace_id_or_uri,
                "deleted": {},
            }
        cur.execute(
            """
            SELECT id::text, root_uri, name
            FROM workspaces
            WHERE id::text = %s
            """,
            (workspace_id,),
        )
        workspace = cur.fetchone()

        workspace_id = workspace[0]
        deleted = count_workspace_rows(cur, workspace_id)
        cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
    conn.commit()
    return {
        "status": "deleted",
        "workspace_id": workspace[0],
        "workspace_uri": workspace[1],
        "workspace_name": workspace[2],
        "deleted": deleted,
    }


def count_workspace_rows(cur: Any, workspace_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in (
        "sessions",
        "events",
        "file_snapshots",
        "changesets",
        "change_entities",
        "code_entities",
        "code_edges",
        "embeddings",
        "summaries",
        "agent_actions",
        "file_reservations",
        "symbol_reservations",
        "reservation_events",
        "handoff_summaries",
        "benchmark_runs",
        "redaction_findings",
        "workspace_aliases",
        "workspace_fingerprints",
    ):
        cur.execute(f"SELECT count(*) FROM {table} WHERE workspace_id = %s", (workspace_id,))
        counts[table] = cur.fetchone()[0]
    cur.execute(
        """
        SELECT count(*)
        FROM messages m
        JOIN sessions s ON s.id = m.session_id
        WHERE s.workspace_id = %s
        """,
        (workspace_id,),
    )
    counts["messages"] = cur.fetchone()[0]
    return counts
