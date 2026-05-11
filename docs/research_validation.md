# Research Validation

이 문서는 초기 아이디어를 검증하고, 공개 오픈소스 프로젝트로 만들 때 바로잡아야 할 점과 더 나은 설계 방향을 정리한다.

## 1. 검증 요약

| 주장 또는 방향 | 검증 결과 | 결론 |
|---|---|---|
| Copilot Chat 기록은 `globalStorage/github.copilot-chat/chat.json`에 있다 | 최신 VS Code/Copilot Chat에서는 워크스페이스별 `workspaceStorage/{hash}/chatSessions`와 `GitHub.copilot-chat/transcripts`가 더 중요하다 | `globalStorage/chat.json` 가정은 폐기하고 workspaceStorage 기반 ingest를 1차 테스트베드로 삼는다 |
| 채팅 파일만 복사하면 세션 복구가 된다 | 실제 테스트에서 목록 표시에는 `state.vscdb` 인덱스가 필요했고, 편집 포함 세션은 `chatEditingSessions`가 필요했다 | 저장소 ingest도 단일 파일이 아니라 세션 인덱스, 메시지, 편집 스냅샷을 함께 처리해야 한다 |
| Postgres + pgvector RAG는 에이전트 성능을 올릴 수 있다 | 맞지만 단순 텍스트 RAG만으로는 차별성이 약하다 | 코드 엔티티 그래프, 변경 의도, 시간축, 에이전트 작업 상태를 함께 저장해야 한다 |
| MCP를 지원하면 여러 에이전트가 쉽게 쓸 수 있다 | 맞다. 다만 MCP는 연결 표준이지 자동 기억 시스템은 아니다 | MCP tools/resources를 통해 명시적으로 기록하고 조회하는 API를 제공해야 한다 |
| 오픈소스를 가져와 확장하면 빠르다 | 가능하지만 기존 에이전트를 fork하는 순간 유지보수 부담이 커진다 | MVP는 독립 MCP 서버 + 어댑터 방식이 낫다. 특정 에이전트 fork는 데모 이후로 미룬다 |
| GraphRAG가 핵심 차별점이다 | 방향은 좋지만 일반 문서 GraphRAG와 코드 GraphRAG는 다르다 | tree-sitter/LSP 기반 코드 그래프와 semantic retrieval을 결합한 Code GraphRAG로 정의한다 |

## 2. 바로잡아야 할 사실

### Copilot Chat 저장 위치

초기 메모에 나온 `globalStorage/github.copilot-chat/chat.json`은 일반화하기 어렵다. 첫 테스트에서 확인된 구조는 다음과 같다.

- `workspaceStorage/{hash}/state.vscdb`: 채팅 세션 목록과 UI 상태 인덱스
- `workspaceStorage/{hash}/chatSessions/{sessionId}.jsonl`: VS Code 채팅 세션 본문
- `workspaceStorage/{hash}/chatEditingSessions/{sessionId}/`: 편집 스냅샷과 파일별 초기 내용
- `workspaceStorage/{hash}/GitHub.copilot-chat/transcripts/{sessionId}.jsonl`: Copilot 확장 전용 트랜스크립트

따라서 ingest 파이프라인은 `chatSessions`만 읽는 방식이면 부족하다. 적어도 `state.vscdb`, `chatSessions`, `chatEditingSessions`, `transcripts`를 함께 다뤄야 한다.

### MCP의 역할

MCP는 에이전트가 외부 도구와 데이터를 다루는 표준 인터페이스다. 하지만 MCP만 붙인다고 에이전트들이 자동으로 서로를 알게 되지는 않는다. Geond가 제공해야 할 것은 다음이다.

- 기록 도구: `record_agent_action`, `record_session_event`, `record_change_intent`
- 조회 도구: `search_dev_memory`, `get_symbol_context`, `explain_change`
- 조정 도구: `reserve_files`, `release_reservation`, `list_active_work`
- 리소스: `geond://sessions/{id}`, `geond://symbols/{symbol}`, `geond://workspaces/{id}/timeline`

### 오픈소스 라이선스

확인한 주요 후보는 대부분 permissive 계열이다.

| 프로젝트 | 확인한 라이선스 성격 | 활용 방향 |
|---|---|---|
| Continue | Apache-2.0 | 직접 fork보다 MCP 클라이언트 데모 대상으로 우선 사용 |
| OpenHands | 기본 MIT, enterprise 디렉터리 별도 | 자율 에이전트 연동 벤치마크 대상으로 적합 |
| MCP Python SDK | MIT | Python MCP 서버 MVP에 적합 |
| MCP TypeScript SDK | MIT에서 Apache-2.0 전환 중, 일부 혼합 | TypeScript SDK 사용 시 NOTICE/라이선스 확인 필요 |
| tree-sitter | MIT | 코드 파서 핵심 라이브러리로 적합 |
| mem0 | Apache-2.0 | 장기 메모리 설계 참고 대상으로 적합 |

