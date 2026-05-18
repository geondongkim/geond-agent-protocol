# MCP Client Config

Geond runs as a stdio MCP server. Start Postgres first, run the schema, then point an MCP client at `uv run geond-mcp` from the repository root.

```bash
docker compose up -d postgres
docker compose --profile tools run --rm geond-migrate
uv run geond seed-sample
uv run geond mcp-smoke --format text --strict
uv run geond-mcp
```

`mcp-smoke` uses the same stdio transport shape as external clients: it starts
`geond-mcp`, performs `initialize`, lists tools/resources, reads
`geond://sessions`, and calls `search_dev_memory`.
For a structural transport smoke against a fresh database or a custom query
that may not return messages, add `--allow-empty-search` so empty retrieval is
reported as ok instead of a warning.

## One-Shot Installer

Preview default workspace integration files before writing anything:

```bash
uv run geond install --format text
```

The default preview targets VS Code MCP and the VS Code LSP collection task. It
uses local-first defaults: `GEOND_PRIVACY_MODE=local-only` and
`GEOND_EMBEDDING_PROVIDER=none`. Write those workspace files with:

```bash
uv run geond install --write
```

Point generated MCP configs at a named database profile when the same repo is
used against more than one Postgres target:

```bash
uv run geond install --client vscode-mcp --write \
  --database-profile azure \
  --database-url "$AZURE_GEOND_DATABASE_URL"
```

The installer writes `GEOND_DATABASE_PROFILE` plus the matching profile-specific
URL key. `local` uses `GEOND_DATABASE_URL`; `azure` uses
`AZURE_GEOND_DATABASE_URL`; custom profiles use `GEOND_DATABASE_URL_<PROFILE>`.

Install or preview specific clients with repeated `--client` values:

```bash
uv run geond install --client vscode-mcp --write
uv run geond install --client vscode-lsp-task --write
uv run geond install --client claude-desktop --format text
uv run geond install --client continue --format text
uv run geond install --client all --format text
```

JSON-based clients are merged conservatively by server name or task label.
Continue YAML is previewed by default; if the target config already exists,
`--write` skips it unless `--overwrite` is also provided.

## Multi-Agent Setup

Every agent should point at the same Geond database and the same canonical
workspace URI. If Copilot imports `file:///C:/Users/you/project` while Codex
imports `file:///C:/tmp/project-copy`, the dashboard correctly shows two
workspaces instead of one mixed lane. Use aliases only when those roots are the
same logical repository.

Recommended shared workspace URI for this checkout on Windows:

```text
file:///C:/Users/EL035/dataschool/geond-agent-protocol
```

### VS Code Copilot Chat

Install the workspace MCP server and LSP collection task:

```bash
uv run geond install --client vscode-mcp --client vscode-lsp-task --write
```

Then enable the `geond` MCP server from VS Code's MCP UI. A Copilot agent can
call `review_workspace_context`, `reserve_files`, `record_changeset`, and
`record_handoff_summary` directly through MCP before, during, and after edits.

### Codex CLI

Codex JSONL is imported through the CLI adapter. Import it into the same
canonical workspace URI so the dashboard shows Codex beside Copilot:

```bash
uv run geond import-codex "C:/Users/<you>/.codex/sessions" \
  --limit 20 \
  --workspace-uri "file:///C:/Users/EL035/dataschool/geond-agent-protocol" \
  --workspace-name "geond-agent-protocol"
```

After importing, Codex sessions appear in the workspace selector and in the
Agent Fleet lanes as source `codex`. If a previous Codex import used a fixture
or temporary path, leave it as a separate workspace for provenance, or register
an alias when it is truly the same repository:

```bash
uv run geond register-workspace-alias \
  "file:///C:/Users/EL035/dataschool/geond-agent-protocol" \
  "file:///C:/tmp/old-codex-root" \
  --reason same-repository-root
```

### Claude Desktop, Continue, and Other MCP Clients

Use the same `uv --directory <repo> run geond-mcp` stdio command and the same
database profile env block as VS Code. The examples in
[examples/mcp_clients](../examples/mcp_clients) include the required command,
args, and env shape. For shared team validation, keep the MCP process local to
each machine and point all clients at the shared PostgreSQL profile.

