# VS Code 채팅 데이터 저장 구조

> 작성 배경: 로컬 VS Code 워크스페이스에서 `workspaceStorage`가 두 개 생성되어 이전 채팅 기록 복구를 시도하면서 파악한 VS Code/Copilot 내부 저장 구조. 이 문서는 `geond-agent-protocol`의 첫 번째 ingest 테스트베드 기록이다.

> 주의: 아래 구조는 VS Code와 GitHub Copilot Chat의 내부 저장 형식을 관찰한 결과이며, 공식 안정 API가 아니다. 공개 프로젝트에서는 best-effort importer로 다뤄야 한다.

현재 Geond `import-vscode`는 세션/이벤트/메시지를 저장한 뒤 `llm_usage_events`가 있으면 usage도 기록한다. 저장 record 안에 provider usage block이 있으면 그 값을 우선하고, 없으면 `chatSessions` line text를 기준으로 session-level estimate를 남긴다. 재import 시에는 stable `source_record_id`로 같은 usage row를 업데이트한다.

---

## 1. workspaceStorage 위치와 구조

```
%APPDATA%\Code\User\workspaceStorage\{hash}\
```

- `{hash}`는 VS Code가 워크스페이스 경로를 SHA-1 해싱하여 생성
- **같은 폴더를 다른 방법으로 열면 hash가 달라진다** (예: 탐색기 드래그 vs `code .`)
- 워크스페이스당 하나의 스토리지가 생성되어야 하지만, 열기 방식이 달라지면 새 hash가 생겨 두 개가 존재할 수 있음

### 이번 케이스

| 구분 | 예시 Hash | 비고 |
|------|-----------|------|
| 구 스토리지 (OLD) | `{old_workspace_hash}` | 이전 채팅 기록 존재 |
| 신 스토리지 (NEW) | `{new_workspace_hash}` | 현재 VS Code가 사용 중 |

---

## 2. 스토리지 내부 폴더/파일 구조

```
{hash}/
├── state.vscdb                      # SQLite DB - 핵심 상태 저장소
├── chatSessions/                    # VS Code 기본 채팅 저장소 (append-only JSONL)
│   └── {sessionId}.jsonl            # 세션별 대화 내용
├── chatEditingSessions/             # 채팅 중 발생한 파일 편집 내역
│   └── {sessionId}/
│       ├── state.json               # 편집 세션 메타데이터 (파일 경로 + 해시)
│       └── contents/                # 파일별 전체 내용 스냅샷
└── GitHub.copilot-chat/             # Copilot 확장 전용 저장소
    ├── transcripts/                 # Copilot 세션 트랜스크립트
    │   └── {sessionId}.jsonl
    ├── debug-logs/                  # 디버그 로그
    │   └── {sessionId}.jsonl
    ├── chat-session-resources/      # 세션 첨부 리소스
    └── codebase-external.sqlite     # 코드베이스 인덱스 캐시
```

---

## 3. state.vscdb 구조

```sql
CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT);
```

모든 값은 JSON 문자열로 저장됨. 주요 키:

| 키 | 크기(OLD) | 역할 |
|----|-----------|------|
| `chat.ChatSessionStore.index` | 4,930 bytes | **채팅 목록 인덱스** - 사이드바에 표시할 세션 목록 |
| `memento/interactive-session` | 89,274 bytes | 입력창 히스토리, 최근 사용 컨텍스트 |
| `chat.terminalSessions` | 10,592 bytes | 터미널 인라인 채팅 세션 |
| `chat.customModes` | 154,209 bytes | 커스텀 에이전트 모드 정의 |
| `agentSessions.model.cache` | 1,634 bytes | 에이전트 세션 모델 캐시 |
| `terminal.integrated.bufferState` | 824,326 bytes | 터미널 버퍼 상태 |
| `mcpToolCache` | 163,967 bytes | MCP 툴 캐시 |

### chat.ChatSessionStore.index 구조

```json
{
  "version": 1,
  "entries": {
    "{sessionId}": {
      "sessionId": "da185113-fcd7-487d-a397-a6aed46d56fb",
      "title": "Flask application context error",
      "lastMessageDate": 1778448311787,
      "timing": {
        "created": 1778253682457,
        "lastRequestStarted": 1778448311787,
        "lastRequestEnded": 1778448754535
      },
      "initialLocation": "panel",
      "hasPendingEdits": true,
      "isEmpty": false,
      "stats": {
        "fileCount": 92,
        "added": 799,
        "removed": 175
      },
      "isExternal": false
    }
  }
}
```

> **핵심**: 이 키가 사이드바의 채팅 목록을 결정. 없으면 세션 파일이 존재해도 목록에 안 보임.

---

## 4. chatSessions vs transcripts 차이

