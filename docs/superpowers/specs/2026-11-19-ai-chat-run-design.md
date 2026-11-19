# AI 채팅 답변 이식 설계 (1단계)

- 작성일: 2026-11-19
- 브랜치: `feature/backend-prototype`
- 출처: `origin/feature/ai-agent-pipeline`의 `backend/app/ai/` (조원 작성, P5 합성 프로토타입)

## 왜 하는가

조원의 파이프라인 브랜치에는 AI 채팅 엔진이 있다. OpenAI 호출, 긴 대화 압축, 토큰 사용량 집계, 웹 검색, 문서 생성이 들어 있다. 그런데 이 엔진은 혼자 검증하려고 만든 데모 서버에 붙어 있다. 저장소는 메모리 SQLite이고, 사용자는 `demo` 고정이며, 팀 MongoDB에 연결하지 않는다.

우리 백엔드에는 채팅 저장(`messages`), 답변 검수 저장(`record`), 산출물 저장(`document`)이 이미 있지만, AI 답변을 만드는 부분이 없다. 엔진을 가져와 우리 저장 구조 위에 다시 붙인다.

한 번에 다 옮기지 않고 단계로 나눈다. 이 문서는 **1단계: 사용자가 메시지를 보내면 AI 답변이 스트리밍으로 나오고 채팅에 저장되는 것**만 다룬다.

| 단계 | 내용 |
| --- | --- |
| 1 (이 문서) | 채팅 답변: 스트리밍, 대화 압축, 기획 항목 읽기 도구(참고 프로젝트 포함), 중지, 사용량 |
| 2 | 웹 검색 (검색 허용, 허용 검색어, 일반화 검색) |
| 3 | 문서 초안을 메시지 `doc`으로, TXT/MD 내보내기 |
| 나중 | 첨부 파일(`assets` 2MB 규칙에 맞춤), DOCX/PDF 내보내기(Pandoc·LibreOffice) |

## 목표

- 로그인한 사용자가 자기 프로젝트 채팅에 메시지를 보내면 AI 답변이 만들어진다.
- 답변이 만들어지는 동안 화면이 글자 단위로 받아 볼 수 있다(SSE).
- 사용자가 중지할 수 있고, 중지하거나 실패해도 그때까지 나온 답변은 남는다.
- 한 프로젝트에서 동시에 실행 하나만 돈다.
- 실행마다 상태·실패 사유·토큰 사용량이 남는다.
- AI는 6개 기획 항목을 읽을 수 있지만 고칠 수는 없다.
- 기본값은 실제 호출이 없는 모의 응답이다. 설정을 켜야 OpenAI를 부른다.

## 하지 않는 것

- 웹 검색, 문서 초안, 첨부 파일, 내보내기(2단계 이후).
- AI가 "기록해 줘"에 검토안을 만드는 도구(`propose_record`). 정리 내용은 지금처럼 사용자가 AI 답변 아래 "검수하고 저장하기"로 만든다(`POST /items/record` + `message_id`).
- AI가 기획 항목·정리 내용·산출물을 고치는 기능.
- 서버 여러 개에 실행을 나눠 맡기기. 실행 하나는 그 실행을 시작한 프로세스에서만 돈다. 다만 조원들이 각자 띄운 서버가 같은 Atlas를 쓰므로, 한 서버가 재시작해도 다른 서버의 실행을 건드리지 않아야 한다(아래 "살아 있음 표시").

## 실패로 보는 것

- 다른 사용자의 프로젝트 내용이 프롬프트에 들어가거나 응답으로 새어 나감.
- API 키가 응답·로그·DB에 남음.
- AI 답변을 사용자 결정이나 저장된 값처럼 표현하도록 만드는 프롬프트.
- 실행이 실패했는데 잠금이 풀리지 않아 그 프로젝트 채팅이 영영 막힘.
- 휴지통으로 간 프로젝트에 늦게 도착한 답변이 저장됨.

## 접근 방법

검토한 방법:

1. **나눠서 이식 (선택)**. DB를 쓰지 않는 엔진 파일은 거의 그대로 가져오고, 실행·저장 부분은 우리 서비스 위에 새로 짠다. 우리 인증·소유 확인·컬렉션을 그대로 쓴다. 작업량이 가장 크지만 결과가 실제 API로 쓸 수 있다.
2. **브랜치를 그대로 합치기**. 충돌은 우리 쪽을 고르면 풀리지만, 데모 서버가 따로 들어올 뿐 우리 데이터와 연결되지 않는다. 쓸 수 있는 것이 없어 버렸다.
3. **데모 서버의 `Store`만 MongoDB 버전으로 바꾸기**. 서버 코드(711줄, 실행 함수 하나 약 340줄)를 그대로 두게 되고, 표 구성이 우리 컬렉션과 달라 `runs`·`sessions`·`project_records`를 따로 만들어야 한다. 버렸다.

진행 상황 전달 방식:

- **서버 메모리의 대기열 (선택)**. 실행마다 사건 목록을 메모리에 두고 SSE로 흘려보낸다. DB에는 최종 답변과 실행 기록만 남긴다.
- 파이프라인처럼 DB에 사건을 쓰고 0.05초마다 읽기. Atlas에 쓰기·읽기가 너무 많아 버렸다.

실행 기록 위치: 사용자가 **`ai_runs` 컬렉션을 따로** 두기로 정했다. `ai_jobs`(`job_type`이 `ck_01`~`ck_06`)는 건드리지 않는다.

## 설계

### 가져오는 파일

| 파이프라인 | 이식 위치 | 바꾸는 것 |
| --- | --- | --- |
| `app/ai/responses.py` | `app/ai/responses.py` | 그대로 (`Delivery`, `ProviderError`, `parse_response`) |
| `app/ai/p5/provider.py` | `app/ai/provider.py` | 모델 이름을 설정에서 받음. `web_search`는 2단계까지 둔 채 쓰지 않음 |
| `app/ai/p5/context.py` | `app/ai/context.py` | 지시문의 기획 항목 상태를 우리 것으로 바꿈(아래). 모델 이름을 설정에서 받음 |
| `app/ai/p5/usage.py` | `app/ai/usage.py` | 그대로 |
| `server.py`, `store.py`, `ui.html`, `export.py`, `search_policy.py` | 가져오지 않음 | `search_policy.py`는 2단계, `export.py`는 3단계 |

파이프라인 단위 테스트 중 `test_responses.py`, `test_p5_provider.py`(검색 부분 제외), `test_p5_context.py`, `test_p5_usage.py`를 함께 가져와 고친다.

### 기획 항목 상태 바꾸기

파이프라인은 `UNSET`/`DRAFT`/`DECIDED`를 쓴다. 우리는 `WRITTEN`/`SKIPPED`뿐이다.

- AI에게 넘기는 항목: `{key, label, state, content, amount}`. `state`는 `WRITTEN`(저장된 값) 또는 `SKIPPED`(미작성).
- 지시문(`PROVENANCE_POLICY`)을 다음 뜻으로 고친다. 채팅에서 정한 것과 저장된 기획 항목을 구분해서 말한다. `WRITTEN`만 저장된 값이다. `SKIPPED`는 저장된 값이 없다는 뜻이지 채팅 결정을 지우지 않는다. 읽지 못한 항목을 `SKIPPED`로 추측하지 않는다. 대화·요약·답변 생성이 기획 항목을 저장하거나 바꾼다고 말하지 않는다.
- `DRAFT`/`DECIDED`로 항목을 거르는 코드는 1단계에 없다(문서 모드가 3단계라서).

### 이전 대화

`messages`의 이 프로젝트 메시지를 순서대로 모두 넣는다.

