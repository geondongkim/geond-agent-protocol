# Geond Agent Protocol 자기소개서 소스 채굴

이 문서는 `geond-agent-protocol` 프로젝트에서 데이터 엔지니어 자기소개서에 사용할 수 있는 실제 경험 소스를 정리한 것이다. 핵심 방향은 "AI 에이전트 도구를 만들었다"가 아니라, 여러 도구에서 발생하는 비정형 작업 로그를 수집, 정규화, 비식별화, 검색 가능한 증거 데이터로 바꾼 경험으로 설명하는 것이다.

## 프로젝트 한 줄 요약

Geond Agent Protocol은 Codex, Claude Code, VS Code Copilot, Manus 같은 여러 AI 에이전트의 작업 기록을 PostgreSQL에 저장하고, MCP/CLI/dashboard를 통해 검색, handoff, evidence reference, coordination을 제공하는 로컬 우선 공유 컨텍스트 레이어다.

## 핵심 포지셔닝

- 단순히 AI 앱을 만든 경험이 아니라, 에이전트가 남긴 작업 로그를 데이터 파이프라인처럼 다룬 경험이다.
- 외부 API 응답을 그대로 저장하지 않고, adapter 계층에서 표준 모델로 정규화했다.
- 민감정보와 파일 메타데이터를 저장 전에 redaction 처리했다.
- raw transcript 전체를 넘기는 대신 evidence reference 중심의 compact response 설계를 고민했다.
- PostgreSQL 기반 hybrid search 구조를 이해하고, 검색 품질 문제와 LLM context 비용 문제를 분리해서 판단했다.

## 소스 1. Manus API v2 작업 이력 수집/정규화

### 목적

Manus에서 생성된 작업, 메시지, 첨부파일, 상태 정보를 Geond의 PostgreSQL 기반 공유 메모리로 가져와 다른 AI 에이전트가 검색하고 이어받을 수 있게 했다.

### 내 역할

이미 구현된 Manus 연동을 실제 API 계약 기준으로 검증하고, adapter, storage, CLI, docs, tests를 함께 고친 품질 하드닝을 담당했다.

### 사용 기술

- Python
- PostgreSQL
- JSON API
- CLI
- pytest
- redaction pipeline

### 데이터 규모

운영 대용량 수치가 있는 프로젝트라기보다는, 세션, 메시지, 이벤트, 파일 스냅샷, redaction findings처럼 로그성 데이터를 구조화해 저장하는 데이터 모델을 다뤘다.

### 가장 어려웠던 문제

mock/fixture 기준으로는 테스트가 통과하지만 실제 Manus API 응답 형태와 맞지 않는 부분이 있었다. 특히 `task.list`, `task.create`, `task.listMessages`의 현재 API shape가 기존 구현 또는 문서 예제와 달랐다.

### 문제를 발견한 방법

구현 파일, 문서 예제, 테스트 fixture, 실제 API contract를 대조했다. 이 과정에서 fixture에 맞춘 구현만으로는 실사용 첫 실행에서 깨질 수 있다는 점을 확인했다.

### 내가 시도한 해결책

- `task.list` 응답에서 `tasks`뿐 아니라 `data`로 내려오는 경우도 adapter에서 흡수했다.
- `task.create` 요청을 예전 `{title, prompt}` 형태가 아니라 현재 message content body 형태로 맞췄다.
- `task.listMessages`에서 `error_message`, `tool_used`, `plan_update`, `new_plan_step`, `explanation`, `structured_output_result` 같은 이벤트성 메시지를 버리지 않고 정규화했다.
- 첨부파일은 원문을 바로 저장하지 않고 metadata-only artifact로 변환했다.
- 파일명과 첨부파일 URL/metadata는 redaction 후 `file_snapshots`에 저장하도록 했다.
- CLI에서 문서 예제와 실제 인자 형태가 어긋나지 않도록 positional task ID도 지원했다.

### 왜 그 해결책을 선택했는지

API 호출부마다 예외처리를 흩뿌리면 downstream 저장, 검색, dashboard 로직이 계속 흔들린다. 그래서 외부 API와 내부 데이터 모델의 경계인 adapter layer에서 현재 API shape를 표준 모델로 변환하는 방식이 더 안정적이라고 판단했다.

### 실패하거나 바꾼 방법

처음에는 기존 요청 형태를 신뢰할 수 있었지만, 현재 Manus API가 message content body를 쓰는 것을 확인하고 `create_task` 요청 구조를 수정했다. 또한 파일 메타데이터 저장 과정에서 파일명/URL path까지 redaction pipeline을 통과시켜야 한다고 판단해 storage 로직을 보강했다.

### 결과

- Manus task, message, attachment를 Geond의 session/message/event/file snapshot 모델로 가져올 수 있게 됐다.
- blocked 상태에 해당하는 `waiting` 등도 세션 메타데이터에 반영했다.
- private URL과 connector ID 같은 민감하거나 식별 가능한 정보는 조심스럽게 처리했다.
- 테스트는 현재 API shape, positional task ID, blocked status, attachment extraction, redaction 경로를 포함하도록 보강했다.
- 이전 검증 기준 전체 테스트는 `199 passed`까지 확인했다.

