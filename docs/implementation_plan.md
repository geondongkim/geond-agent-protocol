# Implementation Plan

이 문서는 `geond-agent-protocol`을 공개 오픈소스 프로젝트로 발전시키기 위한 단계별 구현 계획이다.

## 1. Product Thesis

여러 코딩 에이전트가 같은 프로젝트에서 일할 때 가장 큰 병목은 모델 성능보다 “맥락의 단절”이다. Geond는 에이전트들이 공유할 수 있는 로컬-first 개발 기억 계층을 제공한다.

첫 번째로 증명할 장면:

> 한 에이전트가 남긴 채팅, 코드 변경, 파일 스냅샷, 심볼 그래프를 다른 MCP 클라이언트가 즉시 조회해 “왜 이 코드가 이렇게 바뀌었는지” 설명한다.

## 2. MVP Scope

### 포함

- Docker Compose 기반 Postgres + pgvector
- 기본 DB schema
- VS Code Copilot Chat storage parser
- Codex JSONL session parser as a second test bed
- Git diff/file snapshot ingester
- tree-sitter 기반 Python/TypeScript symbol indexer
- MCP server skeleton
- MCP tools: `search_dev_memory`, `get_symbol_context`, `explain_change`, `record_changeset`, `record_agent_action`
- Secret redaction baseline
- README와 데모 시나리오

### 제외

- 자체 채팅 UI
- 자체 코딩 에이전트
- 실시간 파일 감시
- fine-tuning
- 원격 동기화 서버
- 팀 계정/권한 관리
- 모든 언어 지원

## 3. Phase 0: Research Baseline

Status: mostly complete.

Tasks:

- [x] VS Code Copilot Chat storage structure 조사
- [x] `state.vscdb`, `chatSessions`, `chatEditingSessions`, `transcripts` 역할 정리
- [x] 세션 복원 테스트로 `chatEditingSessions` 필요성 확인
- [x] 원본 아이디어 문서 보존
- [x] 검증 문서 작성

Acceptance criteria:

- 첫 테스트베드 문서가 있어야 한다.
- public repo에 올려도 되는 수준으로 민감 정보가 최소화되어야 한다.

## 4. Phase 1: Repository Skeleton

Tasks:

- [x] README 작성
- [x] 아키텍처 문서 작성
- [x] 구현계획 문서 작성
- [x] `.gitignore` 작성
- [x] LICENSE 추가
- [x] `docker-compose.yml` 초안 작성
- [x] `schemas/` 디렉터리 생성
- [x] `src/` 또는 `packages/` 구조 결정

Recommended structure:

```text
geond-agent-protocol/
├── README.md
├── LICENSE
├── docker-compose.yml
├── docs/
│   ├── architecture.md
│   ├── implementation_plan.md
│   ├── research_validation.md
│   └── vscode_chat_storage_structure.md
├── schemas/
│   └── 001_initial.sql
├── src/
│   └── geond/
│       ├── adapters/
│       ├── core/
│       ├── mcp_server/
│       ├── retrieval/
│       └── storage/
└── tests/
```

Decision needed:

- Python-first MCP server or TypeScript-first MCP server.
- Recommendation: Python-first for fast tree-sitter, ingestion, and Postgres work. Add TypeScript SDK later.

## 5. Phase 2: Database Foundation

Tasks:

- [x] Add Postgres image with pgvector.
- [x] Create initial SQL migration.
- [x] Add tables: `workspaces`, `agents`, `sessions`, `messages`, `events`, `file_snapshots`, `changesets`, `code_entities`, `code_edges`, `embeddings`, `agent_actions`, `file_reservations`.
- [x] Add `workspace_aliases` so moved or renamed folders can resolve to the same workspace.
- [x] Add `workspace_fingerprints` for conservative git and manifest-based alias suggestions.
- [x] Add indexes for workspace, session, file path, entity name, and baseline text search.
- [x] Add seed script with sample workspace and session.

Acceptance criteria:

