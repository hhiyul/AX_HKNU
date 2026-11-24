# AI 채팅 답변 이식 (1단계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 프로젝트 채팅에 메시지를 보내면 AI 답변이 SSE로 흘러나오고 `messages`에 저장되며, 실행 기록이 `ai_runs`에 남는다.

**Architecture:** 조원 브랜치의 DB 없는 엔진(`responses`, `provider`, `context`, `usage`)을 `backend/app/ai/`로 옮기고, 실행·저장은 우리 MongoDB 서비스 위에 새로 짠다. 진행 중 잠금은 `projects.active_run`, 살아 있음 표시는 `ai_runs.heartbeat_at`, 진행 상황은 프로세스 메모리의 사건 대기열이다.

**Tech Stack:** Python 3.11, FastAPI, pymongo async, httpx(이미 있음), pytest + pytest-asyncio, 임시 `test-mongo`.

**Spec:** `docs/superpowers/specs/2026-11-19-ai-chat-run-design.md`

**출처 코드:** `git show origin/feature/ai-agent-pipeline:<경로>` 로 읽는다. 엔진 파일은 `backend/app/ai/responses.py`, `backend/app/ai/p5/{provider,context,usage}.py`, 테스트는 `backend/tests_unit/test_{responses,p5_provider,p5_context,p5_usage}.py`, 실행 흐름 참고는 `backend/app/ai/p5/server.py`의 `run_task`(247-584행).

## Global Constraints

- 커밋·push는 사용자가 직접 한다. 각 Task 끝의 "커밋" 단계는 커밋 메시지를 제안하고 사용자에게 알리는 것으로 끝낸다.
- 테스트는 임시 `test-mongo`에서만: `docker compose -f docker-compose.test.yml run --rm test-runner sh -c "pip install -q -r requirements-dev.txt && pytest -q"` 후 `docker compose -f docker-compose.test.yml down`. 아래 "Run:"의 `pytest ...`는 이 `sh -c` 안의 명령을 뜻한다.
- `.env` 값을 출력하지 않는다. API 키가 응답·DB·사건 내용에 남으면 안 된다.
- `ai_jobs`(job_type `ck_01`~`ck_06`)는 건드리지 않는다.
- 새 의존성 없음. `requirements*.txt`를 바꾸지 않는다.
- 기본 Provider는 모의 응답(`ai_provider = "demo"`). 테스트는 실제 네트워크를 쓰지 않는다.
- 기본 모델 이름 `gpt-6-luna`. 압축 기준 64,000 토큰, 모델 창 1,050,000, 출력 예약 8,000, 안전 예약 8,000, 최근 AI 16,000, 압축 단계 최대 3, 도구 반복 최대 6 (파이프라인 값 그대로).
- 살아 있음 갱신 15초, 만료 60초. 메모리 사건 보관 10분. 참고 프로젝트 최대 5개.
- 실행 상태: `running`, `completed`, `failed`, `stopped`, `unknown`. `error_code`는 `^[A-Z0-9_]{1,80}$`만, 아니면 `INTERNAL_ERROR`.
- 기존 코드 규칙: 오류는 `AppError(상태, "CODE", "한국어 메시지")`, 응답은 `to_api(...)`, ObjectId는 `parse_object_id`.

## Review Focus

- `ai_provider=openai`인데 키가 비어 있음 → 실행 `failed`/`KEY_MISSING_OR_INVALID`, 잠금 해제, 답변 메시지 없음 (Task 6 테스트 `test_missing_key_fails_and_releases_lock`).
- 실행 중 같은 프로젝트에 두 번째 메시지 → 409 `RUN_ACTIVE`, 끝난 뒤에는 다시 됨 (Task 7 `test_second_run_conflicts_until_first_finishes`).
- 참고 프로젝트가 남의 것·휴지통·삭제됨 → AI 자료에서 빠지고 실행은 정상 (Task 4 `test_references_skip_foreign_trashed_and_missing`).
- 압축 뒤 덮인 메시지가 지워짐 → 다음 실행이 요약을 재사용하지 않음 (Task 4 `test_capsule_dropped_when_covered_message_missing`).
- 끝났거나 메모리에서 사라진 실행의 스트림 요청 → 멈추지 않고 바로 끝남 (Task 7 `test_stream_of_finished_or_expired_run_ends`).

---

## File Structure

