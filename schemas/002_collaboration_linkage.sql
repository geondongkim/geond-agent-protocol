CREATE INDEX IF NOT EXISTS idx_changesets_session
    ON changesets(session_id)
    WHERE session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agent_actions_session
    ON agent_actions(session_id)
    WHERE session_id IS NOT NULL;

INSERT INTO schema_migrations (id) VALUES ('002_collaboration_linkage') ON CONFLICT DO NOTHING;