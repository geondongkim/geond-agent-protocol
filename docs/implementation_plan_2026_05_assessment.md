> 2026-05-17 implementation note: 첫 구현 slice에서 이 평가의 핵심 선행 지적 중
> `record-agent-action` CLI 부재, `geond migrate --all` 부재, action/changeset의
> session linkage 미연결 문제를 해결하기 시작했습니다. 이 문서는 원 평가 결과로 유지하되,
> 현재 코드 상태는 `docs/implementation_plan_2026_05.md`의 implementation update를 우선합니다.

**총평**

`docs/implementation_plan_2026_05.md`는 제가 작성했던 가이드를 꽤 잘 흡수했습니다. 특히 [agent_doc_consumption_guide.md](agent_doc_consumption_guide.md) → 평가 문서 → 운영 루프 → usage observability → roadmap으로 이어지는 흐름을 실제 구현 단위로 잘 쪼갰고, `Decision D-#`로 애매한 선택지를 분리한 점이 좋습니다.

제 평가는 **8 / 10** 정도입니다. 바로 구현에 들어가도 될 만큼 구체적이지만, 몇 가지는 먼저 고치거나 더 명확히 해야 합니다.

**좋은 점**


**수정이 필요한 핵심 이슈**

1. **`record-agent-action` CLI가 현재 없습니다.**  
   계획은 `record_agent_action`을 primitive CLI처럼 다루는데, 현재 CLI에는 `reserve-files`, `record-handoff`, `record-changeset` 등은 있어도 `record-agent-action` subcommand는 없습니다. Storage 함수와 MCP tool은 있지만 CLI primitive는 없어요.  
   따라서 `start-task`가 직접 storage 함수를 호출하거나, 먼저 `record-agent-action` CLI를 추가해야 합니다.

2. **`changeset`과 session/action 연결이 약합니다.**  
   anti-tokenmaxxing signal에서 “같은 session ID로 연결된 changeset”을 전제로 하는데, 현재 `record_changeset` storage 함수는 `session_id`를 받지 않습니다. schema에는 `changesets.session_id`가 있지만 실제 insert에서 안 씁니다.  
   Usage vs Evidence를 제대로 하려면 `record_changeset(..., session_id=None, agent_name=None)` 또는 metadata convention을 먼저 정해야 합니다.

3. **`llm_usage_events` DDL에 줄바꿈 오류 가능성이 있습니다.**  
   계획의 schema 부분에서 `source_record_id text` 주석과 `metadata jsonb`가 같은 줄로 붙어 보입니다. SQL에서는 `--` 뒤가 주석이므로 `metadata` 컬럼이 사라질 수 있습니다. 구현 전 문서 DDL을 반드시 정리해야 합니다.

4. **migration runner 선행 확인이 필요합니다.**  
   현재 `geond migrate`는 기본적으로 `--schema schemas/001_initial.sql` 한 파일을 실행합니다. `schemas/002_llm_usage.sql` 방식은 맞지만, 자동 다중 migration은 현재 없습니다.  
   그래서 첫 구현 PR에서 `geond migrate --schema schemas/002_llm_usage.sql`로 갈지, `migrate-all`을 만들지 결정해야 합니다.

5. **CJK token estimation 설명은 조정해야 합니다.**  
   `len(text) // 4`는 영어 rough estimate에는 괜찮지만 한국어/CJK에서는 보통 **과소추정**될 가능성이 큽니다. 계획에는 과소/과대 방향이 애매하게 적혀 있는데, dashboard에는 “estimated, rough, likely undercounts CJK” 같은 품질 표시가 필요합니다.

**질문/선택지 설명**

