# Demo

This demo shows the current MVP path: seed or import development memory, index a
small Python project, retrieve memory, and expose the result through MCP tools and
resources.

## 1. Start Postgres

```bash
docker compose up -d postgres
docker compose --profile tools run --rm geond-migrate
```

## 2. Seed Sample Memory

```bash
uv run geond seed-sample
uv run geond search "app_context" --workspace-uri "file:///sample/geond" --mode keyword
```

## 3. Index The Example Project

```bash
uv run geond index-python examples/python_service \
    --root examples/python_service \
    --workspace-uri "file:///sample/geond" \
    --workspace-name "geond-sample"
```

Then query the symbol graph:

```bash
uv run geond-mcp
```

From an MCP client, use:

- Tool: `get_symbol_context` with `symbol = "build_answer"`
- Resource: `geond://symbols/build_answer`
- Resource: `geond://sessions`
- Resource: `geond://workspaces/<workspace-id>/timeline`

## 4. Import Real Agent Memory

Parse before importing:

```bash
uv run geond parse-vscode "C:/path/to/workspaceStorage/<hash>"
uv run geond parse-codex "C:/Users/<you>/.codex/sessions" --limit 5
```

Import with explicit workspace identity:

```bash
uv run geond import-codex "C:/Users/<you>/.codex/sessions" \
    --limit 5 \
    --workspace-uri "file:///C:/path/to/project" \
    --workspace-name "my-project"
```

## 5. Coordinate Agent Work

From an MCP client:

- Tool: `reserve_files`
- Tool: `get_active_reservations`
- Tool: `release_reservation`
- Tool: `record_agent_action`

## 6. Purge A Workspace

The purge command requires explicit confirmation:

```bash
uv run geond purge-workspace "file:///sample/geond" --yes
```

This deletes the workspace and cascades sessions, messages, events, snapshots,
embeddings, code graph rows, reservations, actions, and redaction findings.