### chatSessions/{sessionId}.jsonl (VS Code 기본)

- **형식**: `{"kind": <int>, "v": <any>}` (한 줄씩)
- VS Code 자체의 채팅 엔진이 읽고 씀
- kind 값 종류:
  - `0`: 세션 헤더 (version, sessionId, creationDate 등)
  - `1`: 메타데이터/참가자 이름/상태값
  - `2`: 실제 메시지 내용 (tool invocations 포함)
- 세션당 수백~수천 줄, 크기가 수십 MB에 달할 수 있음
- **append-only**: 업데이트 시 기존 내용 유지하고 새 줄 추가

```
라인 0: kind=0, v={version, creationDate, sessionId, hasPendingEdits, ...}
라인 1: kind=1, v="GitHub Copilot"   ← 참가자
라인 2: kind=1, v="Flask application context error"  ← 제목
라인 3~N: kind=2, v=[{tool invocation}, ...]  ← 실제 대화
라인 N+1: kind=1, v=False  ← 상태 플래그
```

### GitHub.copilot-chat/transcripts/{sessionId}.jsonl (Copilot 전용)

- **형식**: `{"type": "<event>", "data": {...}}` (한 줄씩)
- Copilot 확장이 자체적으로 기록하는 트랜스크립트
- type 값 종류:
  - `session.start`: 세션 시작
  - `user.message`: 사용자 입력
  - `assistant.message`: AI 응답
  - `tool.execution_start`: 툴 호출 시작
  - `tool.execution_complete`: 툴 호출 완료
  - `assistant.turn_start`, `assistant.turn_end`: AI 응답 구간

---

## 5. chatEditingSessions 구조

```
chatEditingSessions/{sessionId}/
├── state.json       # 편집 컨텍스트
└── contents/        # 파일 내용 스냅샷 (해시별)
    ├── 3e8b053      # 파일 초기 상태
    ├── b04f880
    └── ...
```

**state.json 구조**:
```json
{
  "version": 2,
  "initialFileContents": [
    ["file:///path/to/workspace/app/services/mock_exam.py", "3e8b053"],
    ["file:///path/to/workspace/fastapi_app/mock_exam.py", "b04f880"]
  ],
  "timeline": [...],
  "recentSnapshot": {...}
}
```

- 파일 경로 + 해당 시점의 git-like 해시를 저장
- `contents/` 폴더의 파일명 = 해시값, 파일 내용 = 실제 파일 전체 텍스트

---

## 6. 이번 복구 과정 전체 기록

### 문제 상황

같은 워크스페이스를 VS Code에서 다른 방법으로 열어 `workspaceStorage`가 두 개 생성됨:

- **OLD** (`{old_workspace_hash}`): 이전 채팅 기록 포함
- **NEW** (`{new_workspace_hash}`): 새로 생성된 현재 활성 스토리지

VS Code는 NEW를 사용하므로 OLD의 채팅 기록이 사이드바에 보이지 않음.

### 시도한 복구 단계

#### 단계 1: 파일 직접 복사 (부분 성공)

```powershell
# transcripts 복사
xcopy /E /Y "...\bdb1383b\GitHub.copilot-chat\transcripts\*" "...\10a78d9c\GitHub.copilot-chat\transcripts\"

# chatSessions 복사
xcopy /E /Y "...\bdb1383b\chatSessions\*" "...\10a78d9c\chatSessions\"

# chatEditingSessions 복사
xcopy /E /Y "...\bdb1383b\chatEditingSessions\*" "...\10a78d9c\chatEditingSessions\"
```

**결과**: 파일은 복사됐지만 목록에 여전히 표시 안 됨.
**이유**: `state.vscdb`의 `chat.ChatSessionStore.index`에 세션이 등록되지 않았음.

#### 단계 2: state.vscdb 인덱스 병합 (성공)

```python
# migrate_chat_offline.py
# VS Code 완전 종료 후 실행!
# OLD → NEW의 chat.ChatSessionStore.index 병합
```

**중요**: VS Code 실행 중 DB를 수정하면 VS Code 종료 시 메모리 내용으로 덮어씀 → **반드시 VS Code 완전 종료 후 수정**.

**결과**: 채팅 목록에 14개 세션이 표시됨 ✅

#### 단계 3: 세션 클릭 시 내용 열기 → 최종 복원 확인 ✅

초기에는 세션 목록에는 보이지만 클릭 시 내용이 열리지 않는 것처럼 보였다. 이후 파일 수정 시각과 append된 마지막 JSONL 레코드를 확인하여, `chatEditingSessions` 복사 이후 세션이 정상 로드되었음을 확인했다.

### 원인 분석 결과 (검증 완료)