| 파일 | 책임 |
| --- | --- |
| `backend/app/ai/__init__.py` | 패키지 표시 |
| `backend/app/ai/responses.py` | `Delivery`, `ProviderError`, `parse_response`, `ResponsesClient` (출처 그대로) |
| `backend/app/ai/provider.py` | `OpenAIProvider`, `DemoProvider`, `make_provider(settings)` |
| `backend/app/ai/context.py` | 지시문, `build_wire`, `prepare`(압축) |
| `backend/app/ai/usage.py` | `valid_usage`, `SummaryText`, `SearchResult`, `UsageTracker` (출처 그대로) |
| `backend/app/ai/events.py` | 실행별 메모리 사건 대기열 `RunEvents` |
| `backend/app/ai/runtime.py` | 프로세스 하나에 하나: provider, 사건 대기열, 실행 중 task |
| `backend/app/services/ai_runs.py` | `ai_runs`·`projects.active_run`·`chat_capsule` 저장 규칙 |
| `backend/app/services/ai_context.py` | 프로젝트·참고 프로젝트 자료, 이전 대화, 읽기 도구 |
| `backend/app/services/ai_chat.py` | 실행 시작·백그라운드 실행·중지 |
| `backend/app/schemas/runs.py` | 요청·응답 모델 |
| `backend/app/routers/runs.py` | `/projects/{id}/runs` API |
| `backend/tests/ai_fakes.py` | 테스트용 가짜 Provider |

수정: `app/config.py`, `app/schemas/projects.py`, `app/services/projects.py`, `app/deps.py`, `main.py`, `init_db.py`, `docker-compose.yml`, `.env.example`, `README.md`.

---

### Task 1: AI 엔진 이식

**Files:**
- Create: `backend/app/ai/__init__.py`, `backend/app/ai/responses.py`, `backend/app/ai/provider.py`, `backend/app/ai/context.py`, `backend/app/ai/usage.py`
- Create: `backend/tests/ai/__init__.py`, `backend/tests/ai/test_responses.py`, `test_provider.py`, `test_context.py`, `test_usage.py`

**Interfaces:**
- Produces:
  - `app.ai.responses`: `Delivery`(`NOT_DISPATCHED`/`UNKNOWN`/`RESPONDED`), `ProviderError(code, delivery)` (속성 `code`, `delivery`, 선택 `usage`), `parse_response(payload) -> GenerationResult`
  - `app.ai.provider`: `OpenAIProvider(key: str, model: str, transport: httpx.AsyncBaseTransport | None = None)` 메서드 `count(wire) -> int`, `summarize(messages) -> SummaryText`, `stream(wire) -> AsyncIterator[dict]`(사건 `delta`/`tool`/`usage`/`completed`), `web_search(query)`; `DemoProvider()` 같은 메서드; `make_provider(settings) -> OpenAIProvider | DemoProvider`
  - `app.ai.context`: 상수 `THRESHOLD`, `MODEL_WINDOW`, `OUTPUT_RESERVE`, `SAFETY_RESERVE`, `RECENT_AI_TARGET`, `PROVENANCE_POLICY`; `ContextError(code)`; `Budget`; `Prepared(wire, count, budget, summary, covered_ids, source_ids)`; `history_summary_text(summary) -> str`; `build_wire(prefix, history, current, tools, model, document_mode=False, tail=None) -> dict`; `async prepare(provider, prefix, history, current, tools, emit, model, budget=None, save_summary=None, document_mode=False, tail=None, observe_count=None) -> Prepared`
  - `app.ai.usage`: 출처와 같음

- [ ] **Step 1: 출처 테스트 4개를 `backend/tests/ai/`로 복사하고 import 경로를 `app.ai.provider`, `app.ai.context`로 바꾼다.** `tests_unit/conftest.py`의 네트워크 차단 fixture는 `backend/tests/ai/conftest.py`로 함께 옮긴다(루프백만 허용).
- [ ] **Step 2: 테스트를 우리 규칙에 맞게 고친다.**
  - `OpenAIProvider("sk-...", transport)` 호출을 `OpenAIProvider("sk-...", "gpt-6-luna", transport)`로.
  - `build_wire(...)`, `prepare(...)` 호출에 `model="gpt-6-luna"`를 키워드로 넣는다.
  - `test_chat_decision_and_ck_state_stay_distinct_in_wire`의 매개변수를 `[("SKIPPED", ""), ("WRITTEN", "색상: 빨간색")]`로, 단언을 아래 지시문 문구로 바꾼다:
    ```python
    assert "does not erase a chat decision" in developer["content"]
    assert "If chat and CK differ" in developer["content"]
    assert "Do not infer SKIPPED from missing or inaccessible" in developer["content"]
    assert "reference project" in developer["content"]
    assert "DRAFT" not in developer["content"] and "DECIDED" not in developer["content"]
    ```
  - 새 테스트 `test_model_name_comes_from_argument`: `build_wire([], [], "x", [], model="m-test")["model"] == "m-test"`, 그리고 `OpenAIProvider(key, "m-test", transport)`의 `stream`이 응답 `model`이 `"m-test"`일 때 `completed`를 내고 `"gpt-6-luna"`일 때 `ProviderError("INVALID_RESPONSE")`.
- [ ] **Step 3: 실행해서 실패 확인**
  Run: `pytest -q tests/ai`
  Expected: `ModuleNotFoundError: No module named 'app.ai'`