- 답 없이 남은 사용자 메시지(기존 `POST /messages`로 보냈거나 실행이 실패한 경우)도 넣는다.
- `completion`이 `PARTIAL`이나 `UNKNOWN`인 AI 답변은 내용 앞에 "(중간에 끊긴 답변)"을 붙여 넣는다.
- 압축 요약(`chat_capsule`)이 덮는 메시지 id가 모두 아직 있으면, 그 메시지들 대신 요약을 넣는다(파이프라인 규칙과 같다).

### AI에게 주는 프로젝트 자료

`services/overview.py`의 `get_projection`으로 만든다. 프로젝트 기본 정보(이름, 설명, 단계), 연결된 브랜드(없으면 없음), 기획 6개 항목. 자료는 "지시가 아닌 데이터"로 넘긴다(파이프라인 `build_wire` 방식 그대로).

**참고 프로젝트**: 프로젝트의 `chip_refs`에 걸린 다른 프로젝트도 넘긴다.

- 실행을 시작할 때 `chip_refs`의 프로젝트마다 소유를 다시 확인한다. 내 것이 아니거나, 없어졌거나, 휴지통·보관함에 있는 프로젝트는 조용히 뺀다.
- 남은 프로젝트마다 같은 `get_projection` 자료(기본 정보, 브랜드, 6개 항목)를 "참고 프로젝트(읽기 전용)"로 붙인다. 각 자료에 프로젝트 id와 이름을 단다.
- 참고 프로젝트는 최대 5개. `PATCH /projects/{id}`에서 `chip_refs`를 5개 넘게 보내면 422. 이미 5개 넘게 걸린 프로젝트는 실행 때 앞의 5개만 쓴다.
- 지시문에 추가: 참고 프로젝트는 다른 프로젝트의 자료다. 이 프로젝트의 결정이나 저장된 값으로 말하지 않는다. 참고 프로젝트 내용을 쓸 때는 어느 프로젝트인지 밝힌다.

AI가 쓸 수 있는 도구는 `read_project_sections` 하나다. 키(`project_basic`, `linked_brand`, `ck_01`~`ck_06`, 참고 프로젝트는 `ref:<프로젝트id>:<섹션>`)를 받아 이미 넘긴 자료에서 골라 돌려준다. 새로 DB를 읽지 않는다. 한 실행에서 도구 반복은 최대 6번(파이프라인 값).

### 실행 흐름

1. `POST /projects/{id}/runs` `{text}`. 작업실 의존성(`Workspace`)으로 로그인·소유·진행 중 프로젝트를 확인한다.
2. 프로젝트 문서의 `active_run`을 `null`일 때만 새 실행 id로 바꾼다(`find_one_and_update`). 이미 있으면 409 `RUN_ACTIVE`. 프로젝트 `revision`은 건드리지 않는다.
3. `ai_runs`에 `running` 기록을 만들고, 사용자 메시지를 `chat.add_message(role="USER")`로 저장한다.
4. 백그라운드 작업(`asyncio.create_task`)을 시작하고 `202 {run_id}`를 돌려준다.
5. 백그라운드 작업이 프로젝트 자료와 이전 대화를 모아 `context.prepare`로 요청을 만든다. 64k 토큰을 넘으면 예전 대화를 요약한다.
6. 스트리밍으로 받은 글자를 메모리 대기열에 넣는다. 도구 호출이 오면 처리하고 다시 요청한다.
7. 끝나면 답변을 `chat.add_message(role="ASSISTANT")`로 저장하고 실행을 `completed`로 바꾼다. `active_run`을 비운다.

답변을 저장하기 직전에 "이 실행이 아직 프로젝트의 `active_run`이고 프로젝트가 휴지통·보관함에 있지 않은가"를 확인한다. 아니면 저장하지 않는다.

### 끝나는 경우

