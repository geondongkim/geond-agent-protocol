# Geond Roadmap And Backlog

## Purpose

This roadmap turns the evaluation and operating recommendations into implementable work. It prioritizes small vertical slices that improve real multi-agent use before broad platform expansion.

## Roadmap Principles

- Make the current operating loop executable before adding speculative features.
- Prefer CLI and storage primitives that MCP and dashboard can reuse.
- Link usage metrics to evidence, not personal competition.
- Keep local-first workflows working even if cloud/shared DB features are added.
- Preserve redaction and privacy behavior before adding more observability.
- Add enterprise IAM only after the local shared-memory product loop is clear.

## Phase 0: Documentation And Operating Contracts

Priority: P0

| ID | Task | Acceptance criteria |
| --- | --- | --- |
| DOC-001 | Add repository evaluation document | Scoring, tradeoffs, enterprise gaps, and lightweight MCP comparison are documented. |
| DOC-002 | Add agent operating loop | Start, finish, handoff, and PM interpretation are documented. |
| DOC-003 | Add AI usage observability design | Tokenmaxxing risks, usage schema, dashboard principles, and anti-gaming signals are documented. |
| DOC-004 | Add agent document consumption guide | Agents know which docs to read for each task type. |
| DOC-005 | Link new docs from README | New docs are discoverable from the main README. |

## Phase 1: Executable Operating Loop

Priority: P0

Goal: reduce reliance on agents manually remembering five or more Geond commands.

### `start-task`

Proposed behavior:

- resolve workspace ID or URI
- optionally import latest local sessions
- run context review
- list open handoffs
- list active reservations/conflicts
- record `task_start` agent action
- optionally reserve files and symbols
- output a compact next-action package

Proposed command:

```powershell
uv run geond start-task "<workspace-id-or-uri>" `
  --agent-name "codex" `
  --intent "Implement usage dashboard" `
  --file "src/geond/storage/dashboard.py" `
  --symbol "get_dashboard_overview" `
  --reserve
```

Acceptance criteria:

- covered by CLI tests
- dry-run mode exists
- JSON and markdown output exist
- does not mutate reservations unless `--reserve` is passed
- records an agent action when not dry-run

### `finish-task`

Proposed behavior:

- record `task_finish` agent action
- optionally record changeset
- record structured handoff
- include tested commands and remaining risks
- release or renew reservations
- output final evidence package

Proposed command:

```powershell
uv run geond finish-task "<workspace-id-or-uri>" `
  --agent-name "codex" `
  --summary "Added usage rollup read model." `
  --tested-command "uv run pytest tests/test_usage_dashboard.py" `
  --next-action "Expose usage metrics in dashboard UI." `
  --release-reservations
```

Acceptance criteria:

- covered by CLI tests
- records handoff with required fields
- supports `--release-reservations`, `--renew-reservations`, and default no-op
- preserves explicit risk and blocker fields

## Phase 2: LLM Usage Accounting Foundation

Priority: P0

| ID | Task | Acceptance criteria |
| --- | --- | --- |
| USAGE-001 | Add `llm_usage_events` schema | Migration includes workspace, session, agent, source, provider, model, operation, token fields, cost estimate, `estimated`, metadata, and indexes. |
| USAGE-002 | Add storage API | Insert and query usage events from Python without dashboard coupling. |
| USAGE-003 | Add tests | Tests cover exact usage, estimated usage, missing usage, and redaction metadata. |
| USAGE-004 | Add CLI report | `usage-summary <workspace>` returns total tokens, exact/estimated split, cost, source, and model rollups. |

## Phase 3: Importer Usage Extraction

Priority: P1

| ID | Task | Acceptance criteria |
| --- | --- | --- |
| IMPORT-001 | Codex usage extraction | Codex importer stores provider/model and usage-like fields when present. |
| IMPORT-002 | Claude Code usage extraction | Claude importer stores usage-like fields without exposing hidden reasoning. |
| IMPORT-003 | VS Code Copilot usage extraction | VS Code importer stores prompt/response counts and usage metadata when available. |
| IMPORT-004 | Token estimation fallback | Messages without usage metadata produce estimated token counts. |
| IMPORT-005 | Fixture coverage | Fixtures include exact usage, no usage, and malformed usage metadata. |