- [ ] **Step 4: 엔진 파일을 옮긴다.**
  - `responses.py`, `usage.py`: 출처 그대로.
  - `provider.py`: `OpenAIProvider.__init__(self, key, model, transport=None)`, 코드 안의 `"gpt-6-luna"`를 모두 `self.model`로. `from_env` 삭제. `DemoProvider` 그대로. `make_provider`는 Task 2.
  - `context.py`: `build_wire`·`prepare`에 `model` 인자, `prepare` 안의 AI 토큰 세기 요청도 `model`. `PROVENANCE_POLICY`는 아래 문구로 바꾼다(정해진 문구):
    ```text
    When answering about planning facts, distinguish what the user explicitly decided in this chat, including its earlier conversation summary, from what is actually stored in the current project's CK fields. Cite the source and status of each relevant claim in the answer. A chat decision by itself does not establish a saved CK value, even if called final; only current CK evidence can establish that status. WRITTEN means the CK has a saved value. SKIPPED means that CK has no saved value; it does not erase a chat decision. If chat and CK differ, state both values and their respective status without choosing one as if both agreed. Do not infer SKIPPED from missing or inaccessible CK data; say that the saved state could not be verified. Do not present an AI suggestion as a user decision without explicit acceptance. Never imply that reading, summarizing, chatting, or generating a document saves or changes CK. Sections marked as a reference project belong to a different project: never present them as this project's decisions or saved values, and name the reference project whenever you use its content. Mention CK status when it is relevant to the user's question, not for unrelated conversation.
    ```
- [ ] **Step 5: 실행해서 통과 확인**
  Run: `pytest -q tests/ai`
  Expected: 모두 PASS
- [ ] **Step 6: 커밋 제안** — `feat: port AI provider, context and usage engine from pipeline branch`

---

### Task 2: 설정·저장 규칙·참고 프로젝트 제한·삭제 처리

**Files:**
- Modify: `backend/app/config.py`, `backend/app/ai/provider.py`, `backend/app/schemas/projects.py`, `backend/app/services/projects.py`, `backend/init_db.py`, `docker-compose.yml`, `.env.example`
- Test: `backend/tests/ai/test_provider.py`, `backend/tests/test_projects.py`, `backend/tests/test_workspace.py`

**Interfaces:**
- Produces:
  - `Settings.ai_provider: Literal["demo", "openai"] = "demo"`, `Settings.openai_api_key: str = ""`, `Settings.openai_model: str = "gpt-6-luna"`
  - `app.ai.provider.make_provider(settings: Settings) -> OpenAIProvider | DemoProvider`
  - `app.schemas.projects.MAX_CHIP_REFS = 5`
  - `init_db.COLLECTIONS["ai_runs"]`, `projects` 선택 필드 `active_run`(objectId), `chat_capsule`(object)

- [ ] **Step 1: 실패하는 테스트**
  - `tests/ai/test_provider.py::test_make_provider_defaults_to_demo`: `make_provider(Settings(ai_provider="demo"))`는 `DemoProvider`, `Settings(ai_provider="openai", openai_api_key="sk-x", openai_model="m")`는 `OpenAIProvider`이고 `.model == "m"`.
  - `tests/test_workspace.py::test_chip_refs_limited_to_five`: 내 프로젝트 6개를 만들고 `PATCH`에 6개 → 422 `VALIDATION_ERROR`, 5개 → 200.
  - `tests/test_projects.py::test_permanent_delete_blocked_by_running_ai_run`: 휴지통의 프로젝트에 `ai_runs` 문서(`status: "running"`)를 직접 넣고 완전 삭제 → 409 `PROJECT_IN_USE`; `status: "completed"`로 바꾸면 204이고 `ai_runs` 문서도 사라진다.
  - `tests/test_init_db.py::test_ai_runs_rules`: `ai_runs`에 `status: "queued"` 삽입 → `WriteError`; 필수 필드를 갖춘 `running` 문서는 들어간다.
- [ ] **Step 2: 실행해서 실패 확인** — Run: `pytest -q tests/ai/test_provider.py tests/test_projects.py tests/test_workspace.py tests/test_init_db.py` / Expected: 위 4개 FAIL
- [ ] **Step 3: 구현**
  - `config.py`: 위 세 필드.
  - `provider.py`: `make_provider` — `ai_provider == "openai"`면 `OpenAIProvider(settings.openai_api_key, settings.openai_model)`, 아니면 `DemoProvider()`.
  - `schemas/projects.py`: `ChipRefs = Annotated[list[str], Field(max_length=MAX_CHIP_REFS)]`, `ProjectUpdate.chip_refs: ChipRefs = None`.
  - `services/projects.py`: `CHILD_COLLECTIONS`에 `"ai_runs"`. `delete_project`의 완전 삭제에서 `remove_jobs` 앞에 `ai_runs`에 `{project_id, status: "running"}`이 있으면 `AppError(409, "PROJECT_IN_USE", "프로젝트에 진행 중인 작업이 있습니다.")`.
  - `init_db.py`: `ai_runs` 규칙 — 필수 `user_id`, `project_id`, `status` enum(위 5개), `created_at`, `updated_at`; 선택 `user_message_id`(objectId, 잠금 직후 채움), `assistant_message_id`(objectId), `error_code`, `delivery`(string), `usage`(object), `finished_at`, `heartbeat_at`(date), `stop_requested`(bool). 인덱스 `(project_id, created_at desc)`, `(status, heartbeat_at)`. `projects` 선택 필드 `active_run`(objectId), `chat_capsule`(object).
  - `docker-compose.yml` backend `environment`: `AI_PROVIDER: "${AI_PROVIDER:-demo}"`, `OPENAI_API_KEY: "${OPENAI_API_KEY:-}"`, `OPENAI_MODEL: "${OPENAI_MODEL:-gpt-6-luna}"`. `.env.example`에 같은 세 줄과 한국어 설명(`openai`로 바꾸면 비용 발생).
