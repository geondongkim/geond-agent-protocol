# Manus Integration Implementation Plan

Created: 2026-05-19 KST
Status: implementation brief for another coding agent

This document is the handoff prompt and backlog for adding Manus as the next
agent source in Geond Agent Protocol. It is intentionally written for an
implementation agent that has not participated in the design discussion.

## Executive Decision

Use Manus as the next integration, but start with an observer-style adapter.

Recommended path:

1. Import or receive Manus task evidence into Geond.
2. Show Manus in the existing dashboard and timeline.
3. Only after that, let Manus read Geond MCP context before or during tasks.
4. Treat bidirectional reservations and handoffs as a later phase.

Do not begin with a full two-way agent orchestration project. The first valuable
slice is smaller: one Manus task should become searchable, reviewable evidence in
Geond, with a clear agent lane and artifact references.

## Why Manus Fits Geond

Manus is useful for Geond because it is not just another chat transcript source.
It is closer to an autonomous task runner: it can create tasks, run multi-step
workflows, use connectors and files, and expose task lifecycle data through an
API. That makes it a good test for Geond's broader claim: Geond is a shared
context, evidence, reservation, handoff, and activity layer for multiple AI
agents, not only a memory importer for local coding chats.

The important product story:

- Copilot, Codex, Claude Code, and Manus can all leave evidence in one workspace.
- A human can see what Manus attempted, what it produced, and which files or
  artifacts matter.
- A later agent can search Manus results before repeating work.
- Manus can eventually read Geond context before starting a task.

## Non-Goals For The First Implementation

Do not implement these in phase 1:

- A full Manus-hosted Geond MCP client.
- Real-time bidirectional reservation enforcement.
- A new dashboard application.
- A separate Manus database schema if existing Geond tables can represent the
  evidence.
- A generic `stdout`/`stderr` CLI runner abstraction as the primary model.
- Storage of Manus API keys, connector credentials, raw browser traces, or
  private file contents without redaction.

## Critical API Assumptions

The imported Manus design docs assume a CLI-like result shape with `stdout`,
`stderr`, `exit_code`, and local artifact paths. That is too narrow.

Implement against the current Manus API shape instead:

- API base URL: `https://api.manus.ai`
- Direct auth header: `x-manus-api-key`
- OAuth is also supported by Manus for some flows.
- Tasks are asynchronous.
- Task creation returns a `task_id`, `task_title`, `task_url`, `share_url`, and
  `share_visibility`.
- The complete task history is retrieved through task detail and task messages,
  not only through a terminal output string.
- Connectors and files may be attached to tasks.
- Webhooks can later be used for lifecycle events.

Official references to check before coding:

- https://open.manus.ai/docs/v2/introduction
- https://open.manus.ai/docs/v2/task.create
- https://open.manus.ai/docs/v2/task.detail
- https://open.manus.ai/docs/v2/task.listMessages
- https://open.manus.ai/docs/v2/files
- https://open.manus.ai/docs/v2/webhooks
- https://open.manus.ai/docs/v2/connectors

If any endpoint shape has changed, follow the live official docs, not this
planning document.

## Current Geond Context To Read First

Read these files before implementation:

- `README.md`
- `docs/architecture.md`
- `docs/agent_activity_dashboard.md`
- `docs/agent_collaboration.md`
- `docs/agent_operating_loop.md`
- `docs/mcp_client_config.md`
- `src/geond/cli.py`
- `src/geond/adapters/codex.py`
- `src/geond/adapters/claude_code.py`
- `src/geond/adapters/vscode_copilot.py`
- `src/geond/storage/repository.py`
- `src/geond/storage/dashboard.py`
- `src/geond/storage/context_review.py`
- `src/geond/mcp_server/server.py`
- `tests/test_dashboard.py`
- `tests/test_dashboard_server.py`
- `tests/test_mcp_evidence_contract.py`

Pay attention to existing patterns:

- Importers normalize external data into existing storage concepts.
- Redaction should happen before persistence.
- Dashboard changes should extend the existing read model.
- MCP contracts should preserve evidence metadata.
- Local-first PostgreSQL remains the default; shared cloud PostgreSQL is an
  environment profile, not a separate product mode.

## Proposed Architecture

Phase 1 is a pull/import or webhook-receive adapter:

```text
Manus API task
    -> Geond Manus adapter
    -> redaction and normalization
    -> sessions/messages/events/agent actions/file snapshots
    -> dashboard read model
    -> search and MCP evidence
```

Phase 2 lets Manus read Geond:

```text
Geond MCP server
    -> Manus custom MCP/server connector or API task context packet
    -> Manus executes task with Geond context
    -> Geond imports result back
```

Phase 3 closes the loop:

```text
Geond context review
    -> reservation or task contract
    -> Manus task execution
    -> result import
    -> handoff and reservation release/update
```

## Data Model Guidance

Prefer using existing tables and projections. Only add schema if there is no
reasonable existing fit.

Suggested normalized concepts:

| Manus concept | Geond concept |
| --- | --- |
| task | session or agent action group |
| task message | message or event |
| task lifecycle state | agent action metadata and timeline event |
| task URL/share URL | evidence metadata |
| task result | summary message and/or event |
| file output | file snapshot or artifact reference |
| connector IDs | redacted metadata |
| project ID | workspace metadata or source metadata |
| webhook event | source event with `source="manus"` |

Suggested source naming:

- `source`: `manus`
- `source_adapter`: `manus_api_v2`
- `agent_name`: `Manus`
- `session_external_id`: Manus `task_id`
- `source_record_id`: stable Manus message/event id when available; otherwise a
  deterministic hash of `task_id`, message index, timestamp, and content type.

Idempotency is mandatory. Re-importing the same task must not duplicate messages
or file snapshots.

## Phase 1 Backlog: Manus Evidence Importer

### M1. Adapter Data Contract

Deliverables:

- Add `src/geond/adapters/manus.py`.
- Define small typed records for normalized Manus task, message, file, and
  lifecycle data.
- Keep the adapter independent from CLI parsing.
- Include a pure transformation layer that accepts API JSON fixtures and returns
  Geond-ready records.

Acceptance criteria:

- Unit tests can run without a Manus API key.
- Fixture JSON can be transformed deterministically.
- Missing optional fields do not crash import.
- Unknown future fields are retained only in redacted metadata.

### M2. Manus API Client

Deliverables:

- Add a minimal client wrapper for:
  - task detail
  - task messages
  - task list, optional
  - file metadata or download link handling, optional
- Use environment variable `MANUS_API_KEY` for local development only.
- Do not log the API key.
- Support dry-run or fixture mode.

Acceptance criteria:

- HTTP errors are surfaced with request id and endpoint, without secrets.
- Rate limits use bounded retry/backoff.
- Tests mock HTTP responses.
- No test requires network access.

### M3. CLI Import Command

Possible command:

```bash
uv run geond import-manus-task <task-id> \
  --workspace-uri file:///path/to/workspace \
  --include-messages \
  --include-files metadata-only \
  --dry-run
```

Alternative command:

```bash
uv run geond import-manus \
  --task-id <task-id> \
  --workspace-uri file:///path/to/workspace
```

Deliverables:

- CLI command with `--dry-run`.
- JSON output option for automation.
- Fixture input option:

```bash
uv run geond import-manus-task --fixture tests/fixtures/manus/task_detail.json ...
```

Acceptance criteria:

- Dry-run prints the planned records and writes nothing.
- Real run imports the task, messages, and artifact references.
- Re-running the same command updates or skips existing records without
  duplication.

### M4. Storage Mapping

Deliverables:

- Store a Manus task in the existing workspace/session/message/event model.
- Record at least one agent action for task start or task observed.
- Record completion/failure state in metadata and dashboard-visible timeline.
- Preserve `task_url` and `share_url` as evidence metadata when present.

Acceptance criteria:

- `dashboard-overview` shows Manus activity.
- Search can find text from a Manus task message or final result.
- `get_evidence` or equivalent evidence path can point back to a Manus record.
- Redaction runs before data is persisted.

### M5. Dashboard Read Model

Deliverables:

- Show Manus under agent lanes as `Manus`.
- Surface task title, status, last activity, task URL, and result excerpt.
- Avoid adding a Manus-only dashboard page unless the read model cannot express
  the data.

Acceptance criteria:

- `uv run geond dashboard-overview <workspace> --limit 20` includes Manus.
- Browser dashboard displays Manus without layout overflow.
- Existing dashboard tests still pass.

### M6. Tests And Fixtures

Add fixtures under a clear path such as:

```text
tests/fixtures/manus/
  task_detail_completed.json
  task_messages_completed.json
  task_detail_failed.json
  task_messages_failed.json
```

Required tests:

- transform completed task
- transform failed task
- transform task needing input, if official API exposes that status
- idempotent re-import
- redaction of secrets in messages and metadata
- dashboard read model includes Manus lane
- CLI dry-run writes nothing
- fixture import works without network

Suggested commands:

```bash
uv run ruff check src tests
uv run python -m pytest tests/test_manus_adapter.py
uv run python -m pytest tests/test_dashboard.py tests/test_mcp_evidence_contract.py
uv run geond import-manus-task --fixture tests/fixtures/manus/task_detail_completed.json --dry-run
```

## Phase 2 Backlog: Manus Reads Geond Context

This phase starts only after phase 1 is merged and visible in the dashboard.

Two possible approaches:

1. Manus custom MCP connector or custom server, if Manus supports the needed
   custom MCP shape for this workspace.
2. Geond creates a context packet and submits it inside a Manus task prompt.

The second approach is less elegant but easier to verify.

Deliverables:

- `geond manus-context-packet <workspace> --query ...`
- Context packet includes:
  - relevant prior sessions
  - open handoffs
  - active reservations
  - code graph evidence
  - known risks
  - allowed files or forbidden files
- Optional `create-manus-task` command that creates a Manus task with that packet.

Acceptance criteria:

- Manus receives enough context to avoid repeating a prior task.
- Context packet does not include secrets.
- Packet includes Geond evidence refs, not just prose.
- Output can be imported back by phase 1.

## Phase 3 Backlog: Task Contract, Reservations, Handoffs

This is the stronger Geond collaboration story.

Deliverables:

- A pre-task contract format:
  - intent
  - workspace
  - files or symbols likely touched
  - active conflicts
  - reservation id if claimed
  - expected outputs
  - validation commands
- Manus task prompt includes the contract.
- On completion, Geond records:
  - result summary
  - artifact refs
  - tested commands
  - next actions
  - structured handoff
  - reservation release or risk note

Acceptance criteria:

- A second agent can run `review-context` and see the Manus task evidence.
- Open handoffs reflect Manus output.
- Reservations are not silently left open.
- Failed Manus tasks leave useful evidence and do not poison the dashboard.

## Security Checklist

- [ ] No API key in logs, events, dashboard, screenshots, fixtures, or test output.
- [ ] Redaction is applied before persistence.
- [ ] Connector UUIDs are treated as sensitive-adjacent metadata.
- [ ] File downloads are metadata-only by default.
- [ ] Downloaded files are size-limited.
- [ ] Binary files are not embedded into messages.
- [ ] Task URLs and share URLs are marked with visibility metadata.
- [ ] Private share URLs are not printed by default unless `--show-private-url`
      or equivalent explicit flag is used.
- [ ] Webhook endpoint validates signatures if Manus provides signing.
- [ ] Network tests are skipped unless an explicit environment flag is set.

## UX Checklist

- [ ] Manus appears as an agent, not as a separate product silo.
- [ ] Manus task cards show status, title, latest activity, and evidence link.
- [ ] Long task messages are excerpted.
- [ ] Raw JSON is hidden by default but available through evidence detail.
- [ ] Failed tasks are visible and useful.
- [ ] "Needs input" or "blocked" status is visually distinct if available.
- [ ] The dashboard remains readable with multiple Manus tasks.
- [ ] Existing Copilot, Codex, and Claude Code lanes do not regress.

## Documentation Checklist

- [ ] Add a short README mention only after the feature works.
- [ ] Add `docs/manus_integration.md` or update this file after implementation.
- [ ] Document setup with `MANUS_API_KEY` and redaction warnings.
- [ ] Provide fixture-based quickstart that works without a real Manus key.
- [ ] Provide live API quickstart behind an explicit "requires Manus API key" note.
- [ ] Document limitations: API drift, connector permissions, private share URLs,
      file download limits, and webhook verification status.

## Definition Of Done For Phase 1

Phase 1 is done when all are true:

- A completed Manus task fixture can be imported.
- A failed Manus task fixture can be imported.
- A live Manus task can be imported when `MANUS_API_KEY` is configured.
- Re-importing the same task is idempotent.
- Dashboard overview shows Manus activity.
- Search can find imported Manus task content.
- Redaction tests pass.
- CLI dry-run writes nothing.
- No existing importer, dashboard, MCP evidence, or coordination tests regress.

