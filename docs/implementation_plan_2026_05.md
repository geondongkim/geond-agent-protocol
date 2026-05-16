# Geond Implementation Plan — 2026-05

## Purpose

이 문서는 `docs/agent_doc_consumption_guide.md` 라우팅에 따라 모은 네 개의 신규 문서
(`geond_mcp_repository_evaluation.md`, `agent_operating_loop.md`,
`ai_usage_observability.md`, `geond_roadmap_backlog.md`)를 바탕으로
**실제 구현 가능한 작업 단위**로 풀어 쓴 상세 계획서입니다.

- Phase 0 문서 작업은 이미 완료된 상태(현 git diff)이므로 본 계획은 **Phase 1 ~ Phase 5**의
  P0/P1 항목을 다룹니다.
- 각 작업은 *Scope → Files → Steps → Tests → Acceptance → Risks* 순서를 따릅니다.
- 애매한 결정 지점은 `Decision D-#`로 라벨링하고 **선택지 표**를 첨부했습니다.
  사용자가 선택한 뒤 구현에 착수하면 됩니다.

## 라우팅: 어떤 문서가 근거인가

| 본 계획 Section | 근거 문서 |
| --- | --- |
| §1 start-task / finish-task | `agent_operating_loop.md` §Recommended Start/Finish, `geond_roadmap_backlog.md` Phase 1 |
| §2 `llm_usage_events` 스키마 + 저장 API | `ai_usage_observability.md` §Proposed Data Model, Phase 2 USAGE-001~004 |
| §3 Importer usage extraction | `ai_usage_observability.md` §Importer Responsibilities, Phase 3 IMPORT-001~005 |
| §4 Model pricing registry | `ai_usage_observability.md` §Model Pricing |
| §5 Usage vs Evidence 대시보드 | `ai_usage_observability.md` §Dashboard Views, Phase 4 |
| §6 Anti-tokenmaxxing signals | `ai_usage_observability.md` §Anti-Tokenmaxxing Signals, Phase 5 |

---

## 2026-05-17 Implementation Update

구현은 평가서 피드백을 반영해 **Phase 0: foundation corrections**부터 시작했습니다.
아래의 초기 선택지 표보다 이 업데이트가 우선합니다.

- D-1은 Hybrid로 확정했습니다. `cli.py`는 parser/dispatch를 유지하고, 복잡한 orchestration은 후속 작업에서 `cli_tasks.py`로 분리합니다.
- D-2는 versioned migrations로 확정했습니다. 첫 구현은 `geond migrate --all`과 `schema_migrations` 기반 idempotent runner를 추가하고, `schemas/002_collaboration_linkage.sql`로 action/changeset session linkage indexes를 추가했습니다.
- `record-agent-action` CLI primitive를 먼저 추가했습니다. `--action-type`과 기존 문서의 `--action-kind`는 같은 옵션으로 동작합니다.
- `record-agent-action`과 `record-changeset`은 `--session-id` 또는 `--session-external-id`를 받아 imported session evidence에 명시적으로 연결할 수 있습니다.
- `start-task` / `finish-task` wrapper를 구현했습니다. `cli.py`는 parser/dispatch만 맡고, orchestration은 `src/geond/cli_tasks.py`로 분리했습니다.
- `llm_usage_events` storage slice를 시작했습니다. `schemas/003_llm_usage.sql`, `src/geond/storage/usage.py`, `usage-summary` CLI가 추가되어 importer 작업 전에 수동/테스트 usage event를 요약할 수 있습니다.
- D-6은 Python rule engine + SQL rollup helper 방향으로 수정합니다. 순수 SQL view 단독 구현은 v1 기본안에서 제외합니다.
- Usage schema 번호는 한 칸 밀립니다. collaboration linkage가 `002`, `llm_usage_events`는 후속 `003`, pricing은 그 다음 migration으로 둡니다.

검증된 첫 slice:

- targeted Ruff 통과
- `tests/test_cli_coordination.py`, `tests/test_db_migrations.py`, `tests/test_resources_and_coordination.py` 통과

---

## 0. Cross-cutting Decisions (먼저 골라주세요)

이 결정들은 §1~§6 모두에 영향을 줍니다.

### D-1. CLI 패키징 전략 — `start-task`/`finish-task` 구현 위치

