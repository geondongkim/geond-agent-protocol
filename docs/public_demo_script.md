# Public Demo Script

This runbook keeps README and release-note visuals local, repeatable, and safe
to share. Public demo assets must not contain private transcripts, raw access
tokens, local connection strings, or account identifiers.

## Current README Assets

README-specific narrative GIFs:

- [docs/assets/geond_readme_pair_coding.gif](assets/geond_readme_pair_coding.gif)
- [docs/assets/geond_readme_team_db.gif](assets/geond_readme_team_db.gif)
- [docs/assets/geond_readme_review_loop.gif](assets/geond_readme_review_loop.gif)

Regenerate them with:

```bash
uv run python scripts/render_readme_gifs.py
```

These GIFs are sanitized narrative assets. They explain validated architecture
and workflow patterns, but they do not replay private Codex, Antigravity,
Copilot, Claude, or Manus transcripts.

## Current Dashboard Assets

Browser-verified dashboard assets:

- [docs/assets/geond_dashboard_operations.gif](assets/geond_dashboard_operations.gif)
- [docs/assets/geond_dashboard_evidence.gif](assets/geond_dashboard_evidence.gif)
- [docs/assets/geond_dashboard_timeline_review.gif](assets/geond_dashboard_timeline_review.gif)
- [docs/assets/geond_dashboard_azure_collaboration.gif](assets/geond_dashboard_azure_collaboration.gif)

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

The browser smoke checks every dashboard tab, opens related timeline context,
captures screenshots, and verifies that the screenshots are nonblank before
GIF rendering.

## Legacy Terminal Demo

The older terminal demo asset is still useful for release notes:

- [docs/assets/geond_demo.gif](assets/geond_demo.gif)

Regenerate it with:

```bash
uv run python scripts/render_demo_gif.py
```

## README Scenario Scripts

### AI Pair Coding Across Agent Tools

Purpose: show that Geond is the shared evidence and coordination substrate, not
a replacement for the agents that do the work.

Public narrative:

- Agent A can read prior context through Geond MCP before starting.
- Agent B can record work through CLI, MCP, or an imported transcript.
- Both agents can share reservations, handoffs, changesets, and review context.
- A reviewer can inspect one evidence trail instead of replaying every chat.

Verified concrete example:

- Codex sessions can be imported or recorded into the same Geond workspace.
- Antigravity can read Geond through MCP.
- `codex mcp-server` can expose Codex as a callable agent surface while Geond
  remains the shared layer for search, reservations, handoffs, dashboard read
  models, lineage, and compact evidence refs.

Evidence docs:

- [antigravity_codex_geond_verification.md](antigravity_codex_geond_verification.md)
- [mcp_client_config.md](mcp_client_config.md)
- [agent_testbeds.md](agent_testbeds.md)

Repeatable checks:

```bash
uv run geond doctor --format text
uv run geond mcp-smoke --format text --strict
uv run geond testbed-antigravity \
    --workspace-uri file:///C:/path/to/repo \
    --skip-run \
    --format markdown
```

Use `--skip-run` for documentation-only verification when you do not want to
launch a live Antigravity prompt.

### Multi-PC Shared PostgreSQL Profile

Purpose: show that local MCP, CLI, and dashboard processes can collaborate
through a shared PostgreSQL-compatible database.

Public narrative:

- One machine can run local Geond against Docker PostgreSQL.
- The same repo can switch to `GEOND_DATABASE_PROFILE=azure`.
- A second machine can run its own local `geond-mcp` and dashboard against the
  same Azure PostgreSQL database.
- The dashboard shows safe source metadata without exposing credentials.

Evidence docs:

- [azure_validation/team_collab_validation.md](azure_validation/team_collab_validation.md)
- [azure_validation/README.md](azure_validation/README.md)

Profile shape:

```bash
GEOND_DATABASE_PROFILE=azure
AZURE_GEOND_DATABASE_URL=postgresql://...
```

The README `geond_readme_team_db.gif` is a sanitized narrative GIF. The
Azure-backed dashboard GIF, `geond_dashboard_azure_collaboration.gif`, is tied
to the documented validation flow and sanitized run artifacts.

### PM And Reviewer Dashboard Loop

Purpose: show that humans can review multi-agent work without reading raw MCP
JSON.

Public narrative:

- Mission Control shows active agents, latest work, and source metadata.
- Handoffs show next actions, blockers, tested commands, and remaining risks.
- Code Risk shows active claims, recent changesets, and graph fan-out.
- Timeline and Relationships connect sessions, actions, changesets, handoffs,
  benchmarks, and evidence refs.

Evidence docs:

- [agent_activity_dashboard.md](agent_activity_dashboard.md)
- [agent_operating_loop.md](agent_operating_loop.md)

Useful commands:

```bash
uv run geond dashboard serve
uv run geond dashboard-overview <workspace-id-or-uri> --limit 25
uv run geond dashboard-events <workspace-id-or-uri> --limit 50
uv run geond dashboard-code-risk <workspace-id-or-uri> --limit 50
```

## Local Terminal Storyboard

For a longer terminal-only demo, use the following flow.

### Setup Shot

```bash
docker compose up -d postgres
docker compose --profile tools run --rm geond-migrate
uv run geond seed-sample
```

Show:

- Postgres is local.
- Seed inserts a sample workspace/session.
- No external agent service is required.

### Code Graph Shot

```bash
uv run geond index-tree-sitter examples/python_service \
    --root examples/python_service \
    --workspace-uri file:///sample/geond \
    --workspace-name geond-sample
```

Show `build_answer` in
[examples/python_service/service.py](../examples/python_service/service.py).

Optional TypeScript shot:

```bash
uv run geond index-tree-sitter examples/typescript_service \
    --root examples/typescript_service \
    --workspace-uri file:///sample/geond \
    --workspace-name geond-sample
```

### Retrieval Shot

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

### Coordination Shot

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

### MCP Shot

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

### Cleanup Shot

```bash
uv run geond purge-workspace file:///sample/geond --yes
```

Show cascaded deletion counts.

## Reference README Patterns

The README visuals and tables intentionally borrow public onboarding patterns:

- OpenHuman: transparent local memory and compact context language.
- CLI-Anything: visual first screen and action-oriented demo assets.
- Microsoft AI Agents for Beginners: scenario tables and repeatable learning
  paths.

These are reference patterns only. Geond should keep its claims tied to the
current implementation and validation evidence in this repository.

## Capture Notes

- Keep terminal width around 100 columns.
- Hide `.env` and any shell history that may contain secrets.
- Use keyword mode for public GIFs so the demo does not depend on external
  embedding credentials.
- Keep Azure URLs, passwords, subscription ids, tenant ids, and user names out
  of screenshots and generated GIF text.
- For a longer video, add a second pass with hybrid retrieval after configuring
  an embedding provider.