## Risks And Mitigations

| Risk | Mitigation |
| --- | --- |
| Manus API changes | Isolate API client; use fixtures; link official docs in code comments only where helpful. |
| Task messages are too verbose | Store excerpts for dashboard, raw redacted body in evidence metadata or message content as existing patterns allow. |
| Secrets in task output | Run existing redaction before storage; add tests with fake keys. |
| Duplicate imports | Stable source ids and upsert/update semantics. |
| Manus files are large or private | Metadata-only default; explicit download flag; size limits. |
| Dashboard gets noisy | Aggregate by task/session and show compact lane cards. |
| Bidirectional integration is blocked | Phase 1 still valuable as an observer/importer. |

## Agent Prompt For Implementation

Use this prompt for the coding agent that will implement phase 1.

```text
You are implementing Phase 1 of the Manus integration for Geond Agent Protocol.

Repository:
C:\Users\EL035\dataschool\geond-agent-protocol

Goal:
Add a Manus API v2 task importer/adapter so Manus task evidence can be stored in
Geond, searched, and shown in the existing dashboard. Do not implement full
bidirectional Manus MCP control yet.

Read first:
- docs/manus_integration_implementation_plan.md
- README.md
- docs/architecture.md
- docs/agent_activity_dashboard.md
- docs/agent_collaboration.md
- src/geond/cli.py
- src/geond/adapters/codex.py
- src/geond/adapters/claude_code.py
- src/geond/adapters/vscode_copilot.py
- src/geond/storage/dashboard.py
- src/geond/mcp_server/server.py
- tests/test_dashboard.py
- tests/test_mcp_evidence_contract.py

Important constraints:
- Treat Manus as an async API task source, not as a local CLI that always has
  stdout/stderr/exit_code.
- Use official Manus API v2 task detail/list messages/file/webhook docs as the
  source of truth if endpoint shapes differ from the plan.
- Add fixture-based tests so CI does not need a Manus API key.
- Redact before persisting.
- Re-import must be idempotent.
- Prefer existing Geond storage models and dashboard read model over adding a
  Manus-specific silo.
- Keep phase 1 scoped to import/observe/show/search.

Expected implementation:
1. Add a Manus adapter module under src/geond/adapters/.
2. Add fixtures under tests/fixtures/manus/.
3. Add a CLI command such as `import-manus-task` with --dry-run and fixture mode.
4. Map Manus task/message/result/artifact metadata into existing Geond records.
5. Extend dashboard read model only as needed so Manus appears as an agent lane
   or activity source.
6. Add tests for completed task, failed task, idempotency, redaction, dry-run,
   and dashboard visibility.

Do not:
- Store API keys.
- Print private share URLs by default.
- Download large files by default.
- Create a new dashboard app.
- Implement bidirectional reservation enforcement in this phase.

Verification:
Run at least:
- uv run ruff check src tests
- uv run python -m pytest tests/test_manus_adapter.py
- uv run python -m pytest tests/test_dashboard.py tests/test_mcp_evidence_contract.py
- uv run geond import-manus-task --fixture tests/fixtures/manus/task_detail_completed.json --dry-run

Final response:
Summarize changed files, how to test with fixtures, how to test with a real
MANUS_API_KEY, and any known limitations.
```

## Review Prompt For A Second Agent

Use this after implementation.

```text
Review the Manus integration implementation in Geond Agent Protocol.

Focus on bugs and product risks, not style preferences.

Check:
- Does the importer use Manus API v2 concepts rather than assuming stdout/stderr
  only?
- Is re-import idempotent?
- Are secrets redacted before persistence and before logs?
- Does dry-run write nothing?
- Are fixture tests independent from network and API keys?
- Does dashboard overview show Manus without breaking existing agent lanes?
- Does search/evidence retrieval include Manus content in a useful way?
- Are private URLs and file downloads handled safely?
- Are errors actionable when the API returns rate_limited, permission_denied,
  invalid_argument, or not_found?

Run:
- uv run ruff check src tests
- uv run python -m pytest tests/test_manus_adapter.py
- uv run python -m pytest tests/test_dashboard.py tests/test_mcp_evidence_contract.py

Return findings first, ordered by severity, with file and line references.
```