| 선택지 | 설명 | 장점 | 단점 |
| --- | --- | --- | --- |
| **A. cli.py 안에 인라인 추가** (Recommended) | 기존 1488줄 파일에 subparser 두 개 추가 | 변경 최소, 다른 명령과 일관 | 파일이 더 길어짐 |
| B. `src/geond/cli_tasks.py` 신규 모듈 + import | 가독성 ↑, 단위 테스트 격리 쉬움 | 파일 분할 |  cli.py main()에서 register 함수 호출이라는 새 패턴이 생김 |
| C. MCP tool로만 제공 | Codex/Claude가 도구 호출로 처리 | CLI 의존성 ↓ | 터미널 사용자 경험 손실, 백로그가 CLI 기준임 |

> 백로그(DOC/Phase 1)는 CLI 명령으로 명시되어 있어 A가 기본 권장.

### D-2. 마이그레이션 파일 분리

| 선택지 | 설명 | 장점 | 단점 |
| --- | --- | --- | --- |
| **A. `schemas/002_llm_usage.sql` 신규 추가** (Recommended) | 기존 001은 immutable로 유지 | 운영 DB 안전, rollback 명확 | migrate 명령이 여러 파일 지원해야 함 |
| B. 001_initial.sql에 append | 단일 파일 유지 | 이미 마이그된 DB에 재실행 필요 → 위험 |
| C. Alembic 도입 | 장기적으로 정석 | 새 의존성, 학습 비용 |

→ `migrate` 명령이 multiple files를 처리하는지 먼저 확인 필요. 미지원이면 작은 패치를 함께 진행.

### D-3. 토큰 추정기 (estimated 토큰을 무엇으로 채울 것인가)

| 선택지 | 설명 | 장점 | 단점 |
| --- | --- | --- | --- |
| **A. `len(text) // 4` 휴리스틱** (Recommended for v1) | 의존성 0, 빠름 | 영어 가정. 한글/CJK는 과소 추정 |
| B. `tiktoken` (cl100k_base) | OpenAI 모델과 정합 | Anthropic/Copilot에는 정합 X, 추가 의존성 |
| C. 모델별 라우팅 (provider→tokenizer) | 가장 정확 | 구현 복잡, 모델별 의존성 |

→ schema에는 `estimated=true` 라벨만 정확히 찍히면 v1은 A로 충분. B/C는 IMPORT-004 추적 백로그에 보존.

### D-4. `estimated_cost_usd` 저장 시점

| 선택지 | 설명 |
| --- | --- |
| **A. write-time 저장** (Recommended) | importer가 `model_pricing` 룩업 후 채워서 INSERT |
| B. read-time 계산 | 가격 변경에 즉시 반응, 그러나 모든 쿼리에 JOIN |
| C. 둘 다 — write-time 스냅샷 + `priced_at` | 가격 이력 대응 가능, 컬럼 1개 추가 |

→ 문서가 "price version 또는 priced_at"을 명시했으므로 **C**도 합리적. 단순함을 우선하면 A.

### D-5. 출력 포맷 우선순위

| 선택지 | 설명 |
| --- | --- |
| **A. JSON 기본 + `--format markdown` 옵션** (Recommended) | 자동화 친화, dashboard와 일관 |
| B. Markdown 기본 | 사람 친화, 그러나 다른 명령들과 불일치 |

→ 기존 `review-context`가 `--format markdown` 패턴을 쓰므로 A가 일관.

### D-6. Risk Signals 계산 위치

| 선택지 | 설명 | 장점 | 단점 |
| --- | --- | --- | --- |
| **A. read-time SQL 뷰** (Recommended for v1) | `risk_signals_v` 같은 VIEW | 즉시 최신, 코드 단순 | 큰 워크스페이스에서 느려질 수 있음 |
| B. Materialized view + refresh CLI | 빠른 조회 | refresh 운영 부담 |
| C. Python 레이어 계산 | 룰 변경 쉬움 | DB와 분리되어 SQL 분석 어려움 |

→ 임계치 설정 가능성을 위해 결국 Python 룰 + SQL 보조가 자연스러움. v1은 A로 시작하고 임계치는 환경변수/`config.py` 노출.

---

## 1. `start-task` / `finish-task` CLI 래퍼

