CREATE TABLE IF NOT EXISTS llm_usage_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    session_id uuid REFERENCES sessions(id) ON DELETE SET NULL,
    agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    source text NOT NULL,
    provider text,
    model text,
    operation text,
    input_tokens integer,
    output_tokens integer,
    cached_input_tokens integer,
    reasoning_tokens integer,
    total_tokens integer,
    estimated boolean NOT NULL DEFAULT false,
    estimated_cost_usd numeric,
    priced_at timestamptz,
    source_record_id text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_workspace_created
    ON llm_usage_events(workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_llm_usage_session
    ON llm_usage_events(session_id)
    WHERE session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_llm_usage_agent
    ON llm_usage_events(agent_id, created_at DESC)
    WHERE agent_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_llm_usage_model
    ON llm_usage_events(provider, model);

CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_usage_source_record_unique
    ON llm_usage_events(source, source_record_id)
    WHERE source_record_id IS NOT NULL;

INSERT INTO schema_migrations (id) VALUES ('003_llm_usage') ON CONFLICT DO NOTHING;