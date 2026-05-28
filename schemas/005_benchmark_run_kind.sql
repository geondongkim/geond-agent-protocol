ALTER TABLE benchmark_runs
    ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'search';

UPDATE benchmark_runs
SET kind = 'search'
WHERE kind IS NULL OR kind = '';

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_workspace_kind
    ON benchmark_runs(workspace_id, kind, created_at DESC);

INSERT INTO schema_migrations (id) VALUES ('005_benchmark_run_kind') ON CONFLICT DO NOTHING;