### 1.1 Scope
`agent_operating_loop.md`의 Start/Finish 시퀀스를 한 줄 명령으로 묶기. 기존 primitive(`record-agent-action`, `review-context`, `reserve-files`, `reserve-symbols`, `record-handoff`, `record-changeset`)를 **재호출**하기만 하고 새 SQL은 만들지 않음.

### 1.2 Files
- `src/geond/cli.py` — subparser 두 개 + 핸들러 두 개.
- (D-1 B 선택 시) `src/geond/cli_tasks.py`.
- `tests/test_cli_start_finish.py` — 신규.

### 1.3 Steps — `start-task`
1. 인자 파싱: `workspace_id_or_uri`, `--agent-name` (필수), `--intent`, `--file`(반복), `--symbol`(반복), `--reserve/--no-reserve`(기본 False), `--ttl-minutes`(기본 120), `--dry-run`, `--format json|markdown`.
2. 워크스페이스 해석 (`require_workspace_id`).
3. **읽기 단계 (mutation 없음)**
   - `get_dashboard_overview(limit=25)`
   - `list_handoff_summaries(status="open")`
   - `get_active_reservations(file_paths=...)` 및 `get_symbol_conflicts(symbols=...)`
   - `review_workspace_context(intent, file_paths, symbols)`
4. **쓰기 단계 (dry-run 아닐 때만)**
   - `record_agent_action(action_type="task_start", summary=intent, status="recorded")`
   - `--reserve` 면 `reserve_files` / `reserve_symbols` 호출. 충돌 정책은 워크스페이스 정책(`reservation_conflict_policy`)을 그대로 따름.
5. 결과 패키지 출력:
   - `workspace`, `open_handoffs[]`, `conflicts{files,symbols}`, `review`, `reservations_created[]`, `next_action_hint`.

### 1.4 Steps — `finish-task`
1. 인자: `--summary`(필수), `--next-action`, `--tested-command`(반복), `--risk`(반복), `--blocker`(반복), `--to-agent`, `--changeset-file`(반복, `path:status` 포맷), `--release-reservations/--renew-reservations/--keep-reservations`(기본 keep), `--dry-run`, `--format`.
2. `record_agent_action(action_type="task_finish", summary=summary)`.
3. `--changeset-file` 있으면 `record_changeset` (재사용 가능한 helper로 추출).
4. `record_handoff_summary(template="standard", ...)`.
5. 정책 플래그에 따라 `release_reservation` / `renew_reservation` 반복 호출.
6. 최종 패키지: handoff_id, changeset_id, released/renewed list, agent_action_id, tested_commands echo.

### 1.5 Tests
- `start-task --dry-run` 가 DB에 쓰지 않는지 (`mcp__geond__list_reservation_events` 비교).
- `start-task --reserve` 후 `get_active_reservations`에 보이는지.
- `finish-task --release-reservations` 후 해당 reservation 이 inactive 인지.
- 충돌 워크스페이스에서 `start-task --reserve` 시 정책별 동작(advisory/strict/override).
- JSON 출력 키 셋 안정성 (스냅샷).

### 1.6 Acceptance (백로그 매핑)
- Phase 1 Acceptance 전체 충족: dry-run, JSON+md, no-mutation default, 액션 기록.

### 1.7 Risks
- 기존 helper 들이 내부에서 `conn` 컨텍스트를 어떻게 잡는지에 따라 트랜잭션 1회로 묶는 게 어려울 수 있음 → 첫 버전은 각 호출이 개별 트랜잭션, 실패 시 부분 상태가 남을 수 있음을 명시.

---

## 2. `llm_usage_events` 스키마 + 저장 API (USAGE-001 ~ 004)

### 2.1 Files
- `schemas/002_llm_usage.sql` (D-2 선택에 따라).
- `src/geond/storage/usage.py` — 신규 모듈 (`insert_usage_event`, `query_usage`, `summarize_usage`).
- `src/geond/cli.py` — `usage-summary` subparser.
- `tests/fixtures/usage/` — 케이스별 fixture.
- `tests/test_usage_storage.py`, `tests/test_cli_usage_summary.py`.

### 2.2 Schema (확정안)
`ai_usage_observability.md`의 DDL 그대로 + 두 가지 보강:

```sql
-- 002_llm_usage.sql
CREATE TABLE llm_usage_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid REFERENCES workspaces(id) ON DELETE CASCADE,
    session_id uuid REFERENCES sessions(id) ON DELETE SET NULL,
    agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    source text NOT NULL,                     -- codex|claude_code|vscode_copilot|mcp|manual
    provider text,
    model text,
    operation text,                           -- chat.completion|message|tool_call|embedding
    input_tokens integer,
    output_tokens integer,
    cached_input_tokens integer,
    reasoning_tokens integer,
    total_tokens integer,
    estimated boolean NOT NULL DEFAULT false,
    estimated_cost_usd numeric,
    priced_at timestamptz,                    -- D-4 C 선택 시 (가격 스냅샷 시각)
    source_record_id text,                    -- importer 감사용
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_llm_usage_workspace_created
    ON llm_usage_events(workspace_id, created_at DESC);
CREATE INDEX idx_llm_usage_session ON llm_usage_events(session_id);
CREATE INDEX idx_llm_usage_agent ON llm_usage_events(agent_id, created_at DESC);
CREATE INDEX idx_llm_usage_model ON llm_usage_events(provider, model);
```

**D-7. `source_record_id` 컬럼을 둘까?** (Recommended Yes — importer 감사 및 중복 방지에 필수)

### 2.3 저장 API
```python
def insert_usage_event(conn, *, workspace_id, session_id=None, agent_id=None,
                       source, provider=None, model=None, operation=None,
                       input_tokens=None, output_tokens=None,
                       cached_input_tokens=None, reasoning_tokens=None,
                       estimated=False, metadata=None,
                       source_record_id=None) -> str: ...

def summarize_usage(conn, *, workspace_id, since=None, until=None,
                    group_by=("source","model")) -> list[dict]: ...
```
- `total_tokens`는 트리거 또는 Python에서 계산 후 INSERT.
- 비용은 §4 가격 등록 이후에만 채움 (없으면 NULL).

### 2.4 CLI `usage-summary`
- 출력 필드: 워크스페이스 ID, since/until, 총 토큰, exact/estimated 비율, source별 표, model별 표, 추정 비용 합계.
- D-5에 따라 JSON 기본 + `--format markdown`.

### 2.5 Tests
- 정확한 usage 삽입 → 합계 일치.
- estimated=true 비율 계산 정확성.
- 누락된 usage(모든 토큰 NULL) 처리.
- redaction 메타 보존(민감 키는 사전에 제거되었음을 명시한 metadata 키만 허용).

### 2.6 Risks
- `model_pricing` 미존재 시 비용 컬럼은 NULL 유지. `usage-summary`는 NULL 무시 합계.

---

## 3. Importer Usage Extraction (IMPORT-001 ~ 005)

### 3.1 공통 원칙
- 추출 실패해도 import 자체는 성공해야 함.
- 모든 estimated 값에 `estimated=true`.
- thinking/raw reasoning 텍스트는 절대 metadata에 넣지 않음.
- `source_record_id`로 idempotency 확보 (재import 시 중복 INSERT 방지: `ON CONFLICT (source, source_record_id) DO NOTHING` — UNIQUE 인덱스 필요).

**D-8. UNIQUE 제약을 둘까?** (Recommended Yes — `(source, source_record_id)` UNIQUE WHERE source_record_id IS NOT NULL)

### 3.2 Codex (IMPORT-001)
- `src/geond/adapters/codex.py`에서 메시지 단위 또는 응답 단위로 `usage` 블록 탐색.
- 발견 시 `provider="openai"`, `model=<from session>`, exact tokens 기록.
- 없으면 사용자/어시스턴트 메시지 텍스트 → `len//4` 기반 estimate.

### 3.3 Claude Code (IMPORT-002)
- JSONL의 message 메타에서 `usage.input_tokens`, `usage.output_tokens`, `usage.cache_*` 추출.
- thinking 블록은 카운팅에서 제외 — reasoning_tokens 컬럼은 비워두거나, provider가 따로 카운트를 주면 그 값만 신뢰.

### 3.4 VS Code Copilot (IMPORT-003)
- 트랜스크립트에 usage가 거의 없음 → 대부분 estimated.
- prompt/response 수만 카운팅, `metadata.note="vscode_copilot_estimate"` 라벨.

### 3.5 Token estimation fallback (IMPORT-004)
- D-3 선택 적용.
- 한 함수 `estimate_tokens(text: str, model: str|None) -> int`로 격리.