### 데이터 엔지니어 직무와 연결되는 지점

외부 API 기반 데이터 수집, schema normalization, idempotent import, 로그성 데이터 모델링, 민감정보 비식별화, 테스트 기반 파이프라인 안정화 경험으로 설명할 수 있다.

### 면접에서 자신 있게 설명 가능한 부분

- `task.list`가 `data` 또는 `tasks`로 응답할 수 있어 adapter에서 내부 표준 필드로 normalize했다.
- `task.listMessages`는 단순 user/assistant message만 있는 것이 아니라 operational event가 섞여 있어 content-bearing event를 보존했다.
- 첨부파일은 원문 전체 저장 대신 metadata-only artifact로 다뤘다.
- private URL은 기본적으로 저장/노출하지 않고, public task URL만 evidence navigation 목적으로 남겼다.
- 파일명과 attachment metadata는 redaction 후 저장했다.

## 소스 2. MCP 토큰 비용을 줄이는 Evidence Contract 설계

### 목적

MCP 클라이언트가 과거 작업 맥락을 가져올 때 raw transcript를 통째로 LLM context에 넣지 않고, 필요한 증거만 작게 참조하도록 설계 방향을 잡았다.

### 내 역할

Geond가 MCP 서버로 쓰일 때 토큰을 과도하게 소비할 위험을 분석하고, compact-by-default, evidence-ref-first, lazy-detail 원칙을 제안했다.

### 사용 기술

- MCP
- PostgreSQL
- pgvector
- pg_trgm
- evidence schema
- retrieval API

### 가장 어려웠던 문제

저장소의 크기와 LLM context 비용을 구분해야 했다. 실제 리스크는 데이터베이스에 vector를 저장하는 비용이 아니라, MCP 응답에서 긴 transcript, 큰 code graph, dashboard read model을 그대로 반환할 때 발생하는 payload bloat였다.

### 문제를 발견한 방법

schema와 retrieval 코드를 확인했다. `embeddings.embedding vector(1536)`와 `messages` 검색 인덱스가 이미 존재했기 때문에, 검색 저장소 부재보다 응답 크기와 소비 방식이 더 큰 병목이라고 판단했다.

### 내가 시도한 해결책

MCP 응답은 기본적으로 아래 필드 중심으로 작게 반환하도록 설계 방향을 잡았다.

- `ref` 또는 `id`
- source/session metadata
- 짧은 snippet 또는 reason
- score/rank
- follow-up detail path
- canonical evidence reference

### 왜 그 해결책을 선택했는지

사람이 보는 dashboard는 풍부한 정보가 필요하지만, LLM이 보는 MCP 응답은 context window와 비용에 직접 영향을 준다. 따라서 같은 데이터를 다루더라도 human-facing read model과 LLM-facing tool response는 다르게 설계해야 한다고 판단했다.

### 결과

Geond의 MCP surface를 raw transcript pipe가 아니라 shared evidence protocol로 설명할 수 있게 됐다. `geond.evidence.v1` schema와 contract test를 통해 MCP 도구들이 안정적인 evidence reference를 반환하도록 검증하는 방향이 잡혔다.

### 데이터 엔지니어 직무와 연결되는 지점

데이터 제공 계층에서 downstream consumer의 사용 비용과 응답 계약을 고려한 설계 경험이다. 단순 저장이나 조회가 아니라, AI agent가 소비할 수 있는 형태로 데이터 product를 설계한 사례로 설명할 수 있다.

### 면접에서 자신 있게 설명 가능한 부분

- MCP 응답의 병목은 DB 저장량보다 LLM context로 들어가는 payload 크기다.
- `geond.evidence.v1`는 `schema`, `kind`, `target_id`, `locator`, `metadata` 같은 안정적인 구조를 가진다.
- dashboard read model과 MCP response model은 목적이 다르므로 분리해야 한다.
- raw session 확장은 opt-in으로 두고, 기본값은 compact evidence reference가 되어야 한다.

## 소스 3. PostgreSQL 기반 Hybrid Search 구조 분석

### 목적

여러 에이전트가 남긴 메시지와 변경 이력을 keyword, vector, hybrid 방식으로 검색할 수 있게 하는 구조를 분석하고, 어떤 개선이 우선인지 판단했다.

### 내 역할

Geond의 검색 구조를 확인하고, 검색 품질 문제와 MCP 토큰 비용 문제를 분리해서 판단했다.

### 사용 기술

- PostgreSQL
- pgvector
- pg_trgm
- full-text search
- HNSW index
- Python retrieval layer

### 데이터 규모

`messages`, `events`, `file_snapshots`, `embeddings` 등 로그/증거성 테이블을 중심으로 구성된다. embedding은 `vector(1536)`으로 저장된다.

### 가장 어려웠던 문제