- [ ] **Step 4: 통과 확인** — Run: `pytest -q` / Expected: 전체 PASS
- [ ] **Step 5: 커밋 제안** — `feat: add ai_runs collection rules, AI settings and chip_refs limit`

---

### Task 3: 실행 기록과 잠금 (`services/ai_runs.py`)

**Files:**
- Create: `backend/app/services/ai_runs.py`
- Test: `backend/tests/test_ai_runs.py`

**Interfaces:**
- Consumes: `store.utcnow`, `store.parse_object_id`
- Produces (모두 `db: AsyncDatabase` 첫 인자):
  - 상수 `HEARTBEAT_SECONDS = 15`, `STALE_SECONDS = 60`
  - `async begin_run(db, user_id, project_id) -> ObjectId` — `user_message_id` 없이 `running` 문서를 만든다(사용자 메시지는 잠금을 잡은 뒤 저장하므로 Task 2 규칙에서 선택 필드). 잠금 실패 시 `reap_stale(db, project_id=...)` 후 한 번 더 시도, 그래도 실패면 `AppError(409, "RUN_ACTIVE", "이미 답변을 만들고 있습니다.")`. 사용자 메시지는 잠금 뒤 저장해야 하므로 `user_message_id`는 `attach_user_message`로 나중에 넣는다.
  - `async attach_user_message(db, run_id, message_id) -> None`
  - `async heartbeat(db, run_id) -> None`
  - `async check(db, run_id, project_id) -> RunCheck` — `RunCheck` enum: `OK`, `STOP_REQUESTED`, `PROJECT_NOT_ACTIVE`, `LOST`(실행이 `running`이 아니거나 `active_run`이 다름)
  - `async request_stop(db, run_id) -> None` — `running`일 때만 `stop_requested: True`
  - `async finish_run(db, run_id, project_id, status, error_code=None, delivery=None, usage=None, assistant_message_id=None) -> None` — `status == "running"`인 문서만 바꾸고, `active_run == run_id`일 때만 비운다
  - `async reap_stale(db, project_id: ObjectId | None = None) -> int` — `heartbeat_at`(없으면 `created_at`)이 60초 넘은 `running` → `unknown`/`PROCESS_RESTART`/`delivery: "unknown"`, 해당 프로젝트 `active_run`이 그 실행이면 비움
  - `async load_capsule(db, project_id) -> dict | None`, `async save_capsule(db, project_id, run_id, expected_revision: int, text: str, covered_ids: list[str]) -> int` — 실패 시 `ContextError("CAPSULE_CONFLICT")`, 성공 시 새 revision
  - `async get_run(db, user_id, project_id, run_id) -> dict` — 없으면 `not_found()`

- [ ] **Step 1: 실패하는 테스트** (프로젝트는 API로 만들고 함수는 직접 부른다)
  - `test_begin_run_locks_project`: 첫 `begin_run` 성공 → 프로젝트 `active_run`이 그 id, 두 번째 → 409 `RUN_ACTIVE`; 프로젝트 `revision`은 그대로.
  - `test_finish_run_releases_lock_and_is_idempotent`: `finish_run(... "completed")` 뒤 `active_run is None`, 다시 `finish_run(... "failed")` 해도 상태는 `completed`.
  - `test_check_states`: 정상 `OK`; `request_stop` 뒤 `STOP_REQUESTED`; 프로젝트 `deleted_at` 설정 뒤 `PROJECT_NOT_ACTIVE`; `finish_run` 뒤 `LOST`.
  - `test_reap_stale_only_touches_dead_runs`: `heartbeat_at`을 61초 전으로 바꾼 실행은 `unknown`/`PROCESS_RESTART`이고 잠금 해제, 10초 전인 실행은 그대로 `running`.
  - `test_begin_run_reaps_dead_lock`: 죽은 실행이 잠금을 쥔 프로젝트에 `begin_run`이 성공.
  - `test_capsule_conditional_save`: revision 0에서 저장 → 1; 다시 0으로 저장 → `CAPSULE_CONFLICT`; `active_run`이 다른 실행이면 `CAPSULE_CONFLICT`; 프로젝트 `revision`은 그대로.