### 3.6 Fixtures (IMPORT-005)
`tests/fixtures/usage/`:
- `codex_exact.jsonl`, `codex_partial.jsonl`, `codex_missing.jsonl`
- `claude_with_usage.jsonl`, `claude_thinking_only.jsonl`
- `copilot_chat.json`

### 3.7 Tests
- 각 fixture로 importer를 돌린 뒤 `llm_usage_events` row 수 / estimated 플래그 / source_record_id 확인.
- 같은 fixture를 두 번 import → row 수 불변(idempotent).

---

## 4. Model Pricing Registry

### 4.1 Files
- `schemas/003_model_pricing.sql`
- `src/geond/storage/pricing.py` — `get_price(provider, model, when)`, `seed_from_yaml(path)`.
- `config/pricing.example.yaml` — seed.
- `tests/test_pricing.py`.

### 4.2 Decision
**D-9. 가격 시드 소스**

| 선택지 | 설명 |
| --- | --- |
| **A. 로컬 YAML seed** (Recommended) | `geond seed-pricing <yaml>` CLI로 적재. 오프라인. |
| B. 하드코딩 (Python dict → INSERT) | 단순, 그러나 PR 없이 가격 변경 불가 |
| C. 외부 fetch (provider API) | 운영 부담 ↑, 인증 필요 |

### 4.3 Pricing 적용 흐름
1. importer가 usage row 만들 때 `get_price(provider, model, now())` 호출.
2. 가격 hit → `estimated_cost_usd`, `priced_at` 채움.
3. 가격 miss → 두 컬럼 NULL.

---

## 5. Usage vs Evidence 대시보드 (Phase 4)

### 5.1 Storage 측 (먼저)
`src/geond/storage/dashboard.py`에 함수 추가:
- `get_usage_summary(workspace_id, since, until)`
- `get_usage_by_source(...)`, `get_usage_by_model(...)`
- `get_usage_vs_evidence(workspace_id, window)` — 동일 기간의 changeset/test/handoff/reservation 카운트와 토큰을 JOIN.
- `get_data_quality(workspace_id)` — exact vs estimated 비율.

### 5.2 Dashboard server (`src/geond/dashboard_server.py`)
- 신규 라우트 `/api/usage/summary`, `/api/usage/by-source`, `/api/usage/by-model`, `/api/usage/vs-evidence`, `/api/usage/data-quality`.
- 기존 인증/abort 처리 패턴 그대로 (recent commit `102f342` 참고).

### 5.3 UI
- 기본 첫 화면은 **워크스페이스/팀 롤업**. 개인 드릴다운은 별도 탭(또는 ?personal=1 쿼리).
- 패널: Usage Summary, Usage by Source, Usage by Model, Usage by Evidence, Data Quality, Risk Signals(§6), Enablement Signals.
- exact/estimated 토글이 아니라 **같은 카드에 두 숫자를 동시에** 보여줌 (문서 규칙).

**D-10. 개인 드릴다운 접근 통제**

| 선택지 | 설명 |
| --- | --- |
| **A. `config.py`에 `allow_personal_drilldown: bool=False`** (Recommended for v1) | 단순, 팀 단위 ON/OFF |
| B. 워크스페이스별 `coordination_policy`에 필드 추가 | DB 일관 |
| C. 로그인 도입 후 RBAC | 큰 변화, P3 |

### 5.4 Tests
- `tests/test_usage_dashboard.py` — read-model 정합성.
- `tests/test_dashboard_server_usage.py` — 라우트 응답 스냅샷.

---

## 6. Anti-Tokenmaxxing Signals (Phase 5)

### 6.1 Files
- `src/geond/storage/signals.py` — `compute_signals(workspace_id, window, thresholds)`.
- `src/geond/config.py` — `RiskSignalThresholds` dataclass + env override.
- `tests/test_signals.py`.

### 6.2 Signal 구현 매핑

| ID | 룰 (의사코드) |
| --- | --- |
| SIG-001 high_usage_low_changeset | 세션당 tokens > T1 AND 같은 세션 ID로 연결된 changeset = 0 |
| SIG-002 high_prompts_no_handoff | 윈도우 내 prompts > T2 AND open/closed handoff count = 0 |
| SIG-003 expensive_model_low_risk | model.tier == "expensive" AND task가 changeset/test 부재 |
| SIG-004 repeated_sessions_same_intent | 동일 agent_name + 유사 intent 텍스트(trigram > 0.6) 세션 N개 |
| SIG-005 many_tool_traces_no_tests | tool_call operation 수 > T5 AND tested_command 없는 handoff |
| SIG-006 stale_reservation_high_activity | reservation age > TTL × 2 AND 같은 agent의 다른 영역 활동 존재 |
| SIG-007 high_usage_training_signal | high_usage_low_changeset 가 같은 agent에 N주 연속 |

