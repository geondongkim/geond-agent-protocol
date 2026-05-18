# Contributing to Geond

Thanks for helping improve Geond. The project is alpha, so the best
contributions are focused, well-tested, and careful with private agent data.

## Development Setup

Prerequisites:

- Python 3.11+
- `uv`
- Docker with Compose
- Git
- ripgrep

```bash
cp .env.example .env
uv sync
docker compose up -d postgres
docker compose --profile tools run --rm geond-migrate
uv run geond doctor --format text
uv run pre-commit install
```

For platform-specific notes, see [docs/developer_setup.md](docs/developer_setup.md).

## Validation Commands

Run the smallest useful checks for your change:

```bash
uv run ruff check .
uv run ruff format --check .
uv run python -m pytest
uv run python scripts/check_docs_links.py
```

For MCP or dashboard work, also run:

```bash
uv run geond mcp-smoke --format text --strict
uv run geond dashboard serve
```

For VS Code Copilot, Codex, or Claude Code parser work, add focused tests under
`tests/` and include fixture coverage for redaction and malformed records.

## Project Areas

Common contribution paths:

- Importers: add adapters for new agent transcripts, chat exports, issue tools,
  design tools, QA outputs, or marketing/operations artifacts.
- Retrieval: improve keyword/vector/hybrid search, evidence refs, reranking, or
  deterministic narratives.
- Domain graph: add parsers, edges, route nodes, IaC nodes, or work-object links.
- Coordination: improve reservations, context review, conflict policies, and
  handoff packages.
- Dashboard: improve read models, filters, accessibility, browser verification,
  and screenshots/GIF generation.
- MCP/CLI: keep tool schemas stable, add contract tests, and document examples.
- Docs: make setup, safety boundaries, and examples easier for first-time users.

## Privacy And Redaction Rules

Geond stores agent transcripts and work evidence. Treat that data as sensitive.

- Redact secrets before persistence.
- Do not print raw tokens, connection strings, API keys, private transcripts, or
  customer data in tests, logs, docs, screenshots, or PR descriptions.
- External embeddings are opt-in. Tests should not require live provider keys.
- Use synthetic fixtures for parser tests.
- Keep dashboard screenshots and GIFs scrubbed.

## Files That Must Stay Out Of Git

Do not add local-only or generated private artifacts:

- `docs/patent/`
- `repo/`
- `tmp/`
- `result/`
- `results/`
- `videos/`
- raw VS Code workspaceStorage exports
- raw Codex or Claude Code private sessions
- database dumps, connection files, tokens, or `.env` files

Never use `git add -f docs/patent` or `git add -f repo`.

## Adding An Importer

A good importer should:

1. Parse without writing first through a `parse-*` command when possible.
2. Preserve stable external ids so reimports are idempotent.
3. Redact before database writes.
4. Store normalized sessions/messages/events and useful raw provenance.
5. Add fixtures for valid, malformed, and secret-containing records.
6. Document source layout and command examples.

## Adding MCP Tools

A good MCP tool should:

1. Be backed by core storage/retrieval functions rather than duplicating logic.
2. Return bounded output by default.
3. Include `geond.evidence.v1` refs when it makes claims about stored data.
4. Have CLI parity or a smoke-test path when practical.
5. Be covered by MCP contract tests if it changes the public surface.

## Pull Request Checklist

Before opening a PR:

- The change is scoped to one feature, bug fix, or documentation topic.
- Tests or docs were updated for user-visible behavior.
- `uv run ruff check .` passes.
- `uv run python -m pytest` passes, or the PR explains why a narrower suite was run.
- `uv run python scripts/check_docs_links.py` passes when docs changed.
- No private transcripts, secrets, patent drafts, or local-only generated files are staged.
- The README or docs do not overclaim roadmap features as implemented.

## Good First Issues

Good first contributions include:

- More synthetic parser fixtures.
- Smaller dashboard filters with browser smoke coverage.
- Documentation examples for MCP clients.
- Cross-platform setup fixes.
- Redaction pattern tests.
- Non-development artifact importer design notes.

## Security Issues

Do not disclose vulnerabilities in a public issue. Follow [SECURITY.md](SECURITY.md).