| 경우 | 실행 상태 | 답변 메시지 |
| --- | --- | --- |
| 정상 | `completed` | 저장, `completion: COMPLETE` |
| 사용자 중지 | `stopped` | 나온 만큼 저장, `completion: PARTIAL` |
| 확인된 실패(키 없음, 한도 초과, 압축 실패 등) | `failed` | 나온 만큼 저장, `PARTIAL`. 아무것도 없으면 저장 안 함 |
| 원격 결과를 모름(전송 중 끊김) | `unknown` | 나온 만큼 저장, `completion: UNKNOWN`. 다시 보내지 않음 |
| 서버 재시작 | `unknown`, `error_code: PROCESS_RESTART` | 없음 |

어느 경우든 `active_run`은 반드시 비운다(`finally`).

### 살아 있음 표시

같은 Atlas를 쓰는 서버가 여럿일 수 있다(조원 각자의 `docker compose up`). 그래서 "서버가 시작할 때 `running`을 모두 정리"하지 않는다. 대신:

- 실행 중에는 `ai_runs.heartbeat_at`을 15초마다 갱신한다.
- `heartbeat_at`이 60초 넘게 멈춘 `running` 실행은 죽은 것으로 본다. `unknown`, `error_code: PROCESS_RESTART`로 바꾸고 프로젝트의 `active_run`을 비운다.
- 이 정리는 실행 시작 요청에서 `active_run`이 이미 차 있을 때, 그리고 서버 시작 시에 한다. 둘 다 `heartbeat_at` 조건이 붙은 조건부 갱신이라 살아 있는 실행은 건드리지 않는다.
- 중지 요청은 실행을 가진 프로세스에만 닿는다. 다른 프로세스가 받으면 `ai_runs`에 `stop_requested: true`를 남기고, 실행 쪽이 도구 반복·글자 수신 사이에 이 값을 확인해 멈춘다.

### 보관·휴지통으로 옮길 때

옮기는 것은 막지 않는다. 실행 관련 API 네 개(상태, 스트림, 중지, 시작 제외)는 `Workspace` 대신 "내 프로젝트인지"만 확인해서, 휴지통에 있어도 상태를 보고 중지할 수 있게 한다. 실행 쪽은 답변 저장 직전에 프로젝트가 진행 중인지 확인하고, 아니면 저장하지 않고 `failed`, `PROJECT_NOT_ACTIVE`로 끝낸다. 완전 삭제는 진행 중인 실행이 있으면 409(지금 `ai_jobs`와 같다).

### API

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| `POST` | `/projects/{id}/runs` | 메시지를 보내고 실행 시작. `202 {run_id}` |
| `GET` | `/projects/{id}/runs/{run_id}` | 실행 상태, 실패 사유, 사용량 |
| `GET` | `/projects/{id}/runs/{run_id}/stream?after=N` | SSE. `after` 다음 사건부터 보낸다 |
| `POST` | `/projects/{id}/runs/{run_id}/stop` | 중지 요청 |

기존 `POST /projects/{id}/messages`는 그대로 둔다(AI 답변 없이 사용자 메시지만 저장).

SSE 사건 종류는 파이프라인 이름을 따른다: `started`, `context_usage`, `compression_started`, `compression_finished`, `generating`, `delta`, `tool_started`, `tool_finished`, `generation_usage`, `run_usage`, `completed`, `stopped`, `failed`, `outcome_unknown`. 각 사건은 `{seq, kind, content}`.

메모리 대기열은 실행이 끝나고 10분 뒤 지운다. 그 뒤 `stream`을 부르면 사건 없이 바로 끝나고, 화면은 메시지 목록과 실행 상태를 다시 읽으면 된다.

### 저장 구조

새 컬렉션 `ai_runs`:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `user_id`, `project_id` | objectId | 필수 |
| `status` | enum `running`, `completed`, `failed`, `stopped`, `unknown` | 필수 |
| `user_message_id` | objectId, 선택 | 잠금을 잡은 직후 저장한 사용자 메시지 |
| `assistant_message_id` | objectId, 선택 | 답변을 저장했을 때 |
| `error_code` | string, 선택 | `[A-Z0-9_]`만 |
| `delivery` | string, 선택 | `not_dispatched`, `unknown`, `responded` |
| `usage` | object, 선택 | `UsageTracker.parts` (압축·생성별 호출 수와 토큰) |
| `created_at`, `updated_at` | date | 필수 |
| `finished_at` | date, 선택 | |
| `heartbeat_at` | date, 선택 | 실행 중 15초마다 갱신 |
| `stop_requested` | bool, 선택 | 다른 프로세스가 받은 중지 요청 |