- `docker compose up` starts Postgres.
- Migration runs idempotently.
- A sample session can be inserted and queried.

## 6. Phase 3: VS Code Copilot Chat Ingester

Tasks:

- [x] Parse `chat.ChatSessionStore.index` from `state.vscdb`.
- [x] Parse `chatSessions/{sessionId}.jsonl`.
- [x] Parse `GitHub.copilot-chat/transcripts/{sessionId}.jsonl`.
- [x] Parse `chatEditingSessions/{sessionId}/state.json`.
- [x] Link messages to file snapshots where possible.
- [x] Add fixture tests using sanitized sample data.

Important design choice:

- Treat VS Code storage format as best-effort importer, not as public API.
- Store raw parser version in metadata so future parser changes can reprocess events.

Acceptance criteria:

- One recovered Copilot session can be imported into `sessions`, `messages`, `events`, and `file_snapshots`.
- Import is repeatable without duplicating records.
- Import does not require VS Code to be running.

## 6.5. Codex Test Bed

Status: parser, CLI, DB import, redaction, usage estimate import, and fixture path added.

Tasks:

- [x] Parse Codex JSONL rollout session files.
- [x] Read `session_index.jsonl` titles when available.
- [x] Extract `session_meta` metadata such as cwd, originator, CLI version, source, model provider, and model.
- [x] Extract user and assistant messages from `response_item` and `event_msg` records.
- [x] Import Codex sessions into the same `sessions`, `events`, and `messages` tables used by VS Code Copilot Chat.
- [x] Add sanitized Codex fixture tests.
- [x] Add DB integration tests for `import-codex`.
- [x] Add redaction before persisting raw Codex payloads.

Acceptance criteria:

- A live Codex session can be parsed without printing full message content.
- Imported Codex messages can be searched through the same keyword/vector/hybrid retrieval path.
- The parser treats Codex local storage as best-effort implementation detail, not as a public API.

## 6.75. Claude Code Test Bed

Status: parser, CLI, DB import, redaction, and fixture path added.

Tasks:

- [x] Parse Claude Code JSONL files under `~/.claude/projects`.
- [x] Extract `cwd`, `gitBranch`, `version`, `sessionId`, `uuid`, `parentUuid`, and timestamps.
- [x] Keep `thinking` and tool-only records as events rather than retrieval messages.
- [x] Capture tool calls in event and message metadata.
- [x] Import Claude Code sessions into shared `sessions`, `events`, and `messages` tables.
- [x] Derive workspace identity from JSONL `cwd` when CLI args are omitted.
- [x] Add sanitized Claude Code fixture tests and DB import tests.
- [x] Record `llm_usage_events` from provider usage blocks or text-message estimates.

Acceptance criteria:

- A Claude Code session can be parsed without printing full message content.
- Imported Claude Code messages can be searched with `--source claude-code`.
- Fake secrets in Claude Code payloads are redacted before persistence.

## 7. Phase 4: Code Graph Indexer

Tasks:

- [x] Add tree-sitter runtime.
- [x] Support Python first with a minimal stdlib `ast` indexer.
- [x] Support TypeScript/JavaScript second with a minimal regex-based indexer.
- [x] Add a tree-sitter-backed mixed Python/TypeScript/JavaScript index path with AST/regex fallback.
- [x] Extract files, modules, classes, functions, methods, imports, and same-file basic calls.
- [x] Infer TypeScript/JavaScript fallback line spans for function, class, method, and module entities.
- [x] Resolve import-qualified cross-file `calls` edges for Python and TypeScript/JavaScript.
- [x] Resolve TypeScript/JavaScript re-export barrel call edges for named, default-as, and unambiguous wildcard exports.
- [x] Store entities in `code_entities`.
- [x] Store relationships in `code_edges`.
- [x] Link changesets to touched entities, including unified diff hunk ranges and deletion-only hunks.

Acceptance criteria:

- Given a file path, Geond can return the functions/classes defined in it.
- Given a symbol, Geond can return neighboring symbols and related file changes.