- [ ] **Step 2: 실패 확인** — Run: `pytest -q tests/test_ai_runs.py` / Expected: `ModuleNotFoundError`
- [ ] **Step 3: 구현.** 잠금은 `projects.find_one_and_update({"_id": pid, "active_run": None, "deleted_at": None, "archived_at": None}, {"$set": {"active_run": rid}})` (필드가 없는 예전 문서도 `None` 조건에 맞는다). `chat_capsule`은 `{"text", "covered_ids", "revision"}`.
- [ ] **Step 4: 통과 확인** — Run: `pytest -q tests/test_ai_runs.py` / Expected: PASS
- [ ] **Step 5: 커밋 제안** — `feat: add ai_runs lock, heartbeat and capsule storage`

---

### Task 4: AI에게 줄 자료 (`services/ai_context.py`)

**Files:**
- Create: `backend/app/services/ai_context.py`
- Test: `backend/tests/test_ai_context.py`

**Interfaces:**
- Consumes: `overview.get_projection(db, project, wanted)`, `overview.SECTIONS`, `schemas.projects.MAX_CHIP_REFS`, `ai_runs.load_capsule`, `context.history_summary_text`
- Produces:
  - `READ_TOOL: dict` — 출처 `READ_TOOL`과 같은 구조, 설명은 "Read sections of this project or its reference projects already supplied in context. Keys: project_basic, linked_brand, ck_01..ck_06, or ref:<project_id>:<section> for a reference project."
  - `async build_sections(db, project: dict, user_id: ObjectId) -> dict[str, dict]` — 키는 자기 섹션 이름 그대로, 참고 프로젝트는 `f"ref:{id}:{section}"`. 값은 projection 섹션에 `"source": "this_project"` 또는 `"source": "reference_project", "project_name": ...`를 더한 것.
  - `def prefix_from(sections: dict[str, dict]) -> list[dict]` — `[{"key": k, **v} for k, v in sections.items()]`
  - `def read_sections(sections, keys: list[str]) -> list[dict]` — 있는 키만, 요청 순서대로
  - `async load_history(db, project_id, exclude_message_id: ObjectId) -> tuple[list[dict], int]` — 항목 `{"id": str, "role": "user"|"assistant", "content": str}`, 두 번째 값은 지금 capsule revision(없으면 0)

- [ ] **Step 1: 실패하는 테스트**
  - `test_sections_cover_this_project`: 키가 `["project_basic", "linked_brand", "ck_01", ..., "ck_06"]`, `ck_01["state"] == "SKIPPED"`, 모두 `source == "this_project"`.
  - `test_references_included_with_prefixed_keys`: 참고 프로젝트 하나를 걸고 그 프로젝트 `ck_02`에 "선물 고객" 저장 → `sections[f"ref:{rid}:ck_02"]["content"] == "선물 고객"`, `source == "reference_project"`, `project_name` 일치.
  - `test_references_skip_foreign_trashed_and_missing`: `chip_refs`에 남의 프로젝트 id, 휴지통 프로젝트 id, 없는 id를 DB로 직접 넣는다 → 어떤 `ref:` 키도 생기지 않고 예외 없음.
  - `test_references_capped_at_five`: DB로 7개를 직접 넣으면 앞의 5개 프로젝트만 `ref:` 키로 나온다.
  - `test_read_sections_returns_only_known_keys`: `read_sections(s, ["ck_01", "nope", "ref:x:ck_01"])` → `ck_01` 하나.
  - `test_history_marks_partial_answers_and_excludes_current`: USER "a", ASSISTANT(`completion="PARTIAL"`) "b", USER "c"(현재) → `[("user","a"), ("assistant","(중간에 끊긴 답변) b")]`.
  - `test_history_uses_capsule_when_all_covered_present`: capsule이 메시지 a,b를 덮음 → 첫 항목 `{"id": "capsule", "role": "user", "content": history_summary_text(text), "covered_ids": [...]}`, 그 뒤 덮이지 않은 메시지.
  - `test_capsule_dropped_when_covered_message_missing`: 덮인 메시지 하나를 지우면 capsule 없이 남은 메시지 전체.
- [ ] **Step 2: 실패 확인** — Run: `pytest -q tests/test_ai_context.py` / Expected: `ModuleNotFoundError`
- [ ] **Step 3: 구현.** 참고 프로젝트는 `project["chip_refs"][:MAX_CHIP_REFS]`의 id를 `{"_id": {"$in": ids}, "user_id": user_id, "deleted_at": None, "archived_at": None}`로 한 번에 찾고 `chip_refs` 순서를 유지한다. 메시지는 `db.messages.find({"project_id": pid}).sort("ordinal", 1)`.
- [ ] **Step 4: 통과 확인** — Run: `pytest -q tests/test_ai_context.py` / Expected: PASS
- [ ] **Step 5: 커밋 제안** — `feat: build AI context from project, reference projects and chat history`

