# Codex Test Bed

Codex is the second ingestion test bed for Geond, after VS Code GitHub Copilot Chat.
The goal is to validate the shared-memory protocol against another real repository-agent
session format before expanding provider work to Azure OpenAI, local embeddings,
OpenAI-compatible gateways, model benchmarks, and non-development work adapters.

## Source Layout

Observed local Codex files:

```text
~/.codex/
├── session_index.jsonl
└── sessions/
    └── YYYY/MM/DD/rollout-<timestamp>-<session-id>.jsonl
```

`session_index.jsonl` maps session ids to thread names. Session JSONL files contain
records such as:

- `session_meta` for session id, cwd, originator, CLI version, source, model provider,
  and model.
- `response_item` records for model-visible messages and tool calls.
- `event_msg` records for UI-facing user and agent messages.

The parser treats this layout as best-effort local storage, not as a public Codex API.

## CLI Usage

Parse a single Codex session without writing to the database:

```bash
uv run geond parse-codex "C:/Users/<you>/.codex/sessions/YYYY/MM/DD/rollout-...jsonl"
```

Parse recent sessions from the Codex sessions directory:

```bash
uv run geond parse-codex "C:/Users/<you>/.codex/sessions" --limit 5
```

Import Codex sessions into Geond:

```bash
uv run geond import-codex "C:/Users/<you>/.codex/sessions" \
    --limit 5 \
    --workspace-uri "file:///C:/path/to/project" \
    --workspace-name "my-project"
```

When `llm_usage_events` is available, `import-codex` also records usage events.
It uses provider-reported usage blocks when present and otherwise stores a
session-level estimated event derived from user/assistant message text. Reimports
reuse stable `source_record_id` values so the usage row count stays idempotent.

For multi-agent dashboard work, use the same canonical `--workspace-uri` that
Copilot, Claude Code, and MCP tools use for the repository. For this checkout on
Windows that is:

```bash
uv run geond import-codex "C:/Users/<you>/.codex/sessions" \
  --limit 20 \
  --workspace-uri "file:///C:/Users/EL035/dataschool/geond-agent-protocol" \
  --workspace-name "geond-agent-protocol"
```

If Codex is imported into a fixture or temporary workspace URI, it will still be
preserved, but the dashboard will show it as a different workspace. Register a
workspace alias only when the old URI is truly the same logical repository root;
otherwise leave the split visible so provenance stays honest.

Imported Codex messages use the same `messages` and `events` tables as VS Code Copilot
Chat imports, so keyword/vector/hybrid retrieval can compare both sources.
When the workspace URI matches, the dashboard workspace selector and Agent Fleet
lanes show Codex beside Copilot instead of requiring a manual workspace id.

Search imported Codex memory with an explicit source filter:

```bash
uv run geond search "추가 테스트베드" \
    --mode keyword \
    --workspace-uri "file:///C:/path/to/project" \
    --source codex
```

## Current Verification

The repository includes a sanitized Codex fixture under `tests/fixtures/codex`.

Verified locally:

- `uv run geond parse-codex tests/fixtures/codex --limit 1`
- `uv run pytest`
- `uv run ruff check .`
- Parsed the live current Codex session summary without printing message content.
- Imported the sanitized Codex fixture into local Postgres.
- Retrieved the imported fixture with workspace/source filters and message evidence.
- Verified DB import integration against local Postgres when it is available.
- Verified that fake sensitive values in raw Codex payloads are redacted before persistence.
- Verified repeat import behavior through the shared storage path used by Codex and VS Code Copilot Chat.

Observed live-session summary on 2026-05-11:

- Session id: `019e15c6-1bc8-7911-b929-2842b2358a6f`
- Title: `Codex 테스트베드 추가`
- Events: 169
- Messages: 24
- Originator: `codex_vscode`
- Model provider: `openai`

## Next Improvements

- Keep fixture coverage current as Codex JSONL record types evolve.
- Compare Codex retrieval quality against Copilot Chat and Claude Code with the
  shared benchmark judgments.
- Use `index-tree-sitter` before symbol reservations when validating code-aware
  handoffs.
- Exercise purge and privacy modes on imported Codex sessions that include fake
  secret fixtures.

For the cross-agent comparison, see [agent_testbeds.md](agent_testbeds.md).