## 8. Phase 5: Retrieval Engine

Tasks:

- [x] Implement keyword search over messages.
- [x] Add embedding provider abstraction.
- [x] Add local/no-op embedding mode for privacy-first development.
- [x] Add pgvector search when embeddings are configured.
- [x] Add `pg_trgm` substring indexing for multilingual and partial-match keyword search.
- [x] Implement first-pass hybrid scoring: keyword reciprocal rank + vector reciprocal rank.
- [x] Return evidence objects for `search_dev_memory` results.
- [x] Expand evidence objects to `explain_change` and symbol retrieval.
- [x] Add canonical `geond.evidence.v1` evidence references with stable `locator` and `metadata` fields.
- [x] Add caller/callee call-impact context to symbol, file-change, and changeset retrieval.
- [x] Generate deterministic narratives that cite changeset, symbol, file, message, and `code_edge` evidence.
- [x] Add optional local and HTTP API reranking over expanded keyword/vector/hybrid candidate pools.

Acceptance criteria:

- `search_dev_memory("Flask application context")` returns relevant session, messages, changed files, and symbols.
- `explain_change(file_path)` returns a short explanation with evidence links and call-impact context when available.

## 9. Phase 6: MCP Server

Tasks:

- [x] Choose MCP SDK.
- [x] Implement `search_dev_memory`.
- [x] Implement `get_symbol_context`.
- [x] Implement `explain_change`.
- [x] Implement `get_changeset_detail` with optional narrative synthesis.
- [x] Implement workspace alias registration/listing for moved folders.
- [x] Implement workspace fingerprint recording and alias suggestion CLI/MCP tools.
- [x] Discover manifest file hashes and package-name hashes as supplemental workspace fingerprints.
- [x] Implement `record_changeset` with optional patch hunk linking.
- [x] Implement `record_agent_action`.
- [x] Implement reservation audit event listing.
- [x] Expose resources for sessions, symbols, changesets, and workspace timeline.
- [x] Add examples for Claude Desktop, Continue, and VS Code MCP client config if applicable.

Acceptance criteria:

- An MCP client can call Geond and receive structured context.
- A second agent can retrieve context produced by a first agent.

## 10. Phase 7: Agent Coordination

Tasks:

- [x] Add `reserve_files`, `release_reservation`, and matching CLI commands.
- [x] Track active agent tasks through `agent_actions` and workspace timeline resources.
- [x] Add stale reservation expiry cleanup.
- [x] Add reservation renewal for active file and symbol work.
- [x] Add reservation audit events for create, renew, release, and expiry transitions.
- [x] Add handoff summaries.
- [x] Add structured handoff templates for tested commands, remaining risks, and next action.
- [x] Add workspace lineage graph resource for sessions, actions, handoffs, changesets, and benchmark runs.
- [x] Add context review for comparing requested work with reservations, handoffs, and lineage.
- [x] Add conflict warnings when two agents target the same files.
- [x] Add conflict warnings when two agents target the same symbols.
- [x] Add workspace reservation conflict policies: advisory, strict, and override-with-reason.

Acceptance criteria:

- Agent A records that it is editing `auth.py`.
- Agent B querying `auth.py` receives an active-work warning and Agent A’s intent.

## 11. Phase 8: Public Demo

Demo script:

1. Start Geond with Docker Compose.
2. Import a sanitized Copilot Chat session.
3. Index a small Python project.
4. Query through MCP from another client.
5. Show “why did this code change?” with chat + diff + symbol evidence.

Deliverables:

- Short screen recording or GIF.
- `examples/` fixture project.
- `docs/demo.md`.
- MCP client config examples.
- Retrieval benchmark command and docs.

Status update:

- [x] Static public demo GIF generated for the local protocol walkthrough.
- [x] Temporary Azure validation smoke added for Azure OpenAI, APIM Consumption, and B2s VM SLM benchmark.
- [x] Sanitized Azure evidence and GIF stored in `docs/azure_validation/20260512-combined`.
- [x] Azure validation GIF embedded in README and Azure validation docs as evidence, while the local demo GIF remains the main protocol walkthrough.
- [x] Embedding provider strategy refreshed with OpenAI, Azure OpenAI, gateway, and local SLM selection guidance.

Azure validation notes:

- Azure OpenAI S0 with `text-embedding-3-small` `GlobalStandard` capacity 7 successfully embedded 10 Geond messages and ran a hybrid benchmark.
- APIM Consumption successfully created an API and three backends for the gateway scaffold. Full policy REST application is opt-in because the first policy smoke exceeded the terminal timeout.
- A temporary `Standard_B2s` Ubuntu VM ran `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` on Korean/English retrieval probes with MRR `0.8333`.
- All `rg-geond-validate-*` resource groups were deleted and the final Azure group query returned an empty list.
- Public GitHub release `v0.1.0-alpha`.

Current status:

- [x] Scripted GIF asset generated at `docs/assets/geond_demo.gif`.
- [x] Reproducible GIF renderer added at `scripts/render_demo_gif.py`.
- [x] APIM AI gateway policy sample added under `examples/azure/apim`.
- [x] Foundry project-managed deployment skeleton added under `examples/azure/foundry`.
- [x] Azure CLI and Azure Portal deployment guide added with AWS/GCP resource analogues.
- [x] Changeset-to-code-entity links now surface touched symbols in `explain_change` and symbol context results.

## 11.5. Phase 9: Agent Activity Dashboard

Status: read-model started and documented in [agent_activity_dashboard.md](agent_activity_dashboard.md).

This phase adds an optional local web app for observing multi-agent work. It is
not an MCP feature and does not run agents. It reads Geond's database and
repository functions to show which agents are active, what they claimed, what
they changed, which handoffs are open, and which evidence or validation signals
need review.

Tasks:

- [x] Add a read-only dashboard overview and normalized activity event read model
  through CLI and MCP resources/tools.
- [x] Add a read-only localhost HTTP API over existing workspace overview,
  timeline, lineage, reservation, handoff, changeset, benchmark, and code-risk
  data.
- [x] Add `geond dashboard serve` for local development and seeded demos.
- [x] Build a compact operational UI with mission-control tabs, horizontally
  expanding agent lanes, session/message cards, reservations, handoffs, lineage
  counts, and activity timeline.
- [x] Expand the operational UI with richer handoff board, changeset review,
  and code-risk map views.
- [x] Expand the operational UI with richer collaboration graph drilldowns.
- [ ] Add richer filters and detail panes to dashboard review feeds.
- [x] Add a normalized activity projection so PM agents and orchestrators can
  consume one ordered event stream instead of many tables.
- [ ] Add optional Codex and Claude Code hook examples that record activity
  events without exposing secrets.
- [ ] Add a dashboard GIF to the public demo once the read-only path exists.

Acceptance criteria:

- A seeded workspace can show at least two agents, one reservation, one handoff,
  one changeset, and one benchmark or validation signal in the dashboard.
- The API is localhost-bound and read-only by default.
- Dashboard rows link back to Geond evidence refs or resource-shaped payloads.
- PM/orchestrator examples can consume the same API to summarize blockers and
  suggest next work.

## 12. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| VS Code storage format changes | Importer breaks | Keep parser isolated and versioned |
| Secrets stored accidentally | Trust loss | Redaction before persistence, purge command |
| Retrieval returns noisy context | Poor agent output | Evidence-based retrieval and score explanations |
| Scope expands into full IDE/agent | Project stalls | Keep Geond as memory/protocol layer |
| MCP clients differ in behavior | Integration friction | Provide simple tools first and documented examples |
| Postgres setup feels heavy | Adoption drop | One-command Docker setup and optional SQLite prototype later |

## 13. Open Decisions

