from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from geond.redaction import redact_text
from geond.storage.repository import resolve_workspace_id_cursor


def _table_exists(cur, table_name: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (table_name,))
    return cur.fetchone()[0] is not None


def _reset_seed_workspace(cur, workspace_id: str) -> None:
    """Keep seed-sample deterministic for README screenshots."""
    if _table_exists(cur, "llm_usage_events"):
        cur.execute("DELETE FROM llm_usage_events WHERE workspace_id = %s", (workspace_id,))
    for table in [
        "redaction_findings",
        "benchmark_runs",
        "reservation_events",
        "file_reservations",
        "symbol_reservations",
        "handoff_summaries",
        "agent_actions",
        "code_edges",
        "change_entities",
        "changesets",
        "code_entities",
        "file_snapshots",
        "events",
        "sessions",
    ]:
        cur.execute(f"DELETE FROM {table} WHERE workspace_id = %s", (workspace_id,))


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
        _reset_seed_workspace(cur, workspace_id)
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
    _seed_showcase_dashboard(conn, workspace_id, session_id)
    return {
        "workspace_id": workspace_id,
        "workspace_uri": workspace_uri,
        "session_id": session_id,
        "messages": len(messages) + 14,
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


def _seed_showcase_dashboard(conn: Connection, workspace_id: str, sample_session_id: str) -> None:
    agents = [
        ("codex", "coding"),
        ("copilot", "review"),
        ("claude", "handoff"),
        ("qa-orchestrator", "qa"),
        ("pm-agent", "orchestration"),
    ]
    sessions = [
        (
            "codex",
            "codex-ui-polish",
            "Codex dashboard polish",
            [
                (
                    "user",
                    "The README GIF is stale. Open Geond, make the dashboard prettier, "
                    "and capture fresh proof.",
                ),
                (
                    "assistant",
                    "I will refresh the local-first dashboard, add richer sample evidence, "
                    "then re-record the browser walkthrough.",
                ),
                (
                    "assistant",
                    "Mission Control now groups active work, sessions, handoffs, code risk, "
                    "and timeline evidence for a cleaner demo path.",
                ),
                ("user", "Use the sample workspace if the live workspace is too empty."),
                (
                    "assistant",
                    "The sample seed now includes multiple agents, reservations, handoffs, "
                    "changesets, usage, and review context.",
                ),
            ],
        ),
        (
            "vscode-copilot",
            "copilot-review",
            "Copilot review lane",
            [
                ("user", "Can you check the dashboard_server layout for stale selectors?"),
                (
                    "assistant",
                    "The tab selectors are stable. The browser smoke will verify Mission "
                    "Control, Sessions, Timeline, Graph, and Usage Evidence.",
                ),
                (
                    "assistant",
                    "I flagged the timeline filter and related context panel as the "
                    "highest-risk interaction for regression testing.",
                ),
            ],
        ),
        (
            "claude-code",
            "claude-handoff",
            "Claude handoff prep",
            [
                ("user", "Please package the handoff so the next agent can validate screenshots."),
                (
                    "assistant",
                    "I left tested commands, next actions, and remaining risks in the "
                    "structured handoff.",
                ),
                (
                    "assistant",
                    "The README assets should be replaced only after the screenshots pass "
                    "a nonblank browser check.",
                ),
            ],
        ),
        (
            "qa",
            "qa-validation",
            "QA validation sweep",
            [
                ("user", "Run the dashboard browser smoke and capture all tabs."),
                (
                    "assistant",
                    "The smoke checks every view, applies a Timeline filter, expands related "
                    "context, and writes screenshot evidence.",
                ),
                (
                    "assistant",
                    "The GIF renderer then compresses operations, evidence, and timeline "
                    "review into README-ready clips.",
                ),
            ],
        ),
    ]
    file_snapshots = [
        ("src/geond/dashboard_server.py", "dashboard-server-hash"),
        ("src/geond/storage/maintenance.py", "maintenance-seed-hash"),
        ("scripts/verify_dashboard_browser.py", "browser-smoke-hash"),
        ("scripts/render_dashboard_gifs.py", "gif-renderer-hash"),
        ("README.md", "readme-demo-assets-hash"),
    ]
    entities = [
        (
            "function",
            "mission_control_html",
            "geond.dashboard_server.mission_control_html",
            "src/geond/dashboard_server.py",
            213,
            2618,
        ),
        (
            "function",
            "seed_sample_workspace",
            "geond.storage.maintenance.seed_sample_workspace",
            "src/geond/storage/maintenance.py",
            12,
            224,
        ),
        (
            "function",
            "main",
            "scripts.verify_dashboard_browser.main",
            "scripts/verify_dashboard_browser.py",
            34,
            76,
        ),
        (
            "function",
            "render_frame",
            "scripts.render_dashboard_gifs.render_frame",
            "scripts/render_dashboard_gifs.py",
            59,
            72,
        ),
        ("document", "README Dashboard GIFs", "README.dashboard.assets", "README.md", 75, 86),
    ]
    changesets = [
        (
            "demo-ui-polish",
            "codex/demo-dashboard",
            "Refresh README dashboard visuals",
            "Polished dashboard shell and updated browser-verified GIF assets.",
            [
                ("src/geond/dashboard_server.py", "modified", 96, 38),
                ("README.md", "modified", 6, 3),
            ],
        ),
        (
            "demo-seed-showcase",
            "codex/demo-dashboard",
            "Create rich sample data",
            "Expanded seed-sample with multi-agent sessions, reservations, handoffs, "
            "usage, and graph evidence.",
            [
                ("src/geond/storage/maintenance.py", "modified", 210, 12),
                ("tests/test_maintenance.py", "modified", 14, 2),
            ],
        ),
        (
            "demo-browser-capture",
            "codex/demo-dashboard",
            "Regenerate README GIF captures",
            "Verified dashboard tabs in a browser and rendered operations, evidence, "
            "and timeline GIFs.",
            [
                ("scripts/verify_dashboard_browser.py", "modified", 18, 4),
                ("scripts/render_dashboard_gifs.py", "modified", 8, 1),
                ("docs/assets/geond_dashboard_operations.gif", "modified", 1, 1),
            ],
        ),
    ]

    with conn.cursor() as cur:
        agent_ids = {}
        for name, kind in agents:
            cur.execute(
                """
                INSERT INTO agents (name, kind)
                VALUES (%s, %s)
                ON CONFLICT (name, kind)
                DO UPDATE SET metadata = agents.metadata || %s
                RETURNING id
                """,
                (name, kind, Jsonb({"source": "seed"})),
            )
            agent_ids[name] = cur.fetchone()[0]

        for index, (source, external_id, title, messages) in enumerate(sessions, start=1):
            cur.execute(
                """
                INSERT INTO sessions (workspace_id, source, external_id, title, metadata)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    workspace_id,
                    source,
                    external_id,
                    title,
                    Jsonb({"source": "seed", "demo_order": index}),
                ),
            )
            session_id = cur.fetchone()[0]
            for ordinal, (role, content) in enumerate(messages):
                cur.execute(
                    """
                    INSERT INTO messages (session_id, role, ordinal, content, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (session_id, role, ordinal, redact_text(content)[0], Jsonb({"source": "seed"})),
                )

        snapshot_ids = {}
        for file_path, content_hash in file_snapshots:
            cur.execute(
                """
                INSERT INTO file_snapshots (
                    workspace_id, session_id, file_uri, file_path, content_hash, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    workspace_id,
                    sample_session_id,
                    f"file:///sample/geond/{file_path}",
                    file_path,
                    content_hash,
                    Jsonb({"source": "seed", "demo": True}),
                ),
            )
            snapshot_ids[file_path] = cur.fetchone()[0]

        entity_ids = {}
        for kind, name, qualified_name, file_path, start_line, end_line in entities:
            cur.execute(
                """
                INSERT INTO code_entities (
                    workspace_id, snapshot_id, kind, name, qualified_name,
                    file_path, start_line, end_line, signature, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    workspace_id,
                    snapshot_ids[file_path],
                    kind,
                    name,
                    qualified_name,
                    file_path,
                    start_line,
                    end_line,
                    f"{name}(...)",
                    Jsonb({"source": "seed", "demo": True}),
                ),
            )
            entity_ids[file_path] = cur.fetchone()[0]

        edge_pairs = [
            ("src/geond/storage/maintenance.py", "src/geond/dashboard_server.py", "feeds"),
            ("src/geond/dashboard_server.py", "scripts/verify_dashboard_browser.py", "verified_by"),
            (
                "scripts/verify_dashboard_browser.py",
                "scripts/render_dashboard_gifs.py",
                "captures_for",
            ),
            ("scripts/render_dashboard_gifs.py", "README.md", "publishes_to"),
        ]
        for source_path, target_path, edge_type in edge_pairs:
            cur.execute(
                """
                INSERT INTO code_edges (
                    workspace_id, source_entity_id, target_entity_id,
                    edge_type, confidence, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    workspace_id,
                    entity_ids[source_path],
                    entity_ids[target_path],
                    edge_type,
                    0.92,
                    Jsonb({"source": "seed", "demo": True}),
                ),
            )

        for index, (commit, branch, intent, summary, files) in enumerate(changesets):
            cur.execute(
                """
                INSERT INTO changesets (
                    workspace_id, session_id, git_commit, branch, intent, summary, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    workspace_id,
                    sample_session_id,
                    commit,
                    branch,
                    intent,
                    summary,
                    Jsonb({"source": "seed", "demo_order": index}),
                ),
            )
            changeset_id = cur.fetchone()[0]
            for file_path, status, additions, deletions in files:
                cur.execute(
                    """
                    INSERT INTO change_files (
                        changeset_id, file_path, status, additions, deletions, patch, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        changeset_id,
                        file_path,
                        status,
                        additions,
                        deletions,
                        f"diff --git a/{file_path} b/{file_path}\n",
                        Jsonb({"source": "seed", "demo": True}),
                    ),
                )
                change_file_id = cur.fetchone()[0]
                if file_path in entity_ids:
                    cur.execute(
                        """
                        INSERT INTO change_entities (
                            workspace_id, changeset_id, change_file_id, code_entity_id,
                            match_type, confidence, metadata
                        )
                        VALUES (%s, %s, %s, %s, 'line_range', 0.94, %s)
                        ON CONFLICT (change_file_id, code_entity_id) DO NOTHING
                        """,
                        (
                            workspace_id,
                            changeset_id,
                            change_file_id,
                            entity_ids[file_path],
                            Jsonb({"source": "seed", "demo": True}),
                        ),
                    )

        file_claims = [
            ("codex", "src/geond/dashboard_server.py", "Polish Mission Control for README capture"),
            (
                "qa-orchestrator",
                "scripts/verify_dashboard_browser.py",
                "Verify every dashboard view before GIF render",
            ),
        ]
        for agent_name, file_path, purpose in file_claims:
            cur.execute(
                """
                INSERT INTO file_reservations (
                    workspace_id, agent_id, file_path, purpose, expires_at, metadata
                )
                VALUES (%s, %s, %s, %s, now() + interval '6 hours', %s)
                RETURNING id
                """,
                (
                    workspace_id,
                    agent_ids[agent_name],
                    file_path,
                    purpose,
                    Jsonb({"source": "seed", "demo": True}),
                ),
            )
            reservation_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO reservation_events (
                    workspace_id, reservation_kind, reservation_id, agent_id,
                    action, subject, metadata
                )
                VALUES (%s, 'file', %s, %s, 'created', %s, %s)
                """,
                (
                    workspace_id,
                    reservation_id,
                    agent_ids[agent_name],
                    file_path,
                    Jsonb({"source": "seed", "purpose": purpose}),
                ),
            )

        symbol_claims = [
            (
                "codex",
                "mission_control_html",
                "geond.dashboard_server.mission_control_html",
                "src/geond/dashboard_server.py",
            ),
            (
                "pm-agent",
                "seed_sample_workspace",
                "geond.storage.maintenance.seed_sample_workspace",
                "src/geond/storage/maintenance.py",
            ),
        ]
        for agent_name, symbol, qualified_name, file_path in symbol_claims:
            cur.execute(
                """
                INSERT INTO symbol_reservations (
                    workspace_id, agent_id, symbol, qualified_name, file_path,
                    purpose, expires_at, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, now() + interval '6 hours', %s)
                RETURNING id
                """,
                (
                    workspace_id,
                    agent_ids[agent_name],
                    symbol,
                    qualified_name,
                    file_path,
                    "Keep demo coordination evidence stable",
                    Jsonb({"source": "seed", "demo": True}),
                ),
            )
            reservation_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO reservation_events (
                    workspace_id, reservation_kind, reservation_id, agent_id,
                    action, subject, metadata
                )
                VALUES (%s, 'symbol', %s, %s, 'created', %s, %s)
                """,
                (
                    workspace_id,
                    reservation_id,
                    agent_ids[agent_name],
                    qualified_name,
                    Jsonb({"source": "seed"}),
                ),
            )

        actions = [
            ("codex", "implementation", "Polishing dashboard visual hierarchy", "active"),
            ("copilot", "review", "Reviewing selectors and README asset paths", "recorded"),
            (
                "qa-orchestrator",
                "validation",
                "Running browser smoke and nonblank screenshot checks",
                "active",
            ),
            ("pm-agent", "planning", "Watching open handoffs and release readiness", "recorded"),
        ]
        for agent_name, action_type, summary, status in actions:
            cur.execute(
                """
                INSERT INTO agent_actions (
                    workspace_id, agent_id, session_id, action_type,
                    intent, status, summary, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    workspace_id,
                    agent_ids[agent_name],
                    sample_session_id,
                    action_type,
                    "Refresh README dashboard GIFs",
                    status,
                    summary,
                    Jsonb({"source": "seed", "demo": True}),
                ),
            )

        handoffs = [
            (
                "codex",
                "qa-orchestrator",
                "Dashboard UI polish is ready for browser verification.",
                ["Run scripts/verify_dashboard_browser.py against the local server."],
                "Capture Mission Control, Usage Evidence, Timeline, and Relationships.",
                [],
                ["uv run pytest tests/test_dashboard.py tests/test_dashboard_server.py"],
            ),
            (
                "qa-orchestrator",
                "pm-agent",
                "Browser smoke passed and README GIFs are ready for final review.",
                ["Open README and confirm the GIFs render in context."],
                "Update release notes if the visual story changed.",
                ["Confirm no private transcript or secret appears in captures."],
                ["uv run python scripts/render_dashboard_gifs.py"],
            ),
        ]
        for from_agent, to_agent, summary, next_steps, next_action, blocked_on, tested in handoffs:
            cur.execute(
                """
                INSERT INTO handoff_summaries (
                    workspace_id, from_agent_id, to_agent_id, to_agent_name, status,
                    summary, next_steps, blocked_on, metadata
                )
                VALUES (%s, %s, %s, %s, 'open', %s, %s, %s, %s)
                """,
                (
                    workspace_id,
                    agent_ids[from_agent],
                    agent_ids[to_agent],
                    to_agent,
                    summary,
                    Jsonb(next_steps),
                    Jsonb(blocked_on),
                    Jsonb(
                        {
                            "source": "seed",
                            "handoff_template": {
                                "next_action": next_action,
                                "tested_commands": tested,
                                "remaining_risks": blocked_on,
                            },
                        }
                    ),
                ),
            )

        cur.execute(
            """
            INSERT INTO benchmark_runs (
                workspace_id, label, mode, provider, model, repeat, result, metadata
            )
            VALUES (%s, 'readme-dashboard-demo', 'keyword', 'local', 'postgres-tsvector', 3, %s, %s)
            """,
            (
                workspace_id,
                Jsonb(
                    {
                        "mode": "keyword",
                        "repeat": 3,
                        "queries": [
                            {"query": "dashboard gif", "result_count": 5, "avg_ms": 18.4},
                            {"query": "handoff evidence", "result_count": 4, "avg_ms": 21.1},
                        ],
                    }
                ),
                Jsonb({"source": "seed", "demo": True}),
            ),
        )

        if _table_exists(cur, "llm_usage_events"):
            for index, (agent_name, source, model, input_tokens, output_tokens) in enumerate(
                [
                    ("codex", "codex", "gpt-5-codex", 12800, 4200),
                    ("copilot", "vscode-copilot", "gpt-4.1", 6200, 2100),
                    ("claude", "claude-code", "claude-sonnet", 7400, 2600),
                    ("qa-orchestrator", "qa", "local-smoke", 1800, 620),
                ]
            ):
                cur.execute(
                    """
                    INSERT INTO llm_usage_events (
                        workspace_id, session_id, agent_id, source, provider, model,
                        operation, input_tokens, output_tokens, total_tokens,
                        estimated, source_record_id, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'demo-capture', %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        workspace_id,
                        sample_session_id,
                        agent_ids[agent_name],
                        source,
                        "seed",
                        model,
                        input_tokens,
                        output_tokens,
                        input_tokens + output_tokens,
                        source == "qa",
                        f"seed-demo-usage-{index}",
                        Jsonb({"source": "seed", "demo": True}),
                    ),
                )
    conn.commit()


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
