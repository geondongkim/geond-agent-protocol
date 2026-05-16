# Agent Operating Loop

## Purpose

This document defines how Codex, Claude Code, VS Code Copilot, Continue, or another MCP-capable agent should use Geond during real development work.

The goal is to prevent Geond from becoming only a passive transcript archive. Agents should use it as an active operating loop:

1. read prior context
2. check current coordination state
3. advertise intent
4. reserve risky work
5. record output
6. leave a structured handoff

## When Agents Should Read This

Read this file before:

- starting a task in a repository that already uses Geond
- continuing work from another chat or session
- working alongside another agent
- editing files that another agent may also touch
- creating a PM or reviewer summary from Geond data
- debugging why the dashboard has sessions but weak coordination signals

## Recommended Start Sequence

Every coding agent should start with this sequence when Geond is available.

```text
1. Import recent sessions for the relevant source.
2. Resolve or confirm the canonical workspace URI.
3. Read dashboard overview.
4. Read open handoffs.
5. Read timeline and recent activity.
6. Run context review for the requested files, symbols, and intent.
7. Record the agent's current action.
8. Reserve files or symbols if collision risk is meaningful.
```

## Recommended Commands

Import recent Codex sessions:

```powershell
uv run geond import-codex "C:/Users/<you>/.codex/sessions" `
  --workspace-uri "file:///C:/path/to/project" `
  --workspace-name "project-name" `
  --limit 10
```

Import recent Claude Code sessions:

```powershell
uv run geond import-claude-code "C:/Users/<you>/.claude/projects" `
  --workspace-uri "file:///C:/path/to/project" `
  --workspace-name "project-name" `
  --limit 10
```

Inspect workspace state:

```powershell
uv run geond dashboard-overview "<workspace-id-or-uri>" --limit 25
uv run geond dashboard-events "<workspace-id-or-uri>" --limit 100
uv run geond list-handoffs --workspace-id-or-uri "<workspace-id-or-uri>" --status open
```

Review intended work before editing:

```powershell
uv run geond review-context "<workspace-id-or-uri>" `
  --intent "Implement AI usage metrics" `
  --file "src/geond/storage/dashboard.py" `
  --symbol "get_dashboard_overview" `
  --format markdown
```

Record active intent:

```powershell
uv run geond record-agent-action "<workspace-id>" `
  --agent-name "codex" `
  --action-kind "task_start" `
  --summary "Implement AI usage metrics read model"
```

When the current imported transcript is known, add `--session-id` for the Geond
session row id or `--session-external-id` for the adapter's original session id.
That lets the dashboard lineage graph connect the live action to the session
evidence instead of relying on timestamps alone.

Reserve risky files:

```powershell
uv run geond reserve-files "<workspace-id>" `
  --agent-name "codex" `
  --file "src/geond/storage/dashboard.py" `
  --purpose "Add AI usage rollup read model" `
  --ttl-minutes 120
```

Reserve symbols:

```powershell
uv run geond reserve-symbols "<workspace-id>" `
  --agent-name "codex" `
  --symbol "get_dashboard_overview" `
  --purpose "Extend dashboard summary with usage evidence" `
  --ttl-minutes 120
```

## Recommended Finish Sequence

Every agent should finish or pause with this sequence.

```text
1. Record meaningful changeset evidence if available.
2. Record tested commands.
3. Record remaining risks.
4. Record a handoff summary.
5. Release or renew reservations.
6. Re-import the session if the agent transcript should be searchable.
```

## Handoff Quality Bar

A handoff is useful when a new agent can continue without rereading the whole transcript.

Minimum fields:

- what changed
- why it changed
- files or symbols touched
- commands run
- commands not run and why
- remaining risks
- next action
- blockers
- source session id if known

Recommended command:

```powershell
uv run geond record-handoff "<workspace-id>" `
  --from-agent "codex" `
  --to-agent "next-agent" `
  --summary "Added a usage rollup plan but not schema migration." `
  --next-action "Create llm_usage_events migration and tests." `
  --tested-command "uv run pytest tests/test_dashboard.py" `
  --risk "Token usage may be estimated when provider metadata is missing."
```

## Proposed Convenience Commands

The current CLI already exposes primitive operations, but agents will forget steps. Add wrapper commands to reduce drift.

### `start-task`

Proposed command:

```powershell
uv run geond start-task "<workspace-id-or-uri>" `
  --agent-name "codex" `
  --intent "Add token usage dashboard" `
  --file "src/geond/storage/dashboard.py" `
  --symbol "get_dashboard_overview" `
  --reserve
```

Expected internal behavior:

- resolve workspace
- run `review-context`
- record `task_start`
- optionally reserve files and symbols
- return active handoffs, conflicts, and recommended next action

### `finish-task`

Proposed command:

```powershell
uv run geond finish-task "<workspace-id-or-uri>" `
  --agent-name "codex" `
  --summary "Added token usage read model tests." `
  --tested-command "uv run pytest tests/test_usage_dashboard.py" `
  --next-action "Expose usage signals in dashboard UI." `
  --release-reservations
```

Expected internal behavior:

- record `task_finish`
- optionally record changeset
- record handoff
- release or renew reservations
- return a concise final evidence package

## PM Interpretation

PMs should not treat Geond as a personal surveillance leaderboard. The healthy interpretation is:

- Which work is active?
- Which handoffs are blocked?
- Which files are hot or collision-prone?
- Which sessions produced useful evidence?
- Which AI usage led to changes, tests, handoffs, or decisions?
- Which high-usage patterns suggest training or process help?

Avoid using these as standalone performance metrics:

- raw token count
- raw prompt count
- raw session count
- raw agent action count

Prefer evidence-linked metrics:

- prompts per accepted changeset
- tokens per test-passing changeset
- handoff completion rate
- stale reservation count
- high usage with no output evidence
- repeated sessions with the same unresolved intent

## Agent Reading Hint

If an agent has only one minute, read:

1. this Purpose section
2. Recommended Start Sequence
3. Recommended Finish Sequence
4. Handoff Quality Bar

If an agent is implementing Geond changes, also read:

- `docs/geond_mcp_repository_evaluation.md`
- `docs/ai_usage_observability.md`
- `docs/geond_roadmap_backlog.md`