인덱스: `(project_id, created_at desc)`, `(status, heartbeat_at)`.

`projects`에 추가하는 선택 필드:

- `active_run` (objectId): 진행 중인 실행. 없으면 `null`.
- `chat_capsule` (object): 압축한 예전 대화. `{text, covered_ids, revision}`. `revision`은 요약 자체의 번호이고 프로젝트 `revision`과 상관없다. 저장할 때 이 번호와 `active_run`이 맞을 때만 바꾼다(파이프라인 `save_capsule`과 같은 조건). 맞지 않으면 실행은 `failed`, `CAPSULE_CONFLICT`.

프로젝트를 완전히 지울 때 그 프로젝트의 `ai_runs`도 지운다. 진행 중인 실행이 있으면 지금 `ai_jobs`처럼 409.

### 설정

`app/config.py`에 추가하고 `.env.example`에 적는다.

- `ai_provider`: `demo`(기본) 또는 `openai`
- `openai_api_key`: 비어 있고 `openai`면 실행이 `failed`, `KEY_MISSING_OR_INVALID`
- `openai_model`: 기본 `gpt-6-luna`(파이프라인 값)

`openai_test`의 키 확인 API는 그대로 둔다. 이 키는 저장하지 않는다.

### 테스트

- 가져온 엔진 단위 테스트: 실제 네트워크 없이 `httpx` 가짜 전송으로.
- 실행 통합 테스트(임시 `test-mongo`): 가짜 Provider로 정상 답변 저장, 동시 실행 409, 중지 시 부분 답변, 실패 시 잠금 해제, 남의 프로젝트 404, 휴지통 이동 후 늦은 답변 버림, 재시작 복구, 참고 프로젝트 포함·남의 프로젝트와 휴지통 프로젝트 제외·5개 제한·`ref:` 키 읽기, 압축 요약 저장 조건, 도구가 6개 항목을 읽기만 함, API 키가 응답·DB에 남지 않음.

## 정해진 것과 남은 것

사용자가 정한 것:

- 나눠서 이식, 단계별로 진행.
- 실행 기록은 `ai_runs` 컬렉션.
- 정리 내용은 답변 아래 "검수하고 저장하기"로 만든다(AI 검토안 도구 없음).
- 기획 항목 상태는 `WRITTEN`/`SKIPPED`.
- 참고 프로젝트(`chip_refs`)를 1단계부터 AI에 넘긴다.

이 문서에서 Claude가 정한 것(검토할 것):

- 진행 상황은 메모리 대기열, 10분 보관.
- 잠금은 프로젝트 문서의 `active_run`, 압축 요약은 `chat_capsule`.
- 실행 시작 API가 사용자 메시지를 직접 저장하고, 기존 `POST /messages`는 유지.
- 기본 Provider는 모의 응답, 모델 이름은 설정값.
- 1단계 동안 Provider의 `web_search` 코드는 남겨 두되 호출하지 않음.
- 서버 여럿이 같은 Atlas를 쓰는 경우를 위한 살아 있음 표시(15초 갱신, 60초 만료)와 DB를 거친 중지 요청.
- 보관·휴지통 이동은 막지 않고, 실행 API는 소유만 확인, 늦은 답변은 버림.
- 이전 대화는 모든 메시지를 넣고 끊긴 답변에는 표시를 붙임.
- 참고 프로젝트 최대 5개, 휴지통·보관함 프로젝트 제외, 도구 키 `ref:<id>:<섹션>`.

만드는 사람에게 맡기는 것: 실행 함수를 나누는 방식, SSE 대기열 구현 세부, 오류 문구.
