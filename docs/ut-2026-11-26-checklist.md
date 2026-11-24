# UT(11/26 목) 준비: 해야 할 것과 알아 둘 것

- 작성: 2026-11-22
- 대상: 백엔드 담당, 화면 담당, AI 파이프라인 담당
- 관련 문서: 설계 [docs/superpowers/specs/2026-11-19-ai-chat-run-design.md](superpowers/specs/2026-11-19-ai-chat-run-design.md), API 요약 [README.md](../README.md), 실제 요청·응답 필드는 Swagger(`http://localhost:8000/docs`)

---

## 0. v05 변경 (2026-11-24, 브랜치 `feature/backend-v05`)

11/23 「서비스 구조 & MVP 기획서 v2」에 맞춰 백엔드를 다시 맞췄다. 설계는 [docs/superpowers/specs/2026-11-24-v05-structured-docs-design.md](superpowers/specs/2026-11-24-v05-structured-docs-design.md).

- **전용 문서**: 펀딩 상세페이지(F01~F08, 필수 6)·사업계획서(B01~B08, 필수 8). 항목별 임시 입력·저장·검토 완료, 진행도, 마지막 항목, 미리보기·md/txt/docx/pdf. API는 `/projects/{id}/docs`.
- **항목별 AI**: `POST .../sections/{sid}/assist`(초안/다듬기). 사용자가 고른 참고(`references`)만 보낸다. 후보는 답변 메시지의 `candidate`, 적용(`/apply`)은 임시 입력에만. 후보 뒤에 글이 바뀌었으면 409 `CANDIDATE_OUTDATED`(비교 후 `confirm: true`).
- **할 일 → 일정**: `POST .../sections/{sid}/task-proposals`로 후보(`proposals`), 사용자가 날짜를 골라 `POST /projects/{id}/tasks`(`source_doc_id`·`source_section_id`·`source_proposal_id`). 같은 후보는 한 번만 등록(다시 보내면 200 `existing`). 일정의 `source`로 원래 항목에 돌아간다.
- **계정 홈**: `GET /home?brand_id=&today=`.
- **첨부 자동 읽기 끔**: 기획서대로 기본 false(`AI_READ_ATTACHMENTS`). 파일 내용은 사용자가 `note` 참고로 발췌해 보낸다.
- **추가 API**: 계정 전체 일정 `GET /tasks?from=&to=&brand_id=`. 메시지의 할 일 후보에는 등록한 일정의 `task_id`·`task_status`가 붙는다(재방문 시 "등록됨" 표시). 휴지통에 있는 일정이면 복원을 안내한다.
- **화면 담당 추가 사건**: `assist_candidate`(content=메시지 id, 후보 보여 주기), `task_proposals`(content=메시지 id, 후보 확인 창).
- 기획서 UT 과업과의 대응: ① 펀딩 항목 작성 + AI 도움 → docs·assist·apply ② 견적 할 일 남기고 항목으로 복귀 → task-proposals·tasks·`source` ③ 재방문 시 위치 찾기 → home·`last_section_id` ④ 같은 프로젝트에서 사업계획서 시작 → `template_id: business_plan` + `references`(plan·record·message·note).

## 1. 지금 상태

조원 파이프라인(`feature/ai-agent-pipeline`)의 AI 기능을 백엔드(`feature/backend-prototype`)에 모두 옮겼다.

| 기능 | 상태 |
| --- | --- |
| AI 채팅 답변(스트리밍, 긴 대화 압축, 중지, 사용량 기록) | 완료 |
| 6개 기획 항목·참고 프로젝트·AI와 함께 정리한 내용을 AI가 읽기 | 완료 |
| 웹 검색(검색 허용이 켜진 프로젝트만) | 완료 |
| 문서 초안 → 산출물로 저장, 기존 산출물 참고, 출처·변경 확인 | 완료 |
| 참고 파일(TXT·MD·DOCX)을 AI가 읽기 | v05에서 기본 끔(`AI_READ_ATTACHMENTS=false`). 파일이 붙었다는 사실과 이름만 AI에 알린다 |
| AI 기록 검토안 → 사용자가 검수하고 저장 | 완료 |
| 내보내기(산출물·저장된 기획을 md·txt·docx·pdf로) | 완료 |

