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
- MCP tools: `search_dev_memory`, `get_symbol_context`, `explain_change`, `record_agent_action`
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

Status: parser and CLI path added.

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

## 7. Phase 4: Code Graph Indexer

Tasks:

- [ ] Add tree-sitter runtime.
- [x] Support Python first with a minimal stdlib `ast` indexer.
- [ ] Support TypeScript/JavaScript second.
- [x] Extract files, modules, classes, functions, methods, imports, and same-file basic calls.
- [x] Store entities in `code_entities`.
- [x] Store relationships in `code_edges`.
- [ ] Link changesets to touched entities.

Acceptance criteria:

- Given a file path, Geond can return the functions/classes defined in it.
- Given a symbol, Geond can return neighboring symbols and related file changes.

## 8. Phase 5: Retrieval Engine

Tasks:

- [x] Implement keyword search over messages.
- [x] Add embedding provider abstraction.
- [x] Add local/no-op embedding mode for privacy-first development.
- [x] Add pgvector search when embeddings are configured.
- [x] Implement first-pass hybrid scoring: keyword reciprocal rank + vector reciprocal rank.
- [x] Return evidence objects for `search_dev_memory` results.
- [ ] Expand evidence objects to `explain_change` and symbol retrieval.

Acceptance criteria:

- `search_dev_memory("Flask application context")` returns relevant session, messages, changed files, and symbols.
- `explain_change(file_path)` returns a short explanation with evidence links.

## 9. Phase 6: MCP Server

Tasks:

- [x] Choose MCP SDK.
- [x] Implement `search_dev_memory`.
- [x] Implement `get_symbol_context`.
- [x] Implement `explain_change`.
- [x] Implement `record_agent_action`.
- [x] Expose resources for sessions, symbols, changesets, and workspace timeline.
- [ ] Add examples for Claude Desktop, Continue, and VS Code MCP client config if applicable.

Acceptance criteria:

- An MCP client can call Geond and receive structured context.
- A second agent can retrieve context produced by a first agent.

## 10. Phase 7: Agent Coordination

Tasks:

- [x] Add `reserve_files` and `release_reservation`.
- [x] Track active agent tasks through `agent_actions` and workspace timeline resources.
- [ ] Add stale reservation expiry.
- [ ] Add handoff summaries.
- [x] Add conflict warnings when two agents target the same files.
- [ ] Add conflict warnings when two agents target the same symbols.

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
- Public GitHub release `v0.1.0-alpha`.

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

1. Add VS Code Copilot fixture tests with sanitized sample storage.
2. Harden the Python code graph indexer and decide whether tree-sitter replaces or augments the stdlib `ast` path.
3. Expose MCP resources for sessions, symbols, changesets, and workspace timelines.
4. Add agent coordination tools: file reservations, active work, and handoff summaries.
5. Add a public demo with a small fixture project and MCP client config.

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
- `seed-sample` inserts a searchable sample workspace/session.
- `purge-workspace --yes` deletes a workspace and cascaded local data.
- `GEOND_PRIVACY_MODE=local-only` blocks cloud embedding providers until a local provider is configured.
- `examples/python_service` and `docs/demo.md` provide a runnable local demo path.

Known implementation notes:

- Raw message content may be very large, so text-search indexing uses `left(content, 50000)`.
- Result snippets are generated in Python to keep multilingual output valid even when local agent storage contains awkward Unicode boundaries.
- Embedding requests use `GEOND_EMBEDDING_MAX_CHARS` to avoid provider token limits.
- `GEOND_EMBEDDING_BASE_URL` should stay empty for default OpenAI unless a compatible gateway is used.
- The redaction baseline is conservative and pattern-based; broader privacy modes still need policy controls and purge workflows.