검색 품질을 올린다고 무조건 새 검색엔진이나 별도 벡터 DB를 붙이는 것이 맞는지 판단해야 했다.

### 내가 직접 한 기술적 판단

현재 구조는 이미 keyword search, trigram search, vector search, hybrid merge가 있으므로, 당장 새로운 검색 저장소를 추가하기보다 response-size contract, rerank, evaluation set을 강화하는 것이 더 합리적이라고 판단했다.

### 왜 그 방법을 선택했는지

문제의 중심이 "검색할 수 없는가"가 아니라 "검색 결과를 LLM에게 어떤 크기와 형태로 전달할 것인가"에 있었기 때문이다.

### 전후 변화

처음에는 검색엔진 확장 또는 더 많은 retrieval 기능이 핵심처럼 보일 수 있었지만, 분석 후에는 기존 PostgreSQL 검색 스택을 유지하면서 evidence ref와 compact response contract를 강화하는 방향으로 정리됐다.

### 면접에서 자신 있게 설명 가능한 부분

- `messages.content`에는 full-text/trigram GIN index가 적용된다.
- `embeddings.embedding vector(1536)`에는 HNSW vector index가 적용된다.
- hybrid retrieval은 keyword 결과와 vector 결과를 rank 기반으로 merge한다.
- 저장소 선택보다 consumer-facing payload contract가 더 중요한 병목일 수 있다.

## 자기소개서용 문장 초안

여러 AI 개발 도구에서 생성되는 작업 로그가 도구별 형식으로 흩어져 후속 에이전트가 맥락을 재사용하기 어려운 문제가 있었습니다. 저는 Manus API v2 연동을 검증하면서 mock 기준 구현이 실제 API의 응답 구조와 어긋나는 지점을 발견했고, adapter 계층에서 `task.list`, `task.listMessages`, 첨부파일, blocked 상태를 표준 모델로 정규화했습니다. 또한 첨부파일은 원문 저장 대신 metadata-only artifact로 저장하고, 파일명과 URL 메타데이터는 redaction 후 PostgreSQL에 적재하도록 수정했습니다. 이 과정에서 CLI 문서와 테스트를 함께 맞추고 전체 테스트를 통과시켜, 외부 API 기반 작업 기록을 검색 가능한 evidence 데이터로 안정적으로 수집하는 흐름을 만들었습니다.

## 면접 예상 질문과 답변 포인트

### Q. 이 프로젝트에서 실제로 발견한 문제는 무엇인가요?

mock fixture 기준으로는 통과하지만 실제 Manus API v2 shape와 맞지 않는 부분이 있었다. 예를 들어 `task.list` 응답이 `tasks`가 아니라 `data`로 내려올 수 있었고, `task.create`도 예전 `{title, prompt}` 형태가 아니라 message content body를 사용해야 했다.

### Q. 본인이 직접 한 기술적 판단은 무엇인가요?

외부 API의 여러 shape를 downstream 로직에서 각각 처리하지 않고, adapter boundary에서 내부 표준 모델로 정규화하기로 판단했다. 또한 파일 첨부는 원문 저장보다 metadata-only 저장이 더 안전하다고 봤다.

### Q. 왜 metadata-only artifact 방식을 선택했나요?

첨부파일 원문에는 민감정보나 대용량 바이너리가 포함될 수 있다. 검색과 evidence navigation에는 파일명, mime type, size, source message 같은 메타데이터만으로도 충분한 경우가 많기 때문에, 기본 저장 경로는 metadata-only로 두고 content download는 별도 제한을 두는 방식이 안전하다.

### Q. 데이터 엔지니어링과 어떻게 연결되나요?

외부 API에서 들어오는 비정형/반정형 JSON을 내부 schema로 정규화하고, 민감정보를 비식별화한 뒤, 검색 가능한 저장소에 적재했다. 수집, 정제, 저장, 검증, 재사용이라는 데이터 파이프라인의 핵심 흐름을 다룬 경험이다.

### Q. MCP 토큰 비용 문제는 어떻게 판단했나요?

DB에 vector를 저장하는 비용보다, MCP 응답으로 raw transcript나 큰 dashboard payload가 LLM context에 들어가는 비용이 더 큰 리스크라고 판단했다. 그래서 기본 응답은 evidence reference와 짧은 snippet 중심으로 만들고, 상세 내용은 필요할 때만 가져오는 lazy-detail 방향이 맞다고 봤다.

## 더 강하게 만들기 위해 추가로 채굴할 것

- 실제 Manus task 샘플을 가져와 before/after normalization 예시 만들기
- 테스트 실행 결과 스크린샷 또는 로그 보관하기
- `task.listMessages` 이벤트 타입별 정규화 표 만들기
- redaction 전후 예시 만들기
- hybrid search query가 keyword/vector 결과를 어떻게 merge하는지 간단한 다이어그램 만들기
- "내가 고친 뒤 어떤 실패가 방지됐는지"를 2~3개 구체 사례로 정리하기

