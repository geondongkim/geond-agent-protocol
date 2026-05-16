# Public Demo Script

This is the first recording/GIF script for `v0.1.0-alpha`. It keeps the demo local, repeatable, and short enough for a README GIF or release note clip.

Current scripted demo asset: [docs/assets/geond_demo.gif](assets/geond_demo.gif).
Current browser-verified dashboard assets:
[docs/assets/geond_dashboard_operations.gif](assets/geond_dashboard_operations.gif),
[docs/assets/geond_dashboard_evidence.gif](assets/geond_dashboard_evidence.gif), and
[docs/assets/geond_dashboard_timeline_review.gif](assets/geond_dashboard_timeline_review.gif).

Regenerate the terminal demo with:

```bash
uv run python scripts/render_demo_gif.py
```

Regenerate the dashboard GIFs after running the local dashboard browser smoke:

```bash
uv run geond dashboard serve --host 127.0.0.1 --port 8765
uv run python scripts/verify_dashboard_browser.py \
    --url http://127.0.0.1:8765 \
    --workspace file:///path/to/workspace \
    --output-dir tmp/dashboard_browser
uv run python scripts/render_dashboard_gifs.py \
    --screenshots tmp/dashboard_browser \
    --output-dir docs/assets
```

## Target Story

One agent leaves memory, code graph context, reservations, and a handoff. A second MCP client can retrieve that context without manual re-explanation.

## Setup Shot

```bash
docker compose up -d postgres
docker compose --profile tools run --rm geond-migrate
uv run geond seed-sample
```

Show:

- Postgres is local.
- Seed inserts a sample workspace/session.
- No external agent service is required.

## Code Graph Shot

```bash
uv run geond index-tree-sitter examples/python_service \
    --root examples/python_service \
    --workspace-uri file:///sample/geond \
    --workspace-name geond-sample
```

Show `build_answer` in [examples/python_service/service.py](../examples/python_service/service.py).

Optional TypeScript shot:

```bash
uv run geond index-tree-sitter examples/typescript_service \
    --root examples/typescript_service \
    --workspace-uri file:///sample/geond \
    --workspace-name geond-sample
```

## Retrieval Shot

```bash
uv run geond search app_context --mode keyword --workspace-uri file:///sample/geond
uv run geond benchmark-search app_context build_answer \
    --mode keyword \
    --repeat 5 \
    --workspace-uri file:///sample/geond \
    --save \
    --label public-demo-keyword

uv run geond benchmark-report --workspace-uri file:///sample/geond --format markdown
```

Show:

- prior session memory returns evidence
- benchmark output includes latency and optional quality metrics

## Coordination Shot

Use the workspace id returned from `seed-sample`.

```bash
uv run geond reserve-symbols <workspace-id> \
    --agent-name copilot \
    --symbol build_answer \
    --purpose "prepare rename"

uv run geond conflicts <workspace-id> --symbol build_answer

uv run geond record-handoff <workspace-id> \
    --from-agent copilot \
    --to-agent codex \
    --summary "build_answer is indexed and reserved for a rename check." \
    --next-step "Read symbol conflicts before editing service.py"
```

Then clean expired reservations if the recording uses short TTLs:

```bash
uv run geond cleanup-reservations --workspace-id <workspace-id>
```

Show:

- symbol-level conflict appears before another agent edits
- handoff is stored as durable memory

## MCP Shot

Start the server:

```bash
uv run geond-mcp
```

In the MCP client, show resources/tools:

- `geond://sessions`
- `geond://symbols/build_answer`
- `geond://workspaces/<workspace-id>/timeline`
- `geond://workspaces/<workspace-id>/reservations`
- `geond://workspaces/<workspace-id>/handoffs`
- `search_dev_memory`
- `get_symbol_conflicts`
- `list_handoff_summaries`

Client config examples are in [examples/mcp_clients](../examples/mcp_clients).

## Browser-Verified Dashboard Shot

Open the local Command Center and show the same stored evidence visually:

- Mission Control with ownership, active work, and project hot files
- Sessions with readable prompts and replies separated from technical trace rows
- Handoffs and Changesets as review queues with next actions and touched files
- Usage Evidence and Code Risk evidence cards for spend/output and hot-file review
- Filtered Timeline details with Related Review Context
- Graph and Relationships views connecting sessions, work, handoffs, and evidence

The dashboard plan is tracked in [docs/agent_activity_dashboard.md](agent_activity_dashboard.md),
and the browser smoke writes a JSON report plus screenshots to `tmp/dashboard_browser`.

## Cleanup Shot

```bash
uv run geond purge-workspace file:///sample/geond --yes
```

Show cascaded deletion counts.

## Capture Notes

- Keep the terminal width around 100 columns.
- Hide `.env` and any shell history that may contain secrets.
- Use keyword mode for the GIF so the demo does not depend on external embedding credentials.
- For a longer video, add a second pass with hybrid retrieval after configuring an embedding provider.
