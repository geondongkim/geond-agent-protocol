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
- [ ] `docker-compose.yml` 초안 작성
- [ ] `schemas/` 디렉터리 생성
- [ ] `src/` 또는 `packages/` 구조 결정

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

- [ ] Add Postgres image with pgvector.
- [ ] Create initial SQL migration.
- [ ] Add tables: `workspaces`, `agents`, `sessions`, `messages`, `events`, `file_snapshots`, `changesets`, `code_entities`, `code_edges`, `embeddings`, `agent_actions`, `file_reservations`.
- [ ] Add indexes for workspace, session, file path, entity name, and vector search.
- [ ] Add seed script with sample workspace and session.

Acceptance criteria:

- `docker compose up` starts Postgres.
- Migration runs idempotently.
- A sample session can be inserted and queried.

## 6. Phase 3: VS Code Copilot Chat Ingester

Tasks:

- [ ] Parse `chat.ChatSessionStore.index` from `state.vscdb`.
- [ ] Parse `chatSessions/{sessionId}.jsonl`.
- [ ] Parse `GitHub.copilot-chat/transcripts/{sessionId}.jsonl`.
- [ ] Parse `chatEditingSessions/{sessionId}/state.json`.
- [ ] Link messages to file snapshots and changed files where possible.
- [ ] Add fixture tests using sanitized sample data.

Important design choice:

- Treat VS Code storage format as best-effort importer, not as public API.
- Store raw parser version in metadata so future parser changes can reprocess events.

Acceptance criteria:

- One recovered Copilot session can be imported into `sessions`, `messages`, `events`, and `file_snapshots`.
- Import is repeatable without duplicating records.
- Import does not require VS Code to be running.

## 7. Phase 4: Code Graph Indexer

Tasks:

- [ ] Add tree-sitter runtime.
- [ ] Support Python first.
- [ ] Support TypeScript/JavaScript second.
- [ ] Extract files, modules, classes, functions, methods, imports, and basic calls.
- [ ] Store entities in `code_entities`.
- [ ] Store relationships in `code_edges`.
- [ ] Link changesets to touched entities.

Acceptance criteria:

- Given a file path, Geond can return the functions/classes defined in it.
- Given a symbol, Geond can return neighboring symbols and related file changes.

## 8. Phase 5: Retrieval Engine

Tasks:

- [ ] Implement keyword search over messages, summaries, paths, and symbol names.
- [ ] Add embedding provider abstraction.
- [ ] Add local/no-op embedding mode for privacy-first development.
- [ ] Add pgvector search when embeddings are configured.
- [ ] Implement hybrid scoring: semantic + symbol + recency + intent.
- [ ] Return evidence objects, not only plain text.

Acceptance criteria:

- `search_dev_memory("Flask application context")` returns relevant session, messages, changed files, and symbols.
- `explain_change(file_path)` returns a short explanation with evidence links.

## 9. Phase 6: MCP Server

Tasks:

- [ ] Choose MCP SDK.
- [ ] Implement `search_dev_memory`.
- [ ] Implement `get_symbol_context`.
- [ ] Implement `explain_change`.
- [ ] Implement `record_agent_action`.
- [ ] Expose resources for sessions, symbols, changesets, and workspace timeline.
- [ ] Add examples for Claude Desktop, Continue, and VS Code MCP client config if applicable.

Acceptance criteria:

- An MCP client can call Geond and receive structured context.
- A second agent can retrieve context produced by a first agent.

## 10. Phase 7: Agent Coordination

Tasks:

- [ ] Add `reserve_files` and `release_reservation`.
- [ ] Track active agent tasks.
- [ ] Add stale reservation expiry.
- [ ] Add handoff summaries.
- [ ] Add conflict warnings when two agents target the same files/symbols.

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

1. Python or TypeScript for the first MCP server?
2. Should raw chat content be stored by default, or only redacted normalized events?
3. Which embedding provider should be first-class: local model, OpenAI-compatible API, or pluggable only?
4. Should the first demo target Continue, Claude Desktop, VS Code MCP, or a custom CLI client?
5. How much VS Code Copilot Chat importer behavior can be documented without implying official support?
6. What is the minimum privacy promise for public adoption?

## 14. Recommended Immediate Next Steps

1. Add Apache-2.0 LICENSE.
2. Add `docker-compose.yml` with Postgres + pgvector.
3. Add initial SQL migration.
4. Implement a read-only ingester for one sanitized Copilot session.
5. Implement `search_dev_memory` as a simple keyword search before adding embeddings.
6. Add tree-sitter Python symbol extraction.
7. Wrap search in MCP.

The first version should optimize for one unmistakable demo: context crosses from one agent/session into another without manual re-explanation.