---

### Task 5: 메모리 사건 대기열과 런타임 (`ai/events.py`, `ai/runtime.py`)

**Files:**
- Create: `backend/app/ai/events.py`, `backend/app/ai/runtime.py`
- Test: `backend/tests/ai/test_events.py`

**Interfaces:**
- Produces:
  - `RunEvents(keep_seconds: float = 600)`: `publish(run_id: str, kind: str, content: str) -> int`(seq 1부터), `close(run_id: str) -> None`, `async subscribe(run_id: str, after: int = 0) -> AsyncIterator[dict]` — `{"seq", "kind", "content"}`를 내고, 닫힌 뒤 남은 것을 다 내면 끝. 모르는 run_id면 바로 끝. `close` 후 `keep_seconds`가 지나면 그 실행의 사건을 버린다(다음 `publish`/`subscribe` 때 정리).
  - `Runtime` 데이터클래스: `provider`, `events: RunEvents`, `tasks: dict[str, asyncio.Task]`, `model: str`; `get_runtime() -> Runtime`(처음 부를 때 `make_provider(get_settings())`로 만든 싱글턴), `set_runtime(runtime: Runtime | None) -> None`(테스트용)

- [ ] **Step 1: 실패하는 테스트**
  - `test_subscribe_replays_after_seq_and_ends_on_close`: 사건 3개 publish, `subscribe(after=1)`을 돌리는 중 하나 더 publish하고 close → seq `[2, 3, 4]` 받고 끝.
  - `test_unknown_run_ends_immediately`: 1초 안에 빈 목록으로 끝.
  - `test_closed_runs_expire`: `RunEvents(keep_seconds=0)`에서 close 후 다른 실행 publish → 처음 실행 `subscribe`가 바로 빈 목록.
- [ ] **Step 2: 실패 확인** — Run: `pytest -q tests/ai/test_events.py` / Expected: `ModuleNotFoundError`
- [ ] **Step 3: 구현.** 실행마다 `list` + `asyncio.Condition`. `subscribe`는 조건 대기에 1초 타임아웃을 둬서 놓친 알림이 있어도 멈추지 않게 한다.
- [ ] **Step 4: 통과 확인** — Run: `pytest -q tests/ai/test_events.py` / Expected: PASS
- [ ] **Step 5: 커밋 제안** — `feat: add in-memory run event queue and AI runtime`

---

### Task 6: 실행 흐름 (`services/ai_chat.py`)

**Files:**
- Create: `backend/app/services/ai_chat.py`, `backend/tests/ai_fakes.py`
- Test: `backend/tests/test_ai_chat.py`

**Interfaces:**
- Consumes: Task 1~5 전부, `chat.add_message`
- Produces:
  - `async start_run(db, runtime: Runtime, user_id, project: dict, text: str) -> ObjectId` — `begin_run` → `add_message(USER)` → `attach_user_message` → `runtime.tasks[str(rid)] = asyncio.create_task(run_task(...))`. 메시지 저장이 실패하면 `finish_run(..., "failed", "MESSAGE_SAVE_FAILED")` 후 다시 던진다.
  - `async run_task(db, runtime, user_id, project_id, run_id, user_message_id, text) -> None` — 아래 순서. 함수 하나가 50줄을 넘지 않게 `_prepare_context`, `_generate`(스트림+도구 반복), `_handle_tool_calls`, `_finish` 같은 내부 함수로 나눈다(이름은 자유).
  - `async stop_run(db, runtime, user_id, project_id, run_id) -> str` — 실행이 없으면 404; 이 프로세스에 task가 있으면 `cancel()` 후 `"stop_requested"`; 없으면 `request_stop` 후 `"stop_requested"`; 이미 끝났으면 그 상태 문자열.
  - `tests/ai_fakes.py`의 `FakeProvider(turns: list[list[dict]], count: int | Callable[[dict], int] = 100, summary: str = "요약", gate: asyncio.Event | None = None, error: ProviderError | None = None)` — `stream`은 호출마다 `turns`의 다음 사건 목록을 내고, `gate`가 있으면 첫 `delta` 뒤 `gate.wait()`. `calls`에 받은 wire를 쌓는다.

  `run_task` 동작(출처 `run_task`를 기준으로, 검색·문서·첨부·`propose_record`·`route_document`는 빼고):
  1. 사건 `started`. 15초마다 `heartbeat`하는 보조 task 시작(끝날 때 취소).
  2. `build_sections`, `load_history(exclude=user_message_id)`, `prepare(..., tools=[READ_TOOL], model=runtime.model, save_summary=capsule 저장, observe_count=context_usage 사건)`.
  3. 최대 6번: `check`가 `OK`가 아니면 멈춤 → stream → `delta`면 조각 모으고 사건 `delta` → `tool`이면 모으기 → `usage` 사건 → `completed`. 스트리밍 중 `check`는 1초에 한 번까지. `completed`가 없으면 `PROVIDER_OUTCOME_UNKNOWN`. 도구 호출이 없으면 끝. `read_project_sections`가 아니거나 `call_id`가 없으면 `TOOL_NOT_ALLOWED`, 인자가 `{"keys": [str...]}`가 아니면 `INVALID_TOOL_ARGUMENTS`. 결과를 `function_call`/`function_call_output`으로 tail에 붙이고 다시 `prepare`. 6번을 다 쓰면 `TOOL_BUDGET_EXHAUSTED`.
  4. 저장 직전 `check`: `OK`면 `add_message(ASSISTANT, completion="COMPLETE")` → `completed`. `PROJECT_NOT_ACTIVE`면 저장 없이 `failed`/`PROJECT_NOT_ACTIVE`. `STOP_REQUESTED`면 부분 저장(`PARTIAL`) → `stopped`. `LOST`면 아무것도 저장·변경하지 않고 끝.
  5. 끝나는 경우는 설계 문서 "끝나는 경우" 표 그대로. `asyncio.CancelledError` → `stopped`(부분 저장 `PARTIAL`). 원격 결과 모름 → `unknown`(부분 저장 `UNKNOWN`, 사건 `outcome_unknown`). 그 밖의 `ContextError`/`ProviderError` → `failed`(부분 있으면 `PARTIAL`). 예상 못한 예외 → `failed`/`INTERNAL_ERROR`. 실패 사건 문구는 출처 `reason` 사전 그대로.
  6. `finally`: `finish_run(..., usage=tracker.parts, assistant_message_id=...)`, `runtime.events.close`, `runtime.tasks.pop`.