### Agent Lifecycle Pattern

Use this lightweight sequence for each agent task:

1. `review_workspace_context` with the intended files, symbols, and goal.
2. `reserve_files` or `reserve_symbols` for active ownership.
3. Make the code or documentation change.
4. `record_changeset` with touched files and optional patch evidence.
5. `record_handoff_summary` when another agent or reviewer should continue.

That sequence gives the dashboard enough evidence to show live ownership,
project structure hotspots, recent sessions, and next actions for a PM or
orchestrator.

## Claude Desktop

Use [examples/mcp_clients/claude_desktop_config.json](../examples/mcp_clients/claude_desktop_config.json) as the shape for the `mcpServers` entry. Replace `C:/path/to/geond-agent-protocol` with this repository path and add embedding credentials through environment variables or your shell.

## VS Code MCP

Use [examples/mcp_clients/vscode_mcp.json](../examples/mcp_clients/vscode_mcp.json) as a workspace-level MCP configuration example. The important pieces are:

- `type`: `stdio`
- `command`: `uv`
- `args`: `--directory`, repository path, `run`, `geond-mcp`
- `env`: database and privacy/provider settings

## Continue

Use [examples/mcp_clients/continue_config.yaml](../examples/mcp_clients/continue_config.yaml) as a Continue-style MCP server entry. Continue configuration shapes can vary by version, so keep the command/env values and adapt the surrounding YAML key names if your installed version expects a different location.

## Useful Tools

For collaboration, agents should think in two passes: read first, then write a
small amount of durable state.

Read before editing:

- `search_dev_memory`, `geond://sessions`, and
  `geond://sessions/{session_external_id}` recover prior conversation evidence.
- `get_symbol_context`, `geond://symbols/{symbol}`, `explain_change`, and
  `get_changeset_detail` recover code, changeset, narrative, and call/reference
  evidence.
- `get_dashboard_overview`, `get_agent_activity_events`, workspace `timeline`,
  `lineage`, `reservations`, and `handoffs` reveal current ownership,
  blockers, active claims, and recent activity.
- `review_workspace_context` is the preflight check before a prompt-driven edit;
  it compares the intended files/symbols with current reservations, handoffs,
  and lineage.

Write after deciding to work:

- `record_agent_action` records what the agent is doing now.
- `reserve_files` and `reserve_symbols` advertise ownership before editing;
  `renew_reservation`, `renew_symbol_reservation`, release tools, and
  `list_reservation_events` keep lease state auditable.
- `record_changeset` records file, patch, commit, and intent evidence after a
  meaningful change.
- `record_handoff_summary`, `list_handoff_summaries`, and
  `close_handoff_summary` transfer next steps, tested commands, risks, and
  blockers between agents.

The web dashboard is the human-facing view over the same state. Users watch
Agent Lanes for active ownership, Sessions for the actual user/agent exchange,
Timeline for ordered evidence, Relationships for agent-session-work links, and
Project Structure for hot files. They do not need MCP JSON to decide whether to
continue, review a handoff, reassign work, or ask an agent to release a claim.

- `search_dev_memory`: retrieve prior session evidence. Pass `rerank="local"` or
    `rerank="api"` and optional `candidate_limit` to rerank keyword/vector/hybrid
    candidates locally or through `GEOND_RERANK_URL`.