- **자동 테스트**: 임시 DB에서 332개 통과. 실제 Pandoc·LibreOffice로 DOCX·PDF 변환까지 돌렸다.
- **리허설(11/22)**: 실제 서버 이미지(nginx + 백엔드)와 리허설 전용 DB로 UT 흐름 44개를 돌려 모두 통과했다. 이때 AI는 모의 응답이었다(아래 4번 참고).

---

## 2. UT 전에 해야 할 것

### 백엔드 담당

- [ ] **커밋·push.** 2~4단계 변경(약 39개 파일)이 아직 커밋되지 않았다.
- [ ] **`.env`에 AI 설정 넣기(BAZE 게이트웨이).** `AI_PROVIDER=openai`, `OPENAI_API_KEY=<BAZE 키>`, `OPENAI_BASE_URL=https://api.baze.co/v1/gateway`, `OPENAI_MODEL=gpt-6-luna`. 게이트웨이에서는 웹 검색이 꺼지고, 토큰 수는 서버가 어림잡는다. 이전 안내: `AI_PROVIDER=openai`, `OPENAI_API_KEY=<키>`, 필요하면 `OPENAI_MODEL`(기본 `gpt-6-luna`). 이때부터 호출 비용이 든다. 키는 git에 올리지 않는다.
- [ ] **서버 이미지 다시 빌드.** Pandoc·LibreOffice·한글 글꼴이 들어가 첫 빌드는 몇 분 걸리고 이미지가 수백 MB 커진다.

  ```bash
  docker compose up -d --build
  ```

- [ ] **Atlas에 DB 규칙 적용.** `ai_runs`·`structured_docs`·`doc_sections`·`tasks` 컬렉션과 규칙이 생긴다. **반드시 실행한다.** 중복 방지 인덱스는 서버 시작 때도 만들지만, 검증 규칙은 이 스크립트로만 들어간다.

  ```bash
  docker compose run --rm --build backend python init_db.py
  ```

- [ ] **실제 OpenAI로 확인.** 키를 넣은 뒤 테스트 페이지(`http://localhost`)에서 아래를 눌러 본다.
  - "AI 답변 요청": 답이 글자 단위로 조금씩 나오는지
  - "채팅 설정"(검색 허용) → "AI 검색 답변": 출처 주소가 붙는지
  - "AI 기록 검토안": 응답 메시지에 `record_draft`가 오는지
  - "AI 문서 초안" → 산출물로 저장 → docx·pdf 내려받기
- [ ] **UT용 계정·프로젝트 준비.** 참가자가 쓸 계정과, 6개 항목이 일부 채워진 예시 프로젝트를 미리 만든다.

### 화면 담당

- [ ] 아래 3번 "화면이 지켜야 할 규칙"을 반영한다.
- [ ] 실제 화면으로 위 확인 항목을 한 번 더 돌린다(특히 SSE 스트리밍, 파일 업로드, 내보내기 버튼).

### AI 파이프라인 담당

- [ ] 백엔드로 옮긴 지시문(`backend/app/ai/context.py`의 `PROVENANCE_POLICY`)을 확인한다. 6개 항목 상태를 우리 기준(`WRITTEN`/`SKIPPED`)으로 바꾸고 참고 프로젝트 규칙을 더했다.
- [ ] 파이프라인과 달라진 점(4번 "파이프라인과 다르게 한 것")을 확인한다.

---

## 3. 화면이 지켜야 할 규칙

### AI 답변 요청