- [ ] **Step 1: 실패하는 테스트** (`runtime`은 `Runtime(FakeProvider(...), RunEvents(), {}, "gpt-6-luna")`, 실행 끝은 `await runtime.tasks[str(rid)]`로 기다린다)
  - `test_answer_is_streamed_and_saved`: turns `[[delta "안녕", delta "하세요", completed]]` → 메시지 `[USER 질문, ASSISTANT "안녕하세요" COMPLETE]`, 실행 `completed`, `assistant_message_id` 일치, 사건 종류에 `started`, `delta`, `completed`, `active_run is None`.
  - `test_read_tool_returns_plan_and_reference_sections`: 첫 turn이 `tool read_project_sections {"keys": ["ck_01", "ref:<rid>:ck_02"]}`, 두 번째 turn 답변 → 두 번째 wire 마지막 `function_call_output`에 두 섹션 내용이 있고 6개 항목 DB는 그대로.
  - `test_unknown_tool_fails`: `tool route_document` → `failed`/`TOOL_NOT_ALLOWED`, 잠금 해제.
  - `test_missing_key_fails_and_releases_lock`: `OpenAIProvider("", "gpt-6-luna", MockTransport)` → `failed`/`KEY_MISSING_OR_INVALID`, `delivery == "not_dispatched"`, ASSISTANT 메시지 없음, `active_run is None`.
  - `test_stop_keeps_partial_answer`: `gate` 사용, 첫 delta 뒤 `stop_run` → `stopped`, ASSISTANT 메시지 `completion == "PARTIAL"`.
  - `test_stop_request_from_other_process`: task가 없는 상태를 흉내 내려고 `runtime.tasks`에서 빼 둔 뒤 `stop_run` → `stop_requested: True`; gate를 열면 실행이 `stopped`로 끝난다.
  - `test_unknown_outcome_marks_unknown`: stream이 `ProviderError("TRANSPORT_ERROR", Delivery.UNKNOWN)` → `unknown`, 사건 `outcome_unknown`.
  - `test_trashed_during_run_discards_answer`: gate에서 멈춘 동안 프로젝트를 휴지통으로 → 끝나면 `failed`/`PROJECT_NOT_ACTIVE`, ASSISTANT 메시지 없음.
  - `test_compression_saves_capsule`: `count=lambda wire: 1_000 if "Earlier conversation summary" in json.dumps(wire, ensure_ascii=False) else 70_000`, 이전 메시지 4개 → `projects.chat_capsule.revision == 1`, 사건 `compression_finished`.
  - `test_usage_saved_on_run`: turn에 `usage {input 10, output 5, total 15}` → `ai_runs.usage["generation"]["total_tokens"] == 15`.
  - `test_api_key_never_stored`: OpenAIProvider("sk-secret-xyz", ...)가 응답 delta에 키를 섞어 보냄 → 저장된 답변·사건·`ai_runs` 어디에도 `"sk-secret-xyz"` 없음.
