# 인계: v06 UT용 임시 화면 (2026-11-24)

다른 세션에서 이어서 작업할 때 아래 "붙여 넣을 프롬프트"를 그대로 첫 메시지로 쓰면 된다.

## 붙여 넣을 프롬프트

```
AX_HKNU 저장소(C:\Users\rkdrn\Documents\GitHub\AX_HKNU)에서 UT(11/26 목)용 임시 화면을 이어서 만든다.
먼저 docs/superpowers/handoffs/2026-11-24-v06-front-handoff.md 를 끝까지 읽고, 거기 적힌 "남은 일" 순서대로 진행해 줘.
규칙: 커밋·push는 내가 직접 한다. .env 값은 출력하지 마. 공용 Atlas에 테스트 데이터를 쓰지 말고 로컬 DB로 확인해.
화면은 폰(375px) 먼저, PC(UT 환경)에서도 자연스럽게. 백엔드 호출은 front/src/api 에만 둔다(나중에 조원 화면으로 갈아탈 수 있게).
```

## 배경

- 서비스: AX STUDIO. 창작자가 펀딩 상세페이지·사업계획서를 항목별로 쓰고, AI 도움과 할 일·일정을 같은 작업에 연결한다.
- 기준 기획서: 「서비스 구조 & MVP 기획서 — 인터뷰 반영 v2」(2026-11-23, 사용자 PC `C:\Users\rkdrn\Documents\카카오톡 받은 파일\AX-STUDIO-Service-MVP-Interview-v2.html`). UT 과업 4개:
  1. 펀딩 소개의 한 항목을 작성하고 AI 도움 받기
  2. 견적 확인할 일을 남기고, 그 일에서 다시 작성 중인 항목으로 돌아오기
  3. 재방문해서 어디까지 썼고 무엇을 해야 하는지 찾기
  4. 같은 프로젝트에서 사업계획서를 시작하며 이전 기록 활용