## Phase 4: Usage Versus Evidence Dashboard

Priority: P1

Dashboard panels:

- Usage Summary
- Usage by Source
- Usage by Model
- Usage by Evidence
- Data Quality
- Risk Signals
- Enablement Signals

Acceptance criteria:

- default view is workspace/team rollup
- personal drilldown is not the first screen
- exact versus estimated usage is visible
- usage is displayed next to changesets, tests, handoffs, reservations, and benchmark evidence
- high usage with weak evidence appears as a review signal, not a punitive ranking

## Phase 5: Anti-Tokenmaxxing Signals

Priority: P1

| ID | Signal |
| --- | --- |
| SIG-001 | high usage, low changeset evidence |
| SIG-002 | high prompts, no handoff |
| SIG-003 | expensive model, low-risk task |
| SIG-004 | repeated sessions, same unresolved intent |
| SIG-005 | many tool traces, no tested command |
| SIG-006 | stale reservation with unrelated activity |
| SIG-007 | high usage as training or enablement signal |

Acceptance criteria:

- signals are explainable
- signal thresholds are configurable
- raw tokens alone never create a negative judgment
- dashboard labels signals as "review" or "enablement", not "performance failure"

## Phase 6: MCP Audit Events

Priority: P2

```sql
CREATE TABLE mcp_audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid REFERENCES workspaces(id) ON DELETE SET NULL,
    agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    tool_name text NOT NULL,
    input_redacted jsonb NOT NULL,
    output_redacted jsonb,
    input_hash text,
    output_hash text,
    status text NOT NULL,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now()
);
```

Acceptance criteria:

- MCP tools can record redacted input and output
- output body recording can be disabled by privacy mode
- errors are recorded
- audit events can be exported
- future sink interface can support ELK, Datadog, CloudWatch, or OpenTelemetry

## Phase 7: IAM And Access Control

Priority: P2/P3

| Option | Pros | Cons |
| --- | --- | --- |
| Keep local stdio only | Simple and privacy-preserving | Weak centralized IAM story |
| Add read-only dashboard token | Simple PM access control | Does not solve MCP tool auth |
| Add HTTP/SSE MCP gateway | Enables JWT/API key integration | Larger attack surface and more ops |
| Add DB role separation | Useful for shared DB | Requires careful migration and docs |
| Add Postgres RLS | Strong tenant isolation | More complex queries and tests |

Recommended order:

1. local stdio default remains the safest baseline
2. add dashboard read-only access token if dashboard is shared
3. split DB roles for read, write, and admin
4. add HTTP/SSE gateway only when required
5. evaluate RLS for multi-tenant hosted deployments

## Phase 8: Codex Skill Packaging

Priority: P2

Goal: help Codex agents understand and use Geond without rereading the full docs every time.

Skill contents:

- short Geond operating loop
- command templates
- when to import sessions
- when to reserve files
- how to record handoffs
- how to read dashboard overview
- privacy warning for raw session content
- usage versus evidence dashboard rules

## Priority Stack

| Priority | Work |
| --- | --- |
| P0 | Documentation index, operating loop, `start-task`, `finish-task`, `llm_usage_events` schema |
| P1 | Importer usage extraction, tokenizer estimates, usage reports, Usage versus Evidence dashboard |
| P1 | Anti-tokenmaxxing review signals |
| P2 | MCP audit events, privacy modes, dashboard access control |
| P2 | Codex Skill packaging |
| P3 | HTTP/SSE MCP gateway, JWT integration, DB RLS, external observability sinks |

## Implementation Guidance For Agents

When picking up this backlog:

1. prefer one vertical slice
2. add tests with fixtures
3. keep CLI, storage, MCP, and dashboard boundaries clean
4. do not add a dashboard panel without a storage/read-model function
5. do not add token metrics without exact versus estimated labels
6. do not rank individuals by raw token count in default views
7. keep local-first behavior working without cloud credentials