- `get_symbol_context`: inspect indexed code graph entities, related changesets, caller/callee `calls` edges, and imported LSP-backed `references` edges.
- `record_lsp_references`: import editor-provided reference edges into the code graph. Pass `replace=false` to append instead of replacing prior LSP reference imports for the workspace.
- CLI LSP import: `normalize-lsp-references` previews VS Code/LSP `Location[]` conversion, and `import-lsp-references` accepts either Geond reference JSON or Location JSON; pass `--target-qualified-name`, `--workspace-root`, and `--provider` for bare location arrays.
- CLI LSP collection: `collect-lsp-references` starts a supplied stdio language server, calls `textDocument/references`, writes a live Location payload, and can import the normalized references with `--import-workspace-id`. Use `--server-profile auto`, `pyright`, or `typescript` for common Python/JS/TS cases, and `lsp-server-profiles` to inspect built-in commands.
- Editor wrapper examples: [vscode_lsp_collect_task.json](../examples/mcp_clients/vscode_lsp_collect_task.json) provides a VS Code task shape, and [continue_lsp_reference_workflow.md](../examples/mcp_clients/continue_lsp_reference_workflow.md) shows a Continue pre-query enrichment flow.
- `register_workspace_alias`: attach a moved or renamed folder URI to an existing workspace.
- `list_workspace_aliases`: inspect registered workspace aliases.
- `record_workspace_fingerprints`: store durable workspace identity hints such as sanitized git remote, first commit, manifest hashes, and hashed package names.
- `suggest_workspace_aliases`: ask Geond which existing workspace a new folder URI probably belongs to before registering an alias; responses include recommendation fields for single, ambiguous, already-resolved, and partial matches.
- `get_workspace_coordination_policy`: read reservation conflict behavior for a workspace.
- `set_workspace_coordination_policy`: set reservation conflict behavior to `advisory`, `strict`, or `override-with-reason`.
- `record_agent_action`: record current agent intent/status so it appears in
  activity events, dashboard lanes, and handoff context.
- `explain_change`: inspect file snapshots, related messages, changesets, touched symbols, and resolved call impact. Pass `include_narrative=true` to attach a deterministic, evidence-citing summary under `narrative` (schema `geond.evidence.v1.narrative`).
- CLI handoff lifecycle: use `record-handoff`, `list-handoffs`, and `close-handoff <handoff-id>` to keep context reviews focused on truly open work.
- `get_changeset_detail`: look up a changeset by UUID or git commit (sha or prefix); returns files, touched code entities, call impact, and `geond.evidence.v1` evidence refs. Ambiguous commit prefixes return `ambiguous=true` with candidate matches instead of choosing silently. Pass `include_narrative=true` for a one-paragraph briefing.
- `record_changeset`: record changed files and optional unified diff patches from an MCP client.
- `reserve_files`: warn other agents about file-level work; pass `override_reason` when policy requires it.
- `reserve_symbols`: warn other agents about symbol-level work; pass `override_reason` when policy requires it.
- `renew_reservation`: extend an active file reservation by id or file path.
- `renew_symbol_reservation`: extend an active symbol reservation by id or symbol.
- `list_reservation_events`: inspect created, renewed, released, and expired reservation audit events.
- `get_symbol_conflicts`: check active symbol reservations before editing.
- `record_handoff_summary`: leave concise next-step context for another agent,
  including optional `tested_commands`, `remaining_risks`, and `next_action`
  template fields.
- `list_handoff_summaries`: read open or closed handoffs.
- `get_workspace_lineage_graph`: return a node/edge graph linking sessions,
  agent actions, handoffs, changesets, and benchmark runs.
- `get_agent_activity_events`: return normalized activity events across
  sessions, agent actions, reservations, handoffs, changesets, and benchmark
  runs.
- `get_dashboard_overview`: return a compact read-only overview for dashboard,
  PM-agent, and orchestrator usage.
- `review_workspace_context`: compare requested intent/files/symbols with active
    reservations, open handoffs, and lineage before starting an agent task.

## Useful Resources

- `geond://sessions`
- `geond://sessions/{session_external_id}`
- `geond://symbols/{symbol}`
- `geond://changesets`
- `geond://workspaces/{workspace_id}/timeline`
- `geond://workspaces/{workspace_id}/activity`
- `geond://workspaces/{workspace_id}/overview`
- `geond://workspaces/{workspace_id}/lineage`
- `geond://workspaces/{workspace_id}/reservations`
- `geond://workspaces/{workspace_id}/handoffs`

## Privacy Notes

Use `GEOND_PRIVACY_MODE=local-only` with `GEOND_EMBEDDING_PROVIDER=none`, `local-openai-compatible`, or `ollama` when an MCP client must not trigger cloud embedding calls. Keyword search still works without embeddings.
