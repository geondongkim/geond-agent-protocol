CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_migrations (
    id text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspaces (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    root_uri text NOT NULL UNIQUE,
    name text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    kind text NOT NULL DEFAULT 'unknown',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, kind)
);

CREATE TABLE IF NOT EXISTS sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source text NOT NULL,
    external_id text NOT NULL,
    title text NOT NULL DEFAULT '',
    started_at timestamptz,
    ended_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, source, external_id)
);

CREATE TABLE IF NOT EXISTS events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    session_id uuid REFERENCES sessions(id) ON DELETE SET NULL,
    source text NOT NULL,
    source_id text NOT NULL,
    event_type text NOT NULL,
    occurred_at timestamptz,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    raw_event_id uuid REFERENCES events(id) ON DELETE SET NULL,
    role text NOT NULL DEFAULT 'unknown',
    ordinal integer NOT NULL,
    content text NOT NULL DEFAULT '',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, ordinal)
);

CREATE TABLE IF NOT EXISTS file_snapshots (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    session_id uuid REFERENCES sessions(id) ON DELETE SET NULL,
    file_uri text NOT NULL,
    file_path text,
    content_hash text NOT NULL,
    content text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    captured_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, file_uri, content_hash)
);

CREATE TABLE IF NOT EXISTS changesets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    session_id uuid REFERENCES sessions(id) ON DELETE SET NULL,
    git_commit text,
    branch text,
    intent text,
    summary text NOT NULL DEFAULT '',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS change_files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    changeset_id uuid NOT NULL REFERENCES changesets(id) ON DELETE CASCADE,
    file_path text NOT NULL,
    status text NOT NULL DEFAULT 'modified',
    additions integer,
    deletions integer,
    patch text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS code_entities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    snapshot_id uuid REFERENCES file_snapshots(id) ON DELETE SET NULL,
    kind text NOT NULL,
    name text NOT NULL,
    qualified_name text,
    file_path text NOT NULL,
    start_line integer,
    end_line integer,
    signature text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS code_edges (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_entity_id uuid REFERENCES code_entities(id) ON DELETE CASCADE,
    target_entity_id uuid REFERENCES code_entities(id) ON DELETE CASCADE,
    edge_type text NOT NULL,
    confidence double precision NOT NULL DEFAULT 1.0,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS change_entities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    changeset_id uuid NOT NULL REFERENCES changesets(id) ON DELETE CASCADE,
    change_file_id uuid NOT NULL REFERENCES change_files(id) ON DELETE CASCADE,
    code_entity_id uuid NOT NULL REFERENCES code_entities(id) ON DELETE CASCADE,
    match_type text NOT NULL DEFAULT 'file_path',
    confidence double precision NOT NULL DEFAULT 0.8,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (change_file_id, code_entity_id)
);

CREATE TABLE IF NOT EXISTS embeddings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    target_table text NOT NULL,
    target_id uuid NOT NULL,
    target_kind text NOT NULL,
    model text NOT NULL,
    dimensions integer NOT NULL DEFAULT 1536,
    content_hash text NOT NULL,
    embedding vector(1536),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (target_table, target_id, model)
);

CREATE TABLE IF NOT EXISTS summaries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    target_table text NOT NULL,
    target_id uuid NOT NULL,
    summary text NOT NULL,
    model text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (target_table, target_id, model)
);

CREATE TABLE IF NOT EXISTS agent_actions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    session_id uuid REFERENCES sessions(id) ON DELETE SET NULL,
    action_type text NOT NULL,
    intent text,
    status text NOT NULL DEFAULT 'recorded',
    summary text NOT NULL DEFAULT '',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS file_reservations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    file_path text NOT NULL,
    purpose text NOT NULL DEFAULT '',
    expires_at timestamptz,
    released_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS symbol_reservations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    symbol text NOT NULL,
    qualified_name text,
    file_path text,
    purpose text NOT NULL DEFAULT '',
    expires_at timestamptz,
    released_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS handoff_summaries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    from_agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    to_agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    to_agent_name text,
    status text NOT NULL DEFAULT 'open',
    summary text NOT NULL,
    next_steps jsonb NOT NULL DEFAULT '[]'::jsonb,
    blocked_on jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    closed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benchmark_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid REFERENCES workspaces(id) ON DELETE CASCADE,
    label text NOT NULL DEFAULT '',
    mode text NOT NULL,
    provider text,
    model text,
    repeat integer NOT NULL,
    result jsonb NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS redaction_findings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid REFERENCES workspaces(id) ON DELETE CASCADE,
    source text NOT NULL,
    source_id text NOT NULL,
    finding_type text NOT NULL,
    action text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace_id);
CREATE INDEX IF NOT EXISTS idx_events_workspace_session ON events(workspace_id, session_id);
CREATE INDEX IF NOT EXISTS idx_messages_session_ordinal ON messages(session_id, ordinal);
DROP INDEX IF EXISTS idx_messages_content_trgm_seed;
CREATE INDEX IF NOT EXISTS idx_messages_content_tsv_seed ON messages USING gin (to_tsvector('simple', left(content, 50000)));
CREATE INDEX IF NOT EXISTS idx_file_snapshots_workspace_path ON file_snapshots(workspace_id, file_path);
CREATE INDEX IF NOT EXISTS idx_code_entities_workspace_name ON code_entities(workspace_id, name);
CREATE INDEX IF NOT EXISTS idx_code_entities_workspace_path ON code_entities(workspace_id, file_path);
CREATE INDEX IF NOT EXISTS idx_code_edges_workspace_type ON code_edges(workspace_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_change_entities_workspace ON change_entities(workspace_id, changeset_id);
CREATE INDEX IF NOT EXISTS idx_change_entities_code_entity ON change_entities(code_entity_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_target ON embeddings(target_table, target_id, model);
CREATE INDEX IF NOT EXISTS idx_embeddings_vector_hnsw ON embeddings USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_actions_workspace ON agent_actions(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_file_reservations_active ON file_reservations(workspace_id, file_path) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_symbol_reservations_active ON symbol_reservations(workspace_id, symbol) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_handoff_summaries_workspace ON handoff_summaries(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_benchmark_runs_workspace ON benchmark_runs(workspace_id, created_at DESC);

INSERT INTO schema_migrations (id) VALUES ('001_initial') ON CONFLICT DO NOTHING;