| ID | 계획 추천 | 내 평가 | 선택하지 않으면 생기는 일 |
|---|---|---|---|
| D-1 CLI 위치 | A. `cli.py` inline | **Hybrid 추천**: parser wiring은 `cli.py`, 로직은 `cli_tasks.py` | A만 고르면 빠르지만 `cli.py`가 더 비대해집니다. C(MCP only)를 고르면 터미널 운영 루프가 약해져 백로그 의도와 어긋납니다. |
| D-2 migration 분리 | A. `002_llm_usage.sql` | 동의. 단 migration runner 전략 추가 필요 | 001에 append하면 기존 DB와 재실행 안전성이 나빠집니다. Alembic은 지금 단계에선 과합니다. |
| D-3 token estimator | A. `len//4` | v1은 동의. 단 CJK 과소추정 경고 필수 | 처음부터 tokenizer 라우팅을 하면 구현이 늦어집니다. 반대로 추정 라벨 없이 쓰면 PM 지표가 거짓 정밀도를 갖습니다. |
| D-4 cost 저장 시점 | A 또는 C | **C 추천**: write-time snapshot + `priced_at` | A만 쓰면 가격 변경 이력 설명이 약합니다. B(read-time)는 모든 report가 pricing join에 의존하고 과거 비용이 흔들립니다. |
| D-5 output format | A. JSON 기본 + markdown 옵션 | 동의 | markdown 기본이면 agent/CI/dashboard 재사용성이 떨어집니다. |
| D-6 risk signal 계산 | A. SQL view | **Python rules + SQL rollup helper 추천** | SQL view만 쓰면 threshold/env 설정, evidence_refs, 설명문 생성이 불편합니다. materialized view는 아직 이릅니다. |
| D-7 `source_record_id` | Yes | 강하게 동의 | 없으면 importer 재실행 시 중복/감사 추적이 어려워집니다. |
| D-8 unique constraint | Yes | 강하게 동의 | 없으면 reimport 때 usage row가 폭증합니다. 단 source_record_id가 전역 unique하게 구성되는지 확인해야 합니다. |
| D-9 pricing source | local YAML | 동의 | hardcoding은 가격 갱신마다 코드 PR이 필요하고, provider fetch는 인증/네트워크 변수가 늘어납니다. |
| D-10 personal drilldown | config flag | v1 동의, v2는 workspace policy | 통제 없이 개인 drilldown을 열면 token leaderboard 문화로 흐를 위험이 큽니다. |

**기존 추천안이 왜 추천됐는지**


**내 신규 추천안**

1. **D-1은 Hybrid로 바꾸기**  
   `src/geond/cli.py`에는 subparser와 thin handler만 두고, 실제 `start_task`, `finish_task` orchestration은 `src/geond/cli_tasks.py`에 둡니다.  
   이렇게 하면 첫 PR이 조금 커지지만, 장기적으로 테스트와 유지보수가 좋아집니다.

2. **Phase 1 전에 `record-agent-action` CLI를 추가하거나 wrapper 내부 호출로 명시하기**  
   지금 계획은 존재하지 않는 CLI primitive를 전제로 합니다. 이 부분을 명확히 안 하면 첫 구현자가 헤맵니다.

3. **Usage/Evidence linkage용 `session_id` 전략을 먼저 추가하기**  
   `record_changeset`, `record_agent_action`, `record_handoff`가 같은 session 또는 source session external id를 공유할 수 있어야 합니다.  
   선택 안 하면 anti-tokenmaxxing signals가 “같은 기간 같은 workspace” 정도의 약한 추론만 하게 됩니다.

4. **D-6은 SQL view보다 Python rule engine 먼저**  
   `src/geond/storage/signals.py`가 SQL로 rollup을 가져오고 Python에서 threshold와 explanation을 만드는 방식이 낫습니다.  
   선택 안 하면 신호 설명과 config override가 어려워집니다.

5. **dashboard API 경로는 workspace 하위로 맞추기**  
   계획은 `/api/usage/summary`를 제안하지만 기존 dashboard는 `/api/workspaces/{workspace_id}/...` 형태입니다.  
   `/api/workspaces/{workspace_id}/usage/summary`가 더 일관적입니다.

6. **CJK 추정 품질을 data quality에 명시하기**  
   한국어가 많은 workspace에서는 `len//4`가 과소추정될 수 있습니다.  
   선택 안 하면 비용이 실제보다 낮게 보일 수 있습니다.

**실행 순서 추천**

제가 고친다면 순서는 이렇게 갑니다.

1. 문서 계획 보정: DDL 줄바꿈, D-1 Hybrid, session linkage, migration runner 메모 반영
2. `feat: start-task and finish-task wrappers`
3. `feat: record-agent-action cli` 또는 wrapper 내부 storage 호출 확정
4. `feat: llm_usage_events schema and storage`
5. `feat: usage-summary cli`
6. `feat: changeset/action/session linkage`
7. Codex usage extraction
8. Claude usage extraction
9. VS Code Copilot estimated usage
10. pricing registry
11. dashboard read models
12. dashboard UI
13. anti-tokenmaxxing signals

**최종 판단**

이 구현계획은 방향이 좋고, 그대로 첫 PR을 시작할 수 있을 만큼 구체적입니다. 다만 그대로 진행하면 첫 번째로 부딪힐 가능성이 큰 지점은 **없는 CLI primitive**, **migration runner**, **session-to-changeset 연결**, **DDL 세부 오류**입니다.

그래서 저는 “계획 승인”은 하되, 구현 착수 전에 위 4개를 계획 문서에 반영하는 걸 추천합니다. 그렇게 하면 다른 에이전트가 이어받아도 해석이 훨씬 덜 흔들릴 겁니다.