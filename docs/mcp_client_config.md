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