1. Python is the first MCP server/runtime. TypeScript can be added later as a client SDK or secondary package.
2. Should raw chat content be stored by default, or only redacted normalized events?
3. Which embedding provider should be first-class: local model, OpenAI-compatible API, or pluggable only?
4. Should the first demo target Continue, Claude Desktop, VS Code MCP, or a custom CLI client?
5. How much VS Code Copilot Chat importer behavior can be documented without implying official support?
6. What is the minimum privacy promise for public adoption?

## 14. Recommended Immediate Next Steps

The patent drafting pass clarified the strongest next product slice:
GEOND should focus on the coupled pipeline of AST-derived symbol dependency
scope, diff hunk evidence, reservations, structured handoff packages, and
follow-up agent verification.

1. Add the read-only dashboard API and reuse existing timeline, lineage,
   reservation, handoff, changeset, and benchmark read models.
2. Add MCP contract tests that count static resources and resource templates separately.
3. Implement dependency-aware reservation scopes with caller/callee/import/export expansion.
4. Upgrade handoff summaries into consumable handoff packages that bind intent, patch, evidence refs, reservation ids, tests, risks, and next action.
5. Add code graph correctness and coordination benchmarks.
6. Validate patch-hunk symbol links on larger Python/TypeScript repositories and tune deletion-only hunk behavior.
7. Run saved benchmark comparisons across OpenAI, Azure OpenAI, APIM gateway, and local providers using the same judgments file.
8. Expand benchmark reports with token usage and provider billing dimensions when gateways expose them.
9. Move long-lived Azure validation resources to Bicep or Terraform under `infra/` and validate with `what-if` before deployment.

See [docs/improvement_backlog.md](improvement_backlog.md) for the deeper prioritized backlog.

The first version should optimize for one unmistakable demo: context crosses from one agent/session into another without manual re-explanation.

## 15. MVP Verification Snapshot

Completed locally:

- `uv sync` dependency management works.
- Docker Postgres with pgvector starts successfully.
- Initial schema migration runs successfully.
- VS Code Copilot Chat session import works with large tool-output messages after limiting text-search indexing.
- OpenAI `text-embedding-3-small` created 40 message embeddings.
- Korean query comparison showed `keyword` returning no results while `vector` and `hybrid` retrieved the relevant chat memory.
- Codex JSONL fixture parsing is covered by tests.
- Codex JSONL fixtures are explicitly tracked despite the global `*.jsonl` ignore rule.
- Current live Codex session summary parsed successfully: 169 events and 24 messages.
- Sanitized Codex fixture import works against local Postgres.
- Codex DB integration test verifies import, workspace/source filtered search, and raw payload redaction.
- Redaction baseline masks sensitive keys, env secret assignments, bearer tokens, GitHub-style tokens, OpenAI-style keys, and URL passwords before persistence.
- VS Code Copilot Chat Korean keyword and hybrid search were revalidated against the live recovered session.
- Retrieval snippets are now sliced in Python after fetching text, avoiding DB-side multibyte truncation issues from `left(content, 1200)`.
- Repeat imports delete stale message rows and their message embeddings when a local session file changes shape.
- Minimal Python code graph indexing stores modules, imports, classes, functions, methods, contains/imports edges, and same-file name-matched call edges.
- Local repository indexing verified `19` Python files, `219` code entities, and `332` code edges with no index errors.
- `search_dev_memory` now supports workspace/source filters and returns message evidence objects.
- `.pre-commit-config.yaml` is installed through uv and validates `ruff` plus `ruff-format`.
- `uv run pytest` and `uv run ruff check .` pass.
- VS Code Copilot Chat sanitized fixture tests cover `state.vscdb`, `chatSessions`, transcripts, and editing session state.
- MCP resources expose sessions, session details, symbol context, changesets, and workspace timeline.
- Agent coordination tools reserve files, surface active reservation conflicts, and release reservations.
- Symbol-level reservations surface conflicts through CLI and MCP.
- Handoff summaries can be recorded, listed, closed, shown on workspace timeline resources, structured with tested commands, risks, and next action metadata, and included in workspace lineage graphs.
- `seed-sample` inserts a searchable sample workspace/session.
- `purge-workspace --yes` deletes a workspace and cascaded local data.
- `GEOND_PRIVACY_MODE=local-only` blocks cloud embedding providers while allowing local OpenAI-compatible and Ollama-style providers.
- Azure OpenAI, gateway, GitHub Models, and local embedding provider modes are documented and wired into the provider layer.
- Claude Desktop, Continue, and VS Code MCP client config examples are available under `examples/mcp_clients`.
- `benchmark-search` measures keyword/vector/hybrid retrieval latency for fixture or imported data.
- `benchmark-search --save` persists runs and `benchmark-report` compares saved runs.
- `benchmark-search --judgments` reports `recall_at_k`, `mrr`, and `ndcg_at_k`; `benchmark-report --format markdown` renders comparison tables.
- `benchmark-search --rerank local|api` and `search --rerank local|api` rerank expanded candidate pools with deterministic lexical phrase/token scoring or a configured HTTP reranker.
- Reranked benchmark runs include per-query diagnostics and report aggregates for top-result changes, rank movement, rerank scores, and missing API scores.
- TypeScript/JavaScript indexing stores modules, imports, classes, functions, methods, and same-file call edges.
- TypeScript/JavaScript cross-file call edges resolve direct imports, default imports, and re-export barrel imports when the target symbol is indexed.
- `index-tree-sitter` indexes mixed Python/TypeScript/JavaScript paths and merges syntax-derived structure with AST/regex call-edge fallback.
- Editor-provided LSP references can be imported through CLI/MCP, normalized from VS Code/LSP `Location[]` fixture payloads, and exposed as `references` in symbol context.
- `collect-lsp-references` can call a supplied stdio language server, auto-select built-in Python/TypeScript profiles, collect live `textDocument/references` results, write the Location payload, and optionally import normalized references into Geond.
- `geond install` previews or writes VS Code MCP, VS Code LSP task, Claude Desktop, and Continue integration config with local-first defaults.
- CI runs documentation link checks, generates and uploads a release notes preview, runs a seeded keyword benchmark smoke, uploads benchmark markdown reports and package dist artifacts, creates GitHub Releases with notes, source distribution, wheel, SHA256 checksums, and Sigstore keyless signing bundles on `v*` tag pushes, and provides a manual `Publish to PyPI` trusted publishing workflow that builds a selected tag in a restricted job before publishing through PyPI OIDC.
- Changesets can be recorded from the CLI and linked to indexed code entities by touched file path.
- `explain_change` and `get_symbol_context` now return evidence objects and related changeset links for touched symbols.
- Changesets with unified diff patches now prefer `line_range` symbol links over broad file-path links.
- Claude Code fixture parsing and DB import are covered by tests, including source-filtered retrieval and redaction.
- Expired file and symbol reservations are marked released automatically on conflict paths and manually through `cleanup-reservations`.
- `examples/python_service` and `docs/demo.md` provide a runnable local demo path.
- `docs/apple_silicon.md` documents native arm64 setup and Apple Silicon Docker/Python cautions.
- GitHub Actions CI isolates external embeddings with `GEOND_EMBEDDING_PROVIDER=none` and avoids global privacy-mode overrides that change provider unit-test semantics.
- `docs/assets/geond_demo.gif` and Azure Foundry/APIM samples provide public demo and deployment starting points.

Known implementation notes:

- Raw message content may be very large, so text-search indexing uses `left(content, 50000)`.
- Result snippets are generated in Python to keep multilingual output valid even when local agent storage contains awkward Unicode boundaries.
- Embedding requests use `GEOND_EMBEDDING_MAX_CHARS` to avoid provider token limits.
- `GEOND_EMBEDDING_BASE_URL` should stay empty for default OpenAI unless a compatible gateway is used.
- The redaction baseline is conservative and pattern-based; broader privacy modes still need policy controls and purge workflows.