- `POST /projects/{id}/runs` → 202 `{run_id}`. 본문:

  | 필드 | 설명 |
  | --- | --- |
  | `text` | 필수. 공백만 있으면 422 |
  | `mode` | `auto`(기본, 문서 요청이면 문서), `chat`(대화만), `document`(문서 초안) |
  | `include_session` | 문서를 만들 때 이전 대화를 넣을지. 비우면 넣는다 |
  | `public_search_terms` | 웹 검색에 써도 되는 공개 검색어(최대 500자) |
  | `asset_ids` | 이번 메시지에 붙인 파일(`POST /assets`로 올린 것) |
  | `reference_document`, `reference_body` | 문서 모드에서 참고할 산출물 key, 저장 안 한 편집 중인 글 |

- 한 프로젝트에 실행은 하나. 진행 중이면 409 `RUN_ACTIVE`. 보관·휴지통 프로젝트는 409 `PROJECT_NOT_ACTIVE`.
- 상태 `GET .../runs/{run_id}`, 중지 `POST .../runs/{run_id}/stop`. 둘 다 휴지통에 있어도 된다.

### 진행 상황(SSE)

- `GET .../runs/{run_id}/stream`. 끊기면 브라우저 `EventSource`가 `Last-Event-ID`로 이어 받는다.
- 사건마다 `{seq, kind, content}`. 화면이 처리할 것:

  | 사건 | 화면 동작 |
  | --- | --- |
  | `delta` | 답변 글에 이어 붙인다 |
  | `document_route` | **지금까지 받은 글을 지운다**(문서로 다시 만든다) |
  | `document_candidate` | content가 메시지 id. 그 답변에 "산출물로 저장" 버튼 |
  | `record_candidate` | content가 `{title, body}` JSON. "검수하고 저장" 창 |
  | `tool_query`, `tool_started`, `tool_finished`, `tool_result` | 검색·조회 중 표시(선택) |
  | `completed` | 끝. 메시지 목록을 다시 읽는다 |
  | `stopped`, `failed`, `outcome_unknown` | 끝. content의 안내 문구를 보여 주고 메시지 목록을 다시 읽는다 |

- 끝났거나 10분 지난 실행의 스트림은 사건 없이 바로 끝난다. 이때는 상태 API와 메시지 목록으로 보여 준다.

### 저장

- 산출물: `POST /items/document` + `{message_id}` → 답변의 `doc`(제목·내용)이 산출물이 된다.
- 정리 내용: `POST /items/record` + `{message_id, title?, content?}`. 제목·내용을 비우면 AI 검토안(`record_draft`)을 쓴다.
- 메시지 목록(`GET /messages`)에 `doc`, `record_draft`가 함께 온다. 새로고침 후에도 버튼을 다시 띄울 수 있다.

### 파일·내보내기

- 파일 업로드 `POST /assets`(최대 2MB, TXT·MD·DOCX·PNG·JPEG·WebP). 3MB 이상은 nginx가 413.
- 프로젝트 참고 파일은 `PATCH /projects/{id}`의 `file_refs`, 이번 메시지만이면 `asset_ids`.
- 내보내기:
  - 산출물: `GET /items/document/{key}/export/{md|txt|docx|pdf}`
  - 저장된 기획: `GET /plan-export/{md|txt|docx|pdf}`
  - 변환 도구가 없으면 503(md·txt로 안내).
- 출처 변경 확인: `GET /items/document/{key}/changes` → 항목마다 `unchanged`·`changed`·`unavailable`.

---

## 4. 알아 둘 것

### 확인하지 못한 것

- **실제 OpenAI 호출**: `.env`에 키가 없어 리허설은 모의 응답으로 돌렸다. 웹 검색, 기록 검토안, AI가 스스로 문서로 넘어가는 흐름은 자동 테스트로만 확인됐다.
- **글자 단위 스트리밍(nginx 경유)**: 모의 응답은 순식간에 끝나 실제로 조금씩 보이는지는 못 봤다. nginx에 스트림 전용 규칙(버퍼링 끔, 1시간 대기)은 넣었다.

### 제약