| 항목 | OLD | NEW | 상태 |
|------|-----|-----|------|
| `chat.ChatSessionStore.index` da185113 포함 | ✅ | ✅ | 정상 |
| `chatSessions/da185113.jsonl` 존재 | ✅ (752줄) | ✅ (753줄) | **1줄 차이** |
| `GitHub.copilot-chat/transcripts/da185113.jsonl` | ✅ | ✅ | 동일 |
| `chatEditingSessions/da185113/` 존재 | ✅ | ✅ | 복사됨 |
| index 내 `hasPendingEdits` | `true` | `false` (VS Code 갱신) | 정상 |

### 결론: chatEditingSessions가 핵심이었다

**복원 성공 시점**: `2026-05-11 11:30:38` — `NEW/chatSessions/da185113.jsonl` mtime으로 확인

**증거**: NEW 파일의 753번째 마지막 줄 `{"kind":1,"v":""}` 는 오류가 아니라, VS Code가 세션을 **정상 로드한 후 자동 append**한 기록이다.

```
09:02:35  → chatSessions + chatEditingSessions 복사 완료
           → state.vscdb 인덱스 병합 완료 (오프라인)
           → VS Code 재시작 → 세션 목록 표시 ✅
           ↓
11:30:38  → 사이드바에서 da185113 세션 클릭
           → VS Code가 chatSessions + chatEditingSessions 로드 성공
           → 마지막 줄 `{"kind":1,"v":""}` 자동 append (정상 처리 증거)
           → 세션 복원 완료 ✅
```

**실패 → 성공의 분기점**: `hasPendingEdits: true` 상태인 세션은 `chatEditingSessions` 폴더가 없으면 로드를 거부한다. 파일 복사(단계 1)만 했을 때 안 열렸던 이유가 이것이며, chatEditingSessions까지 복사한 후 복원이 완료됐다.

---

## 7. VS Code 채팅 복구 방법 (일반 가이드)

### 세션이 목록에 보이지 않을 때

1. VS Code **완전 종료** (모든 창 닫기)
2. 신 스토리지의 `state.vscdb` 열기
3. `chat.ChatSessionStore.index` 키 수정하여 구 스토리지 항목 추가
4. VS Code 재시작

```python
import sqlite3, json

OLD_DB = r"...\{old_hash}\state.vscdb"
NEW_DB = r"...\{new_hash}\state.vscdb"

old_db = sqlite3.connect(OLD_DB)
new_db = sqlite3.connect(NEW_DB)

old_cur = old_db.cursor()
new_cur = new_db.cursor()

old_cur.execute("SELECT value FROM ItemTable WHERE key='chat.ChatSessionStore.index'")
new_cur.execute("SELECT value FROM ItemTable WHERE key='chat.ChatSessionStore.index'")

old_index = json.loads(old_cur.fetchone()[0])
new_index = json.loads(new_cur.fetchone()[0])

old_entries = old_index.get("entries", {})
new_entries = new_index.get("entries", {})

added = 0
for sid, entry in old_entries.items():
    if sid not in new_entries:
        new_entries[sid] = entry
        added += 1

new_index["entries"] = new_entries
new_cur.execute(
    "INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)",
    ("chat.ChatSessionStore.index", json.dumps(new_index))
)
new_db.commit()
print(f"{added}개 세션 추가됨")
```

### 세션 파일 복사가 필요한 경우

```powershell
$OLD = "...\{old_hash}"
$NEW = "...\{new_hash}"

# 채팅 내용
xcopy /E /Y "$OLD\chatSessions\*" "$NEW\chatSessions\"

# 파일 편집 이력
xcopy /E /Y "$OLD\chatEditingSessions\*" "$NEW\chatEditingSessions\"

# Copilot 트랜스크립트
xcopy /E /Y "$OLD\GitHub.copilot-chat\transcripts\*" "$NEW\GitHub.copilot-chat\transcripts\"
```

---

## 8. 핵심 요약

```
채팅 목록 표시 = state.vscdb의 chat.ChatSessionStore.index
채팅 내용 저장 = chatSessions/{sessionId}.jsonl (append-only, kind/v 형식)
편집 이력 저장 = chatEditingSessions/{sessionId}/ (state.json + contents/)
Copilot 전용   = GitHub.copilot-chat/transcripts/{sessionId}.jsonl (type/data 형식)

⚠️  hasPendingEdits: true 세션은 chatEditingSessions 없이 로드 불가

복구 순서:
1. VS Code 완전 종료
2. chatSessions, chatEditingSessions, transcripts 파일 복사 (모두 필요)
3. state.vscdb에서 chat.ChatSessionStore.index 병합
4. VS Code 재시작 → 목록 표시 확인
5. 세션 클릭 → 로드 성공 시 chatSessions 파일에 마지막 줄 자동 append됨 (정상)
```

---

*마지막 업데이트: 2026-05-11*
