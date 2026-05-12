# MCP Client Config

Geond runs as a stdio MCP server. Start Postgres first, run the schema, then point an MCP client at `uv run geond-mcp` from the repository root.

```bash
docker compose up -d postgres
docker compose --profile tools run --rm geond-migrate
uv run geond-mcp
```

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

- `search_dev_memory`: retrieve prior session evidence.
- `get_symbol_context`: inspect indexed code graph entities.
- `explain_change`: inspect file snapshots, related messages, changesets, and touched symbols. Pass `include_narrative=true` to attach a deterministic, evidence-citing summary under `narrative` (schema `geond.evidence.v1.narrative`).
- `get_changeset_detail`: look up a changeset by UUID or git commit (sha or prefix); returns files, touched code entities, and `geond.evidence.v1` evidence refs. Pass `include_narrative=true` for a one-paragraph briefing.
- `record_changeset`: record changed files and optional unified diff patches from an MCP client.
- `reserve_files`: warn other agents about file-level work.
- `reserve_symbols`: warn other agents about symbol-level work.
- `get_symbol_conflicts`: check active symbol reservations before editing.
- `record_handoff_summary`: leave concise next-step context for another agent.
- `list_handoff_summaries`: read open or closed handoffs.

## Useful Resources

- `geond://sessions`
- `geond://sessions/{session_external_id}`
- `geond://symbols/{symbol}`
- `geond://changesets`
- `geond://workspaces/{workspace_id}/timeline`
- `geond://workspaces/{workspace_id}/reservations`
- `geond://workspaces/{workspace_id}/handoffs`

## Privacy Notes

Use `GEOND_PRIVACY_MODE=local-only` with `GEOND_EMBEDDING_PROVIDER=none`, `local-openai-compatible`, or `ollama` when an MCP client must not trigger cloud embedding calls. Keyword search still works without embeddings.