- 첨부 파일 내용은 기본으로 AI에 보내지 않는다(기획서 v2). 필요한 내용은 사용자가 발췌해 `note` 참고로 보낸다. `AI_READ_ATTACHMENTS=true`로 켜면 TXT·MD·DOCX를 최대 10개, 파일당 5만 자까지 읽는다.
- 참고에서 빼거나 지운 파일로 만든 답변·요약은 이후 대화에 쓰지 않는다. 원본 파일은 남는다.
- AI에게 넘기는 정리 내용은 최근 20개. 참고 프로젝트는 5개까지(남의 것, 보관·휴지통 프로젝트는 빠짐).
- 웹 검색은 정해진 일반 개념이나 사용자가 적은 공개 검색어의 단어로만 한다. 벗어나면 검색하지 않고 AI가 다시 고른다.
- 파일과 정리 내용을 매번 통째로 보내서, 큰 파일이 많으면 느리고 비싸다.
- PDF 변환은 서버 한 대에서 동시에 2개까지. 한 번에 몇 초 걸린다.
- DOCX·PDF 내보내기에서 이미지는 글(대체 글)로 바뀐다(서버 파일·외부 주소를 읽지 못하게 막은 것).
- AI 실행은 시작한 서버에서만 돈다. 서버를 여러 대 띄우면, 다른 서버가 받은 스트림 요청은 바로 끝난다.
- 서버가 꺼지면 진행 중이던 실행은 60초 뒤 `unknown`으로 정리되고 잠금이 풀린다.

### 파이프라인과 다르게 한 것

- 6개 항목 상태: `UNSET`/`DRAFT`/`DECIDED` 대신 `WRITTEN`/`SKIPPED`.
- 실행 기록: SQLite `runs` 대신 MongoDB `ai_runs` 컬렉션.
- 첨부: 대화별 첨부 대신 기존 `assets`(프로젝트 `file_refs` + 메시지 `asset_ids`).
- 정리 내용: 프로젝트당 하나가 아니라 여러 개. AI는 검토안만 만들고 저장은 사용자가 한다.
- 검색 끄기: 별도 API 없이 `search_enabled`를 끄면 실행 중에도 반영된다.
- 문서를 만들 때 이전 대화를 기본으로 넣는다(파이프라인은 기본 제외).
- 참고 프로젝트(`chip_refs`)를 AI가 읽는 기능은 우리가 추가했다.

### 아직 없는 것

- 다음에 해야 할 일(`next_action`) 자동 채우기
- 이미지 파일 내용 읽기
- 실제 화면(지금 `front/src/App.jsx`는 API 테스트 페이지)
- 배포 준비(HTTPS, 로그인 횟수 제한, 8000 포트 닫기)

### 작은 문제(UT에 지장 없음)

- 마지막 두 확인 사이에 상태가 바뀌면 답변 없이 `completed`로 기록될 수 있다.
- 압축할 때 DB 확인이 자주 일어난다.
- 서버 이미지가 수백 MB 커졌다(한글 글꼴이 큼).

---

## 5. 문제 생겼을 때

| 증상 | 확인 |
| --- | --- |
| AI 답변이 바로 `failed`, "OpenAI 연결 설정" | `.env`의 `AI_PROVIDER=openai`, `OPENAI_API_KEY` 확인 후 `docker compose up -d` |
| 계속 409 `RUN_ACTIVE` | 진행 중 실행이 끝나길 기다리거나 `POST .../stop`. 서버가 죽었으면 60초 뒤 풀린다 |
| docx·pdf가 503 | 서버 이미지를 다시 빌드했는지(`--build`) 확인 |
| 답변이 한 번에 몰려서 옴 | 프론트 이미지도 다시 빌드했는지 확인(nginx 스트림 규칙) |
| 실행 기록 확인 | Swagger의 `GET /projects/{id}/runs/{run_id}`, Atlas의 `ai_runs` 컬렉션 |
| 서버 오류 | `docker compose logs backend` |
