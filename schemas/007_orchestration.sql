CREATE TABLE IF NOT EXISTS orchestration_goals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title text NOT NULL,
    summary text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'accepted',
    created_by_agent text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orchestration_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    goal_id uuid REFERENCES orchestration_goals(id) ON DELETE SET NULL,
    title text NOT NULL,
    risk_level text NOT NULL DEFAULT 'medium',
    status text NOT NULL DEFAULT 'active',
    created_by_agent text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orchestration_tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id uuid NOT NULL REFERENCES orchestration_runs(id) ON DELETE CASCADE,
    title text NOT NULL,
    description text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'ready',
    priority integer NOT NULL DEFAULT 0,
    required_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_by_agent text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS worker_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id uuid NOT NULL REFERENCES orchestration_runs(id) ON DELETE CASCADE,
    agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    agent_name text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    session_external_id text,
    last_heartbeat_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS task_leases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id uuid NOT NULL REFERENCES orchestration_runs(id) ON DELETE CASCADE,
    task_id uuid NOT NULL REFERENCES orchestration_tasks(id) ON DELETE CASCADE,
    worker_session_id uuid NOT NULL REFERENCES worker_sessions(id) ON DELETE CASCADE,
    agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    status text NOT NULL DEFAULT 'active',
    expires_at timestamptz,
    released_at timestamptz,
    last_heartbeat_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS command_evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id uuid NOT NULL REFERENCES orchestration_runs(id) ON DELETE CASCADE,
    task_id uuid REFERENCES orchestration_tasks(id) ON DELETE SET NULL,
    worker_session_id uuid REFERENCES worker_sessions(id) ON DELETE SET NULL,
    command text NOT NULL,
    purpose text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'recorded',
    exit_code integer,
    stdout_summary text NOT NULL DEFAULT '',
    stderr_summary text NOT NULL DEFAULT '',
    log_path text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_findings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id uuid NOT NULL REFERENCES orchestration_runs(id) ON DELETE CASCADE,
    task_id uuid REFERENCES orchestration_tasks(id) ON DELETE SET NULL,
    severity text NOT NULL DEFAULT 'P2',
    status text NOT NULL DEFAULT 'open',
    summary text NOT NULL,
    reviewer text,
    affected_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id uuid NOT NULL REFERENCES orchestration_runs(id) ON DELETE CASCADE,
    task_id uuid REFERENCES orchestration_tasks(id) ON DELETE SET NULL,
    risk_level text NOT NULL DEFAULT 'high',
    status text NOT NULL DEFAULT 'requested',
    reason text NOT NULL,
    requested_by_agent text,
    resolved_by text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orchestration_decisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id uuid NOT NULL REFERENCES orchestration_runs(id) ON DELETE CASCADE,
    task_id uuid REFERENCES orchestration_tasks(id) ON DELETE SET NULL,
    decision text NOT NULL,
    status text NOT NULL DEFAULT 'accepted',
    reason text NOT NULL DEFAULT '',
    decided_by text,
    evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS idempotency_records (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid REFERENCES workspaces(id) ON DELETE CASCADE,
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    payload_hash text NOT NULL,
    response jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (operation, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_orchestration_goals_workspace_created
    ON orchestration_goals(workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_orchestration_runs_workspace_status
    ON orchestration_runs(workspace_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_orchestration_runs_goal
    ON orchestration_runs(goal_id);

CREATE INDEX IF NOT EXISTS idx_orchestration_tasks_run_status
    ON orchestration_tasks(run_id, status, priority DESC, created_at);

CREATE INDEX IF NOT EXISTS idx_worker_sessions_run_status
    ON worker_sessions(run_id, status, last_heartbeat_at DESC);

CREATE INDEX IF NOT EXISTS idx_task_leases_run_status
    ON task_leases(run_id, status, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_task_leases_one_active_per_task
    ON task_leases(task_id)
    WHERE released_at IS NULL AND status IN ('active', 'renewed');

CREATE INDEX IF NOT EXISTS idx_command_evidence_run_created
    ON command_evidence(run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_review_findings_run_status
    ON review_findings(run_id, status, severity);

CREATE INDEX IF NOT EXISTS idx_approval_requests_run_status
    ON approval_requests(run_id, status, risk_level);

CREATE INDEX IF NOT EXISTS idx_orchestration_decisions_run_created
    ON orchestration_decisions(run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_idempotency_records_workspace
    ON idempotency_records(workspace_id, created_at DESC)
    WHERE workspace_id IS NOT NULL;

INSERT INTO schema_migrations (id) VALUES ('007_orchestration') ON CONFLICT DO NOTHING;
