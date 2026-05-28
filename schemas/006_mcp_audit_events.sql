CREATE TABLE IF NOT EXISTS mcp_audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid REFERENCES workspaces(id) ON DELETE SET NULL,
    agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    item_kind text NOT NULL DEFAULT 'tool',
    item_name text NOT NULL,
    client_name text,
    input_redacted jsonb NOT NULL,
    output_redacted jsonb,
    input_bytes integer,
    output_bytes integer,
    input_hash text,
    output_hash text,
    elapsed_ms double precision,
    status text NOT NULL,
    error_type text,
    error_message text,
    evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mcp_audit_workspace_created
    ON mcp_audit_events(workspace_id, created_at DESC)
    WHERE workspace_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_mcp_audit_item_created
    ON mcp_audit_events(item_kind, item_name, created_at DESC);

INSERT INTO schema_migrations (id) VALUES ('006_mcp_audit_events') ON CONFLICT DO NOTHING;
