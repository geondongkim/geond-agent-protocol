CREATE TABLE IF NOT EXISTS orchestration_task_edges (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id uuid NOT NULL REFERENCES orchestration_runs(id) ON DELETE CASCADE,
    from_task_id uuid NOT NULL REFERENCES orchestration_tasks(id) ON DELETE CASCADE,
    to_task_id uuid NOT NULL REFERENCES orchestration_tasks(id) ON DELETE CASCADE,
    edge_type text NOT NULL DEFAULT 'blocks',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (from_task_id, to_task_id, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_orchestration_task_edges_run
    ON orchestration_task_edges(run_id, edge_type, created_at);

CREATE INDEX IF NOT EXISTS idx_orchestration_task_edges_to
    ON orchestration_task_edges(to_task_id, edge_type);

CREATE INDEX IF NOT EXISTS idx_orchestration_task_edges_from
    ON orchestration_task_edges(from_task_id, edge_type);

INSERT INTO schema_migrations (id) VALUES ('008_orchestration_task_graph') ON CONFLICT DO NOTHING;
