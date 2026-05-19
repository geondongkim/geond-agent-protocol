# Manus Integration

Geond supports importing Manus task history through the Manus API v2. This
lets Manus task evidence appear in the Geond dashboard, become searchable, and
participate in the reservation and handoff lifecycle alongside Copilot, Codex,
and Claude Code.

## Prerequisites

- A Manus API key (obtain from your Manus account settings).
- `MANUS_API_KEY` set in your `.env` file or environment.

## Setup

```bash
# Add to .env
MANUS_API_KEY=your_key_here
```

The key is passed via the `x-manus-api-key` HTTP header. It is never logged,
stored in the database, or included in fixtures or test output.

## Fixture-Based Quickstart (no API key required)

```bash
# Dry-run: see what would be imported without writing anything
uv run geond import-manus-task \
  --fixture tests/fixtures/manus/task_detail_completed.json \
  --fixture-messages tests/fixtures/manus/task_messages_completed.json \
  --workspace-uri file:///path/to/workspace \
  --dry-run

# Real import from fixtures
uv run geond import-manus-task \
  --fixture tests/fixtures/manus/task_detail_completed.json \
  --fixture-messages tests/fixtures/manus/task_messages_completed.json \
  --workspace-uri file:///path/to/workspace
```

## Live API Quickstart (requires Manus API key)

```bash
# Import a task by ID
uv run geond import-manus-task abc123 \
  --workspace-uri file:///path/to/workspace

# Check what would be imported first
uv run geond import-manus-task abc123 \
  --workspace-uri file:///path/to/workspace \
  --dry-run
```

Re-running the same command is safe: existing records are updated in place
without duplication (idempotent import).

## Context Packets

Before starting a Manus task, you can give Manus relevant Geond context:

```bash
# Print a context packet as JSON
uv run geond manus-context-packet \
  --workspace-uri file:///path/to/workspace \
  --query "auth middleware refactor"

# Create a Manus task automatically with the context as the prompt
uv run geond manus-context-packet \
  --workspace-uri file:///path/to/workspace \
  --query "auth middleware refactor" \
  --create-task \
  --task-title "Review auth middleware with Geond context"
```

The context packet includes relevant prior sessions, open handoffs, active
reservations, evidence refs, and known risks. It never includes API keys or
credential-bearing connection strings.

## Task Contracts

Declare intent and reserve files or symbols before Manus starts working:

```bash
# Dry-run: see the contract without reserving
uv run geond manus-task-contract \
  --workspace-uri file:///path/to/workspace \
  --intent "Refactor JWT middleware" \
  --file src/auth/middleware.py \
  --symbol verify_token \
  --dry-run

# Reserve and print as a Manus prompt fragment
uv run geond manus-task-contract \
  --workspace-uri file:///path/to/workspace \
  --intent "Refactor JWT middleware" \
  --file src/auth/middleware.py \
  --format prompt
```

## Task Completion and Handoff

After Manus finishes, import the result and release reservations:

```bash
# Import result and record handoff
uv run geond manus-task-complete \
  --task-id abc123 \
  --workspace-uri file:///path/to/workspace \
  --handoff-summary "Refactored JWT middleware to use RS256" \
  --next-step "Run integration tests" \
  --tested-command "uv run pytest tests/test_auth.py" \
  --reservation-mode release

# Dry-run: see what handoff would be recorded
uv run geond manus-task-complete \
  --task-id abc123 \
  --workspace-uri file:///path/to/workspace \
  --dry-run
```

## Dashboard

After import, `uv run geond dashboard-overview <workspace>` shows a Manus
agent lane alongside Copilot, Codex, and Claude Code. Each task appears as a
session card with its title, status, and last activity timestamp.

## Limitations

- **API stability**: The integration targets Manus API v2. If endpoint shapes
  change, check the [official docs](https://open.manus.ai/docs/v2/introduction)
  and update the adapter in `src/geond/adapters/manus.py`.
- **Connector credentials**: Connector UUIDs are stored as redacted metadata.
  Actual connector credentials are never fetched or stored.
- **File downloads**: File metadata is imported by default. File content
  download is not implemented; use Manus directly to access file content.
- **Private share URLs**: `share_url` with `share_visibility=private` is
  stored in evidence metadata but not displayed in dashboard output by default.
- **Webhooks**: Real-time webhook ingestion is not yet implemented. Import is
  pull-based (CLI command or fixture).
- **Task creation**: `create_task` uses the `v2/task.create` endpoint. If
  Manus restricts task creation via API key, the error is surfaced clearly
  with the endpoint and HTTP status code.
- **Rate limits**: The client retries rate-limited requests up to three times
  with exponential backoff. `permission_denied`, `not_found`, and
  `invalid_argument` errors abort immediately with a descriptive message.