### 6.3 출력 규약
- 각 signal은 `{signal_id, severity:"review"|"enablement", evidence_refs:[...], explanation:str}`.
- "performance failure" 라벨 사용 금지.
- 임계치 기본값은 보수적으로(노이즈 ↓), env로 조정.

### 6.4 Tests
- 합성 데이터로 각 signal trigger 케이스 / non-trigger 케이스 둘 다.
- 임계치 override 동작.

---

## 7. 작업 순서와 의존 그래프

```
[D-1~D-10 결정]
      │
      ▼
§1 start-task/finish-task ─── (단독으로 출시 가능, 즉시 가치)
      │
      ▼
§2 llm_usage_events 스키마 + storage + CLI usage-summary
      │
      ├──► §3 importers (Codex → Claude → Copilot 순서, 각 PR 분리)
      │
      ├──► §4 model_pricing (§3와 병행 가능)
      │
      ▼
§5 Usage vs Evidence dashboard (storage 함수 먼저, route 다음, UI 마지막)
      │
      ▼
§6 Risk signals (§2,§3,§5 완료 후)
```

추천 PR 분할:
1. `feat: start-task and finish-task wrappers` (§1)
2. `feat: llm_usage_events schema and storage` (§2 스키마 + storage + 테스트)
3. `feat: usage-summary cli` (§2 CLI)
4. `feat: codex usage extraction` (§3 IMPORT-001)
5. `feat: claude code usage extraction` (§3 IMPORT-002)
6. `feat: vscode copilot usage extraction` (§3 IMPORT-003)
7. `feat: model pricing registry` (§4)
8. `feat: usage dashboard read models` (§5 storage)
9. `feat: usage dashboard routes + ui` (§5 server/ui)
10. `feat: anti-tokenmaxxing signals` (§6)

---

## 8. 위험과 미해결 질문

- **트랜잭션 경계**: §1의 wrapper들이 여러 helper를 호출하면서 부분 실패가 가능. 1차 버전은 best-effort + 결과 패키지에 실패 항목 표시.
- **재import 시 row 폭증**: §3의 idempotency를 첫 PR부터 확보하지 않으면 fixture 테스트가 중복으로 깨짐 → D-8 Yes 권장.
- **CJK 토큰 추정 정확도**: 한글이 많은 워크스페이스에서 `len//4`는 보통 30~50% 과소 추정. 대시보드에 `data_quality` 패널이 있으므로 "estimated 비율"로 신뢰도가 노출되긴 함.
- **Migration runner**: `geond migrate`가 여러 SQL 파일 / 멱등 실행을 지원하는지 미확인 (D-2 A를 고른 뒤 첫 PR 전에 점검 필요).
- **백로그 ID 부착**: 모든 PR 제목/본문에 `USAGE-001` 같은 ID를 명시하면 `geond_roadmap_backlog.md`와 1:1 추적 가능.

---

## 9. 선택지 요약 (한 번에 응답하기 좋게)

| ID | 질문 | 권장 |
| --- | --- | --- |
| D-1 | start/finish CLI 구현 위치 | A. cli.py 인라인 |
| D-2 | 마이그레이션 파일 분리 | A. 002_llm_usage.sql 신규 |
| D-3 | 토큰 추정기 | A. len//4 (v1) |
| D-4 | estimated_cost_usd 저장 시점 | A. write-time (또는 C, priced_at 추가) |
| D-5 | 출력 포맷 | A. JSON 기본 + --format markdown |
| D-6 | Risk signals 계산 | A. read-time SQL view + Python 룰 |
| D-7 | source_record_id 컬럼 추가 | Yes |
| D-8 | (source, source_record_id) UNIQUE | Yes |
| D-9 | 가격 시드 소스 | A. 로컬 YAML |
| D-10 | 개인 드릴다운 접근 통제 | A. config 플래그 |

선택지를 알려주시면 D-결정을 반영해 첫 PR(§1 start-task/finish-task)부터 착수하겠습니다.