- [ ] **Step 2: 실패 확인** — Run: `pytest -q tests/test_ai_chat.py` / Expected: `ModuleNotFoundError`
- [ ] **Step 3: 구현** — 위 Interfaces 그대로.
- [ ] **Step 4: 통과 확인** — Run: `pytest -q tests/test_ai_chat.py` / Expected: PASS
- [ ] **Step 5: 커밋 제안** — `feat: run AI chat answers with tools, compression and stop`

---

### Task 7: API·앱 연결·문서

**Files:**
- Create: `backend/app/schemas/runs.py`, `backend/app/routers/runs.py`
- Modify: `backend/app/deps.py`, `backend/main.py`, `README.md`, `front/src/App.jsx`
- Test: `backend/tests/test_runs_api.py`

**Interfaces:**
- Consumes: Task 3·5·6
- Produces:
  - `deps.owned_any_project(project_id, user, db) -> dict` — 소유만 확인(보관·휴지통 허용)
  - `schemas.runs.RunCreate(RequestModel)`: `text: Annotated[str, StringConstraints(min_length=1, max_length=20000)]`; `RunStarted`: `run_id: str`; `RunOut`: `id, project_id, status, error_code: str|None, delivery: str|None, usage: dict|None, user_message_id: str|None, assistant_message_id: str|None, created_at, finished_at: datetime|None`; `StopOut`: `status: str`
  - 라우터 `prefix="/projects/{project_id}/runs"`, tag `ai`:
    - `POST ""` → 202 `RunStarted` (`Workspace` 의존성, `get_runtime()`)
    - `GET "/{run_id}"` → `RunOut` (`owned_any_project`)
    - `GET "/{run_id}/stream?after=0"` → `text/event-stream`, 사건마다 `id: {seq}\ndata: {json}\n\n` (`owned_any_project`, 실행이 DB에 없으면 404)
    - `POST "/{run_id}/stop"` → `StopOut` (`owned_any_project`)
  - `main.py`: 라우터 등록. `lifespan` 시작 때 `mongo_details`가 있으면 `ai_runs.reap_stale(get_db())`.

- [ ] **Step 1: 실패하는 테스트** (fixture가 `set_runtime(Runtime(FakeProvider(...), RunEvents(), {}, "gpt-6-luna"))`, 끝나면 `set_runtime(None)`)
  - `test_run_round_trip`: `POST /runs` 202 → task 대기 → `GET /runs/{id}` `completed` → `GET /messages`에 답변.
  - `test_second_run_conflicts_until_first_finishes`: gate로 멈춘 동안 두 번째 `POST` → 409 `RUN_ACTIVE`; gate 열고 끝난 뒤 다시 202.
  - `test_stream_returns_events_in_order`: 끝난 직후 `GET /stream` 본문에 `"kind": "started"`, `"kind": "delta"`, `"kind": "completed"`가 순서대로, `?after=<첫 seq>`면 첫 사건이 빠진다.
  - `test_stream_of_finished_or_expired_run_ends`: `RunEvents(keep_seconds=0)` 런타임에서 끝난 실행의 스트림이 2초 안에 빈 본문으로 끝난다.
  - `test_run_endpoints_check_owner`: 다른 사용자로 로그인하면 `POST`/`GET`/`stream`/`stop` 모두 404.
  - `test_trashed_project_can_still_be_stopped`: 실행 중 휴지통 이동 → `GET /runs/{id}` 200, `POST /stop` 200; 새 `POST /runs`는 409 `PROJECT_NOT_ACTIVE`.
  - `test_empty_text_rejected`: `{"text": ""}` → 422.
- [ ] **Step 2: 실패 확인** — Run: `pytest -q tests/test_runs_api.py` / Expected: 404 (라우터 없음)
- [ ] **Step 3: 구현** — 위 Interfaces 그대로.
- [ ] **Step 4: 문서와 테스트 페이지**
  - `README.md`: API 표에 `/runs` 네 개, 컬렉션 목록에 `ai_runs`, 설정에 `AI_PROVIDER`/`OPENAI_API_KEY`/`OPENAI_MODEL`, "아직 없는 것"의 AI 항목을 "1단계(채팅 답변) 완료, 검색·문서 초안·첨부는 다음 단계"로.
  - `front/src/App.jsx`: AI 채팅 묶음에 `['AI 답변 요청', 'POST', '/projects/{project_id}/runs', { text: '이 상품의 고객을 정리해 줘' }]`, `['실행 상태', 'GET', '/projects/{project_id}/runs/{run_id}']` 형태로 두 버튼(기존 버튼 배열 형식을 따른다. `{run_id}` 치환이 없으면 입력칸을 하나 추가). 스트림은 테스트 페이지에서 다루지 않는다.
- [ ] **Step 5: 전체 확인**
  - Run: `pytest -q` / Expected: 전체 PASS (기존 121개 + 새 테스트)
  - Run (`front/`): `npx vite build` / Expected: `built in`
- [ ] **Step 6: 커밋 제안** — `feat: expose AI chat run API with SSE stream`