프로젝트 자체는 Apache-2.0을 추천한다. MCP 생태계와 기업 채택에 유리하고, 특허 grant가 있어 프로토콜/SDK 성격의 프로젝트에 잘 맞는다.

## 3. 더 나은 방향

### 3.1 에이전트를 만들기보다 기억 계층을 만든다

가장 큰 개선점은 프로젝트의 정체성을 명확히 하는 것이다. Geond는 새로운 코딩 에이전트가 아니라, 여러 에이전트가 공유하는 개발 기억 계층이다.

이 선택의 장점:

- Copilot, Codex-like CLI, Continue, OpenHands 같은 도구를 경쟁자가 아니라 사용자로 삼을 수 있다.
- MCP 서버 하나로 여러 클라이언트와 연결 가능하다.
- 특정 UI나 모델 제공자에 종속되지 않는다.
- 처음부터 전체 에이전트를 만들 필요가 없어 MVP 범위가 작아진다.

### 3.2 RAG보다 Code Memory Retrieval로 정의한다

일반 RAG는 질문과 문서 조각의 유사도를 찾는다. 코딩 에이전트에는 이것만으로 부족하다.

Geond의 retrieval은 네 축을 결합해야 한다.

| 축 | 설명 | 예시 |
|---|---|---|
| Semantic | 자연어/코드 임베딩 유사도 | “로그인 에러 처리”와 관련된 과거 대화 검색 |
| Symbol | 함수, 클래스, 모듈 그래프 | `authenticate_user`를 호출하는 서비스와 테스트 함께 조회 |
| Temporal | 작업 시간축과 변경 순서 | “어제 이 파일을 왜 고쳤지?” |
| Intent | 변경 이유와 에이전트 목적 | “성능 개선”, “버그 수정”, “테스트 추가” |

이 네 축을 조합해야 단순 편의 기능을 넘어 코드 품질에 영향을 주는 성능 확장이 된다.

### 3.3 이벤트 소싱으로 저장한다

최종 상태만 저장하면 “왜 그렇게 됐는지”가 사라진다. Geond는 모든 입력을 event로 먼저 저장한 뒤, 검색용 projection을 따로 만든다.

- 원본 이벤트: 채팅 메시지, 도구 호출, 파일 변경, 테스트 결과, 에이전트 action
- 정규화 projection: sessions, messages, changesets, code_entities, code_edges
- 검색 projection: embeddings, summaries, symbol neighborhoods, timeline views

이 구조는 파서가 바뀌어도 원본 이벤트를 다시 재처리할 수 있게 해준다.

### 3.4 개인 정보 보호를 MVP 요구사항에 포함한다

공개 오픈소스에서 개발자 신뢰를 얻으려면 “편하다”보다 “안전하다”가 먼저다.

필수 정책:

- 기본은 local-only Docker Compose
- 외부 LLM 임베딩 사용 여부를 명시적으로 opt-in
- API key, token, `.env`, credential 패턴 redaction
- repo별 allowlist/denylist
- raw transcript 저장 여부 선택 가능
- 삭제 명령과 workspace 단위 purge 제공

## 4. 벤치마크 대상

| 대상 | 비교 포인트 | Geond의 차별화 목표 |
|---|---|---|
| Cursor | 코드베이스 인덱싱과 UX | 특정 에디터 안에 갇히지 않는 공유 기억 |
| Sourcegraph Cody | 대규모 코드 그래프 | 로컬 개인/팀 작업 히스토리와 채팅 의도까지 연결 |
| Continue | 오픈소스 에이전트 UX | Continue가 읽고 쓸 수 있는 외부 memory backend 제공 |
| OpenHands | 자율 실행 에이전트 | 실행 결과와 작업 의도를 다른 에이전트가 재사용 가능하게 저장 |
| mem0 | 장기 메모리 | 코딩 특화 AST/변경 이력/파일 예약 모델 추가 |
| Microsoft GraphRAG | 그래프 기반 검색 | 일반 문서 그래프가 아니라 코드 심볼 그래프 중심 |
| tree-sitter | AST 파싱 범용성 | 여러 언어의 symbol/entity extraction 계층으로 활용 |

## 5. MVP 결론

처음부터 “여러 에이전트가 동시에 코딩하는 집단지성”까지 구현하면 범위가 너무 크다. MVP는 다음 한 장면만 확실히 보여주면 된다.

> Copilot Chat에서 해결한 문제와 관련 코드 변경 맥락을 Geond가 ingest하고, 다른 MCP 클라이언트가 “방금 왜 이 파일이 바뀌었어?”라고 물었을 때 세션, diff, symbol context를 함께 답한다.

이를 위해 필요한 최소 구성:

1. VS Code Copilot Chat storage parser
2. Git diff / file snapshot ingester
3. Postgres + pgvector schema
4. tree-sitter 기반 Python/TypeScript symbol indexer
5. MCP tool `search_dev_memory`
6. MCP tool `explain_change`
7. Docker Compose one-command setup

이 MVP가 되면 다음 단계로 `record_agent_action`, 파일 예약, dashboard, SDK를 붙이면 된다.