- 사용자 결정:
  - 프로토타입(https://duing.shop/AX/) 코드를 가져오지 않고 React로 새로 만든다. 생김새만 프로토타입을 따른다.
  - UT는 PC로 하지만 실제 사용은 폰 중심이다.
  - 목요일 전에 조원 화면이 오면 그쪽으로 바꿀 수 있다. 그래서 API 호출을 `front/src/api`로 분리한다.
- 범위(Claude가 정함): UT 과업 4개 + 로그인/가입 + 프로젝트 목록·생성 + 기획 6항목 + 자유 AI 대화. 정산·저장함·브랜드 관리는 넣지 않는다.

## 브랜치와 커밋 상태 (중요)

- 지금 브랜치: `feature/frontend-v06` (`feature/backend-v05`에서 갈라짐, 기준 커밋 `09bad55`).
- **v05 백엔드 변경 41개 파일이 아직 커밋 전**이라 작업 트리에 같이 있다(백엔드 `backend/`, `docs/`, `docker-compose.yml`, `.env.example`, `front/nginx.conf` 등). v06 작업은 `front/`만 바꾼다.
- 사용자에게 안내할 것: v05 백엔드 파일은 `git switch feature/backend-v05` 후 백엔드 파일만 골라 커밋하고, v06 화면 파일은 `feature/frontend-v06`에서 커밋한다(커밋 안 한 변경은 브랜치를 옮겨도 따라다닌다).

## 백엔드 (v05, 이미 완료·테스트 402개 통과)

- 설계: `docs/superpowers/specs/2026-11-24-v05-structured-docs-design.md`, API 요약: `README.md`, UT 체크리스트: `docs/ut-2026-11-26-checklist.md`(0절이 v05 요약).
- 주요 API(모두 쿠키 로그인, nginx에서는 `/api` 접두사):
  - 로그인·가입: `/auth/login`, `/auth/signup`, `/auth/me`, `/auth/logout`
  - 계정 홈 `GET /home`: `recent_docs`, `today_tasks`, `overdue_tasks`, `docs_in_progress`, `recommendations`(각 항목에 `project_id`·`doc_id`·`section_id`·`task_id`)
  - 계정 전체 일정 `GET /tasks?from=&to=`
  - 프로젝트 `GET·POST /projects`, `GET /projects/{id}`
  - 작성 틀 `GET /templates`
  - 문서 `GET·POST /projects/{id}/docs`(생성에 `Idempotency-Key`), `GET /projects/{id}/docs/{doc_id}`(항목 전체·`progress`·`last_section_id`)
  - 항목:
    - 임시 입력 `PUT .../sections/{sid}/draft {text, draft_revision}`
    - 저장 `PUT .../sections/{sid} {text, revision}`
    - 검토 완료 `POST .../sections/{sid}/review {revision}`
    - 상태 `UNWRITTEN`·`WRITING`·`REVIEWED`
  - 항목별 AI:
    - 요청 `POST .../sections/{sid}/assist {action: draft|refine, request, references:[{type: plan|section|brand|record|message|note, id?, text?}]}` → 202 `{run_id}`
    - 진행 상황 SSE `GET /projects/{id}/runs/{run_id}/stream`(사건 `delta`, `assist_candidate`, `completed`, `failed` 등)
    - 후보 `GET .../sections/{sid}/messages`의 마지막 답변 `candidate.text`
    - 적용 `POST .../sections/{sid}/apply {message_id, draft_revision, confirm}`. 409 `CANDIDATE_OUTDATED`면 사용자 확인 뒤 `confirm: true`
  - 할 일 제안:
    - 요청 `POST .../sections/{sid}/task-proposals {request}` → 202
    - 후보는 답변 메시지의 `proposals[{proposal_id, title, suggested_date|null, note, task_id, task_status}]`
    - 등록 `POST /projects/{id}/tasks {title, date(필수), note, source_proposal_id}`. 같은 후보는 200 `existing`
    - 일정의 `source{doc_id, section_id, section_title}`로 원래 항목에 복귀
  - 일정 `GET /projects/{id}/tasks`, `PATCH /projects/{id}/tasks/{tid} {done|title|date|note, revision}`
  - 기획 6항목 `GET /projects/{id}/items/plan`, `PUT /projects/{id}/items/plan/{ck_0N} {content, revision}`(첫 저장 revision 1)
  - 자유 AI 대화 `POST /projects/{id}/runs {text, mode: chat}`, 메시지 `GET /projects/{id}/messages`
  - 미리보기·내보내기 `GET .../docs/{doc_id}/preview`, `GET .../docs/{doc_id}/export/{md|txt|docx|pdf}`
- AI: `.env`가 BAZE 게이트웨이로 설정돼 있다(`OPENAI_BASE_URL=https://api.baze.co/v1/gateway`, `gpt-6-luna`). 실제 호출 4종(항목 AI·할 일 제안·자유 대화·문서 초안)과 nginx 경유 스트리밍을 확인했다. 크레딧이 들므로 화면 확인은 `AI_PROVIDER=demo`로 하는 게 좋다(모의 응답, 할 일 후보 1개를 냄).

## 이미 만든 화면 파일 (feature/frontend-v06, 커밋 전)

- `front/src/api/client.js`: fetch 공통, `ApiError`, `errorText`, `newKey`.
- `front/src/api/index.js`: `auth`, `projects`, `home`, `plan`, `docs`, `tasks`, `chat`, `runs`, `download`.
- `front/src/api/stream.js`: `followRun(projectId, runId, {onEvent, onDone})`(EventSource).
- `front/src/router.js`: 해시 라우터 `useRoute`, `go`, `paths`(`#/home`, `#/login`, `#/p/:id/docs`, `#/p/:id/docs/:docId?s=F05`, `#/p/:id/tasks`, `#/p/:id/plan`, `#/p/:id/chat`, `#/dev`).
- `front/src/styles/prototype.css`(프로토타입 CSS 원본) + `front/src/styles/app.css`(추가 조각). 화면 틀은 `front/src/components/Shell.jsx`.
  - 토큰: --ink #22252d, --muted #7b808c, --line #e7e9ee, --blue #445ad2, --pale #f1f3ff, --bg #f7f8fa, Noto Sans KR.
  - 레이아웃: 버튼 44px, 폰에서는 `.tabs`가 하단 고정, 768px 이상은 상단, 항목 목차는 `.chips`(폰에서 가로 스크롤).
- `front/src/App.jsx`: `/auth/me`로 로그인 확인 → `Login`, `Home`, `Project`. `#/dev`는 예전 API 테스트 페이지.
- `front/src/screens/Login.jsx`, `front/src/screens/Home.jsx`(최근 문서·오늘/기한 지난 일·추천·프로젝트 목록·새 프로젝트)
- `front/src/components/ui.jsx`: `TopBar`, `Progress`, `StateBadge`, `STATE_LABEL`.
- `front/src/dev/DevConsole.jsx`·`DevConsole.css`: 예전 `App.jsx`·`App.css`를 옮긴 것(`git mv`).
- 2026-11-24 기준 화면은 모두 만들었고 PC·폰(375px) 리허설로 확인했다. 최신 구조는 `docs/superpowers/specs/2026-11-24-v06-ut-front.md` 참고.

## 남은 일 (순서대로)

1. `front/src/screens/Project.jsx`: 탭 4개(문서 작성 `docs`·일정 `tasks`·기획 `plan`·AI 대화 `chat`). `route.parts[2]`가 탭, `route.parts[3]`이 doc_id, `route.query.s`가 항목. TopBar의 뒤로 가기는 문서 편집 중이면 문서 목록, 아니면 홈.
2. `front/src/screens/Docs.jsx`: "새 문서 시작"(펀딩/사업계획서 버튼. 같은 버튼을 다시 누르면 같은 Idempotency-Key 사용) + 작성 중 문서 목록(진행도, 마지막 항목으로 열기).
3. `front/src/screens/DocEditor.jsx`(핵심):
   - 항목 칩 목차(상태 점) → 선택 항목 카드(제목·질문·가이드).
   - 본문 입력 = 임시 입력 ?? 저장 본문. 버튼은 임시 저장·저장·검토 완료(저장 본문이 있고 변경이 없을 때만).
   - 다른 항목으로 옮길 때 변경이 있으면 임시 저장할지 묻는다.
   - AI 도움(접이식):
     - 초안 만들기/다듬기, 요청 입력, 참고 고르기(내용 있는 기획 6항목, 다른 항목, 직접 적는 발췌 note)
     - 실행 → SSE로 글 표시 → 후보 카드 "적용/닫기". 적용 409면 확인 후 confirm
     - 409 `RUN_ACTIVE`는 안내 문구로
   - 할 일 제안 받기 → 후보마다 날짜 입력(없으면 사용자가 고름) + "일정 등록". 이미 등록된 후보(`task_id`)는 "등록됨"으로 표시.
   - 미리보기(저장 안 한 임시 입력이 있으면 경고)·PDF·DOCX 내려받기(`download`).
   - 이 항목의 AI 기록(`sectionMessages`).
   - 파일이 커지면 `DocAssist.jsx`, `TaskProposals.jsx`로 나눈다.
4. `front/src/screens/Tasks.jsx`: 이 프로젝트 일정(날짜순). 완료 체크(`PATCH done`, revision 필요), 원래 항목으로 이동(`source`), 직접 일정 추가(제목·날짜).
5. `front/src/screens/Plan.jsx`: 기획 6항목 보기·저장(상태 `WRITTEN`/`SKIPPED`를 작성됨/미작성으로).
6. `front/src/screens/Chat.jsx`: 메시지 목록 + 입력 → `chat.send` → SSE delta 표시 → 끝나면 목록 다시 읽기.
7. `main.jsx`·`index.html` 정리, `cd front && npx vite build` 통과.
8. 실제 확인(로컬 DB, demo 모드):
   - 리허설 설정 파일 예시는 아래. 백엔드 `MONGO_DETAILS=mongodb://rehearsal-mongo:27017/...`, `JWT_SECRET` 임의값, `AI_PROVIDER=demo`, 포트 8080/8001.
   - 내장 브라우저로 375px(폰)과 PC 폭에서 UT 과업 4개를 직접 눌러 확인하고, 끝나면 `down -v --rmi local`로 정리.
9. `docs/superpowers/specs/2026-11-24-v06-ut-front.md` 진행 체크 갱신, 사용자에게 결과와 커밋 안내.

### 리허설 설정 예시 (`docker compose -p ax-rehearsal -f docker-compose.yml -f <이 파일> up -d --build`)

```yaml
services:
  rehearsal-mongo:
    image: mongo:8
    networks: [app-network]
    healthcheck:
      test: ["CMD", "mongosh", "--quiet", "--eval", "db.adminCommand('ping')"]
      interval: 3s
      timeout: 5s
      retries: 20
  backend:
    ports: !override
      - "8001:8000"
    environment:
      MONGO_DETAILS: mongodb://rehearsal-mongo:27017/ax_rehearsal
      JWT_SECRET: rehearsal-secret-at-least-32-bytes-long!!
      COOKIE_SECURE: "false"
      AI_PROVIDER: demo
      OPENAI_API_KEY: ""
    depends_on:
      rehearsal-mongo:
        condition: service_healthy
  react-front:
    ports: !override
      - "8080:80"
```

띄운 뒤 `docker compose -p ax-rehearsal ... exec -T backend python init_db.py`로 규칙을 적용한다.

## 지켜야 할 것

- 커밋·push·PR은 사용자가 직접 한다.
- 백엔드 테스트는 임시 `test-mongo`에서만:
  - 실행: `docker compose -f docker-compose.test.yml run --rm test-runner sh -c "pip install -q -r requirements-dev.txt && pytest -q"`
  - 끝나면: `docker compose -f docker-compose.test.yml down`
- `.env` 값은 출력하지 않는다.
- Git Bash heredoc에 작은따옴표나 `\n`이 섞인 긴 Python을 넣으면 깨진다. 스크립트는 파일로 써서 실행한다.
- Docker Desktop이 꺼져 있을 수 있다. 켤 때 `"C:\Program Files\Docker\Docker\Docker Desktop.exe"`를 실행한다. 켜지는 동안에는 셸이 잠깐 실패할 수 있다.
