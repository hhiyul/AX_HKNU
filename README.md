# AX_HKNU

브랜드와 상품 기획을 AI와 함께 만들어 가는 서비스의 저장소입니다.
지금은 **백엔드**(로그인, 브랜드, 프로젝트 작업실, 파일, 작업 조회)까지 있고, AI 생성 기능과 실제 화면은 아직 없습니다.
프로젝트 작업실은 UX Flow Atlas v0.3 기준으로 개요, 상품기획 6개 항목, 경제성, 기획 산출물, 추가 기록, AI 채팅·제안의 저장 구조와 API를 갖췄습니다.

## 한눈에 보기

```
브라우저
  │  http://localhost
  ▼
react-front (nginx)
  ├─ /        → React 화면 (지금은 API 동작 확인용 임시 화면)
  └─ /api/**  → /api를 떼고 backend:8000 으로 전달
                   │
                   ▼
              backend (FastAPI) ──→ MongoDB Atlas
                   └─ 업로드 파일은 Docker 볼륨(backend_uploads)
```

| 폴더 | 내용 |
|---|---|
| `backend/` | FastAPI 서버. 이 저장소의 대부분의 코드가 여기 있습니다. |
| `front/` | React(Vite) + nginx. `src/App.jsx`는 임시 테스트 화면이라 통째로 교체할 예정입니다. |
| `gate/` | Spring Cloud Gateway. **지금은 쓰지 않고** 코드만 남겨 두었습니다. |
| `docs/` | 설계 문서와 구현 계획 |

## 실행

Docker만 있으면 됩니다.

1. `.env.example`을 `.env`로 복사하고 값을 채웁니다.

   | 변수 | 설명 |
   |---|---|
   | `MONGO_DETAILS` | Atlas 연결 문자열. DB 이름을 빼면 `ax_platform`을 씁니다. |
   | `JWT_SECRET` | 로그인 토큰 서명 키. 비어 있으면 로그인 API가 503을 돌려줍니다. 만드는 명령은 `.env.example`에 있습니다. |
   | `COOKIE_SECURE` | HTTPS로 배포할 때만 `true` |
   | `AI_PROVIDER` | `demo`(기본, 실제 호출 없는 모의 응답) 또는 `openai`(실제 호출, 비용 발생) |
   | `OPENAI_API_KEY` | `AI_PROVIDER=openai`일 때 쓰는 키 |
   | `OPENAI_MODEL` | 기본 `gpt-6-luna` |

2. 컨테이너를 띄웁니다.
   ```bash
   docker compose up -d --build
   ```

3. 접속합니다.

   | 주소 | 용도 |
   |---|---|
   | `http://localhost` | 화면 (임시 테스트 화면) |
   | `http://localhost/api/...` | API. 화면이 호출하는 주소 |
   | `http://localhost:8000/docs` | Swagger. 백엔드 직접 접근이며 디버깅용 |

4. DB를 새로 만들었거나 `backend/init_db.py`의 규칙을 바꿨을 때만 규칙과 인덱스를 적용합니다.
   ```bash
   docker compose run --rm --build backend python init_db.py
   ```

> `.env`의 Atlas에 실제로 저장됩니다. 테스트 화면에서 누른 것도 그대로 쌓입니다.

코드를 고친 뒤에는 해당 서비스를 다시 빌드해야 반영됩니다.
```bash
docker compose up -d --build backend
```

프론트만 빠르게 고칠 때는 `front/`에서 `npm run dev`를 쓰면 됩니다(`http://localhost:5173`). 이때도 `/api`는 백엔드 8000으로 넘어갑니다.

## 테스트

임시 로컬 MongoDB에서만 실행되고 Atlas는 건드리지 않습니다. 연결 주소가 `test-mongo`가 아니면 테스트가 시작되지 않습니다.

```bash
docker compose -f docker-compose.test.yml run --rm test-runner sh -c "pip install -q -r requirements-dev.txt && pytest -q"
```

## 데이터 구조

```
users (사용자)
 ├─ brands (브랜드)
 ├─ projects (프로젝트)              brand_id는 선택. 진행 단계, 보관·휴지통, 메모, 최근 작업, AI 채팅 설정도 여기 있다
 │   ├─ items                        프로젝트에 저장하는 글. kind로 구분한다
 │   │                                 plan 상품기획 6개 항목 · record AI와 함께 정리한 내용 · document 산출물
 │   ├─ cost_sheets                  경제성 입력값(프로젝트당 하나). 비교 결과는 조회할 때 계산한다
 │   ├─ conversations                AI 채팅 대화 목록. 대화마다 이전 대화·압축 요약이 따로다
 │   ├─ messages                     AI 채팅 메시지. 채팅은 conversation_id로 대화에 속한다(항목별 AI 메시지는 없음)
 │   ├─ ledger_entries               정산 수입·지출 내역(휴지통·복원)
 │   └─ ai_runs                      AI 채팅 실행 기록(상태, 실패 사유, 토큰 사용량). 진행 중 잠금은 projects.active_run
 ├─ assets (파일)                    brand_id, project_id 중 하나 이상
 ├─ ai_jobs (AI 작업)
 └─ requests                         중복 요청 방지 기록 (7일 뒤 자동 삭제)
```

- **모든 문서에 `user_id`가 있고**, "내 것인지"는 그 값으로 판단합니다(`services/store.py`).
- 상품기획 항목, AI와 함께 정리한 내용, 산출물은 모두 "제목 + 내용"이라 **`items` 한 컬렉션**에 `kind`로 구분해 저장합니다. 새 종류가 생기면 컬렉션이나 API를 늘리지 않고 `kind`만 추가합니다.
- 화면 구성과 동작은 프로토타입(https://duing.shop/AX/)을 기준으로 맞췄습니다.
- 컬렉션의 필드 타입, 필수 여부, 인덱스는 `backend/init_db.py`가 원본입니다. 필드를 추가하면 이 파일도 같이 고쳐야 합니다.
- `docs/superpowers/specs/2026-11-11-backend-v03-design.md`는 프로토타입에 맞추기 전(v0.3) 설계 기록입니다. 초안/확정, 이력, AI 제안, 가격 시나리오는 지금 코드에 없습니다.

## API

모든 경로는 화면에서 부를 때 앞에 `/api`가 붙습니다. `/auth/signup`, `/auth/login`, `/auth/logout`을 뺀 나머지는 로그인이 필요합니다.

| 기능 | 경로 | 비고 |
|---|---|---|
| 인증 | `POST /auth/signup`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | 가입과 로그인은 쿠키를 심어 줍니다 |
| 프로필 | `PATCH /profile` | 창작자 이름 변경 |
| 브랜드 | `GET·POST /brands`, `GET·PATCH·DELETE /brands/{id}` | 이름과 소개만으로 만들 수 있습니다 |
| 프로젝트 | `GET·POST /projects`, `GET·PATCH·DELETE /projects/{id}` | 이름만 있으면 만들 수 있습니다. 목록은 `?brand_id=`(`none`이면 브랜드 없는 프로젝트), `?status=active\|archived\|trash`, `?q=`(이름 검색)로 거릅니다. 진행 단계·메모·보관·복원·AI 채팅 설정은 `PATCH`로 바꿉니다 |
| 파일 | `GET·POST /assets`, `GET·DELETE /assets/{id}`, `GET /assets/{id}/file` | 목록은 `?brand_id=`나 `?project_id=` 필수. 업로드는 multipart |
| AI 작업 | `GET /jobs`, `GET /jobs/{id}` | 조회만. 생성과 상태 변경은 파이프라인 담당 |
| OpenAI | `POST /openai/test` | 키가 유효한지만 확인. 키는 저장하지 않습니다 |
| 요청 결과 | `GET /requests/{key}` | `Idempotency-Key`로 보낸 저장 요청이 반영됐는지 확인 |

아래는 모두 `/projects/{id}` 밑에 붙습니다.

| 영역 | 경로 | 비고 |
|---|---|---|
| 개요 | `GET /overview` | 작업실 홈에 필요한 것: 프로젝트(메모·최근 작업 포함), 기획 6개 항목 상태, 글·대화·파일 수, 확정 가격 |
| AI 맥락 | `GET /projection?sections=` | 파이프라인에 넘길 프로젝트 내용과 `bundle_revision` |
| 글 | `GET·POST /items/{kind}`, `GET·PUT·DELETE /items/{kind}/{key}`, `DELETE /items/document/{key}/draft` | `kind`는 `plan`, `record`, `document`. AI 답변 저장은 `POST`에 `message_id`를 넣습니다 |
| 내보내기 | `GET /items/document/{key}/export/{md·txt·docx·pdf}`, `GET /plan-export/{md·txt·docx·pdf}` | 저장된 산출물, 또는 6개 항목과 AI와 함께 정리한 내용을 파일로 내려받습니다. 임시 저장은 넣지 않습니다. 변환 도구가 없으면 503 |
| 문서 출처 | `GET /items/document/{key}/changes` | AI가 만든 산출물이 참고한 기획 항목·정리 내용·참고 산출물·참고 파일이 그 뒤 바뀌었는지(`unchanged`·`changed`·`unavailable`) |
| 경제성 | `GET·PUT /economics`, `POST /economics/baseline`, `POST /economics/confirm` | 입력 저장, 비교 기준 저장, 판매가 결정(`plan/ck_05`에 반영) |
| AI 채팅 | `GET·POST /messages` | 메시지 목록과, AI 답변 없이 사용자 메시지만 저장 |
| 전용 문서(v05) | `GET /templates`, `GET·POST /docs`, `GET·PATCH·DELETE /docs/{doc_id}`, `POST /docs/{doc_id}/restore`, `PUT·DELETE .../sections/{sid}/draft`, `PUT .../sections/{sid}`, `POST .../sections/{sid}/review`, `GET /docs/{doc_id}/preview`, `GET /docs/{doc_id}/export/{md·txt·docx·pdf}` | 펀딩 상세페이지(F01~F08, 필수 6)·사업계획서(B01~B08, 필수 8)를 항목별로 씁니다. 항목 상태는 `UNWRITTEN`·`WRITING`·`REVIEWED`, 진행도는 확인한 필수 항목 수입니다. 설계는 `docs/superpowers/specs/2026-11-24-v05-structured-docs-design.md` |
| 항목별 AI(v05) | `POST .../sections/{sid}/assist`, `POST .../sections/{sid}/apply`, `GET .../sections/{sid}/messages`, `POST .../sections/{sid}/task-proposals` | 고른 참고만 보내 후보(답변의 `candidate`)를 만들고, 적용하면 임시 입력에만 들어갑니다. 후보 뒤에 글이 바뀌었으면 409 `CANDIDATE_OUTDATED`. 할 일 후보는 답변의 `proposals`이고 아직 일정이 아닙니다 |
| 할 일(v05) | `GET·POST /tasks`, `PATCH·DELETE /tasks/{task_id}`, `POST /tasks/{task_id}/restore` | 날짜 필수. 같은 AI 후보(`source_proposal_id`)는 한 번만 등록되고 다시 보내면 기존 일정(200, `existing`). `source`로 원래 문서 항목에 돌아갑니다 |
| 홈(v05) | `GET /home?brand_id=&today=` | 계정 전체(또는 브랜드)의 최근 문서·오늘 할 일·기한 지난 일·진행 중 문서·규칙 기반 추천 |
| AI 답변 | `POST /runs`, `GET /runs/{run_id}`, `GET /runs/{run_id}/stream?after=`, `POST /runs/{run_id}/stop` | 메시지를 보내면 실행이 시작되고(202), 진행 상황은 SSE로 받습니다. 프로젝트당 한 번에 하나(409 `RUN_ACTIVE`). 상태·스트림·중지는 휴지통에 있어도 됩니다. 본문: `text`, `mode`(`auto`·`chat`·`document`), `include_session`, `public_search_terms`, `asset_ids`(이번에 붙인 파일), `reference_document`·`reference_body`(문서 모드에서 참고할 산출물). 첨부 파일 내용은 기본으로 AI에 보내지 않습니다(`AI_READ_ATTACHMENTS=true`면 TXT·MD·DOCX를 읽음). 기록 검토안은 `record_candidate` 사건과 답변의 `record_draft`로 옵니다. 웹 검색은 프로젝트의 `search_enabled`가 켜져 있을 때만 합니다. 문서 모드에서 끝까지 만든 답변은 메시지의 `doc`에 남고 `document_candidate` 사건이 옵니다. `document_route` 사건이 오면 그때까지 받은 글을 지우세요. `include_session`을 비우면 이전 대화를 넣고 문서를 만듭니다 |

요청과 응답의 정확한 필드는 Swagger(`http://localhost:8000/docs`)에서 확인하세요.

## 공통 규칙

**로그인**
- JWT를 `access_token` 쿠키(HttpOnly, 7일)에 담습니다. 프론트는 요청에 `credentials: 'include'`만 붙이면 됩니다.
- 비밀번호는 argon2로 해시해서 저장합니다.

**오류 응답**은 항상 같은 형태입니다.
```json
{ "code": "NOT_FOUND", "message": "자료를 찾을 수 없습니다." }
```

| 상태 | code | 의미 |
|---|---|---|
| 401 | `UNAUTHORIZED`, `INVALID_CREDENTIALS` | 로그인 필요, 이메일·비밀번호 불일치 |
| 404 | `NOT_FOUND`, `FILE_MISSING` | 없는 자료. **남의 자료와 잘못된 ID 형식도 404**입니다 |
| 409 | `EMAIL_TAKEN`, `BRAND_IN_USE`, `PROJECT_IN_USE`, `NOT_IN_TRASH` | 중복 가입, 지울 수 없는 상태 |
| 409 | `PROJECT_NOT_ACTIVE` | 보관함·휴지통에 있는 프로젝트의 작업실을 열려고 함 |
| 409 | `REVISION_CONFLICT`, `REQUEST_IN_PROGRESS` | 다른 곳에서 먼저 수정됨, 같은 요청 처리 중 |
| 413 | `FILE_TOO_LARGE` | 2MB 초과 |
| 422 | `VALIDATION_ERROR`, `NAME_REQUIRED`, `INVALID_ASSET`, `LOGO_NOT_IMAGE`, `ASSET_OWNER_REQUIRED`, `AMOUNT_NOT_ALLOWED`, `NO_DRAFT`, `ITEM_LIMIT`, `COSTS_INCOMPLETE` | 입력값 문제 |
| 503 | `AUTH_NOT_CONFIGURED`, `DB_NOT_CONFIGURED`, `DB_UNAVAILABLE` | `.env` 누락 또는 Atlas 연결 실패 |

**버전(revision)과 동시 수정**
- 고칠 수 있는 자료(프로젝트, 글, 경제성 입력)는 응답에 정수 `revision`이 있습니다.
- 수정 요청에는 **읽었을 때의 `revision`을 그대로** 실어 보냅니다. 그 사이 다른 탭이나 기기에서 바뀌었으면 409 `REVISION_CONFLICT`이고 아무것도 저장되지 않습니다. 화면은 최신 내용을 다시 읽어 사용자에게 보여 주면 됩니다.
- 경제성 입력은 아직 저장한 적이 없으면 `revision: 0`으로 조회되고, 처음 저장할 때 0을 보냅니다. 기획 항목은 프로젝트를 만들 때 6개가 함께 생기므로 `revision: 1`부터 시작합니다.

**중복 요청 방지**
- 저장 요청에 `Idempotency-Key` 헤더(요청마다 새로 만든 값)를 붙이면 같은 키의 요청은 한 번만 처리됩니다. 같은 키로 다시 보내면 처음 결과를 그대로 돌려줍니다.
- 응답을 못 받았으면 다시 보내기 전에 `GET /requests/{key}`로 확인합니다. `APPLIED`(반영됨), `NOT_APPLIED`(거부됨), `PROCESSING`(아직 모름).

**브랜드 초안 저장**
- 생성하면 항상 `status: "draft"`로 시작합니다. 필수 필드는 없고, `brand_type`을 보내지 않으면 `existing`입니다.
- `PATCH`는 **보낸 필드만** 바꿉니다. 글자 필드에 빈 문자열을 보내면 `null`로 저장됩니다.
- `status: "completed"`로 바꾸려면 `name`이 있어야 합니다.
- 정의하지 않은 필드를 보내면 422입니다. `keywords`, `colors`는 항목당 100자, 50개까지입니다.

**프로젝트**
- `stage`(진행 단계)는 `planning` 기획, `prototype` 시제품, `ready` 판매 준비, `selling` 판매 중입니다. 사용자가 직접 바꾸며 자동으로 바뀌지 않습니다.
- `status`는 `active` 진행 중, `archived` 보관함, `trash` 휴지통입니다. `PATCH`에 `status`를 보내 보관·복원하고, `DELETE`는 휴지통으로 옮깁니다. 보관함·휴지통에 있는 동안에는 작업실(`/projects/{id}/...`)을 열 수 없습니다(409 `PROJECT_NOT_ACTIVE`).
- `note`는 나만의 메모장, `recent`는 최근 작업 20건(최신이 먼저), `plan_filled`는 기획 6개 항목 중 작성한 개수입니다. `next_action`(다음에 해야 할 일)은 AI 파이프라인이 채우는 자리입니다.
- 프로젝트를 만들면 기획 6개 항목이 항상 함께 생깁니다. 생성할 때 `plan`에 마법사의 답변(`[{key, content, amount?}]`)을 함께 보낼 수 있고, 답하지 않았거나 `plan`을 보내지 않은 항목은 `SKIPPED`(건너뜀)로 남습니다.

**글 (`/items`)**
- 초안/확정 구분과 이력은 없습니다. 저장하면 바로 반영됩니다. 내용은 다듬지 않고 받은 그대로 저장합니다.
- `PUT`은 `content`와 `title` 중 보낸 것만 바꿉니다.
- 종류별로 다른 점은 아래뿐입니다.

| `kind` | 무엇 | 다른 점 |
|---|---|---|
| `plan` | 상품기획 6개 항목 | `key`가 `ck_01` 상품선정, `ck_02` 고객선정, `ck_03` 구매가치결정, `ck_04` 상품구성결정, `ck_05` 가격결정, `ck_06` 제작가능성으로 고정. 새로 만들거나 지울 수 없습니다. `state`는 `SKIPPED` 건너뜀, `WRITTEN` 작성됨 둘뿐이며, 화면에서는 `SKIPPED`를 "미작성"으로 보여 줍니다. 내용을 비워 저장하면 건너뜀으로 돌아갑니다. 금액(`amount`)은 `ck_05`에만 넣으며 보내지 않으면 유지됩니다. 5,000자 |
| `record` | AI와 함께 정리한 내용 | AI 답변을 검수해서 `POST`로 저장합니다(`title`, `content`, `message_id`). 같은 답변을 다시 검수하면 새로 만들지 않고 고칩니다. 5,000자, 200개까지 |
| `document` | 산출물 | `POST`에 `message_id`만 보내면 그 답변의 문서 초안(`doc`)을 저장합니다. 이미 저장한 답변이면 같은 문서를 돌려줍니다. 편집 중인 내용은 `PUT`에 `as_draft: true`로 임시 저장하고(`draft`), `DELETE .../draft`로 버립니다. 50,000자, 200개까지 |

- 메시지의 `record_key`, `doc_key`는 그 답변에서 저장한 글입니다. 글을 지우면 비워지고 원래 대화는 남습니다.

**경제성**
- 입력은 상품 1개 기준의 `material` 재료비, `labor` 공임, `packaging` 포장비, `shipping` 판매자 부담 배송비, `other` 기타, `fee_rate` 판매 수수료(%), `price` 비교할 판매가입니다. `null`은 "아직 모름"이며 0원과 다릅니다. 모르는 값이 있으면 계산하지 않습니다.
- `GET /economics`는 입력값과 함께 `before`(변경 전 기준), `keep`(기존 판매가 유지), `change`(판매가 변경), `candidates`(입력한 판매가와 ±5,000원)를 계산해 돌려줍니다. 계산 결과는 저장하지 않습니다.
- 비교 기준(`base`)은 `POST /economics/baseline`으로만 바뀝니다.
- `POST /economics/confirm`(`mode`: `keep` 또는 `change`)을 해야 가격이 `plan/ck_05`에 반영됩니다. 후보 가격을 입력하는 것만으로는 확정 가격(`confirmed_price`)이 바뀌지 않습니다.
- 계산식: 남는 금액 = 판매가 − 개당 비용 합 − 판매가 × 수수료(%). 세금은 포함하지 않습니다.

**목록 조회**
- 최신순이며 `?limit=`(기본 50, 최대 100)과 `?skip=`으로 나눠 받습니다.

**파일**
- TXT, MD, DOCX, PNG, JPEG, WebP만 받고 2MB까지입니다. 브랜드나 프로젝트 중 하나에는 속해야 합니다. 형식은 확장자가 아니라 **파일 내용**으로 판별합니다.
- `asset_type`이 `logo`, `banner`, `product_image`면 이미지만 올릴 수 있습니다. `document`는 전부 됩니다.
- 브랜드 로고는 파일을 올린 뒤 `PATCH /brands/{id}`에 `logo_asset_id`를 보내 지정합니다. 이미지만 됩니다.
- 응답의 `file_url`은 `/assets/{id}/file`입니다. 화면에서는 앞에 `/api`를 붙여 씁니다.

**삭제**
- 브랜드를 지워도 연결된 프로젝트는 지워지지 않고 "브랜드 없음"으로 남습니다. 브랜드에만 속한 파일(로고 등)은 함께 지워집니다.
- **프로젝트 `DELETE`는 휴지통으로 옮기는 것**이고 자료는 그대로 남아 복원할 수 있습니다. `DELETE ...?permanent=true`는 휴지통에 있는 프로젝트를 딸린 자료(글, 경제성, 채팅)까지 완전히 지웁니다. 이때 프로젝트에만 속한 파일은 지워지고, 브랜드에도 속한 파일은 연결만 풀립니다.
- 진행 중(`queued`, `running`)인 AI 작업이 있으면 브랜드 삭제와 프로젝트 완전 삭제가 막힙니다. 끝난 작업 기록은 함께 지워집니다.
- 파일을 지우면 그 파일을 로고로 쓰던 브랜드의 `logo_asset_id`가 비워지고, 프로젝트의 참고 파일 목록(`file_refs`)에서도 빠집니다.

## 백엔드 코드 구조

```
backend/
├─ main.py            앱 생성, 라우터 등록
├─ init_db.py         컬렉션 검증 규칙과 인덱스 (DB 설계의 원본)
├─ app/
│  ├─ config.py       환경변수
│  ├─ db.py           Mongo 연결
│  ├─ deps.py         current_user(로그인 확인), Page(목록 범위), Workspace(내 프로젝트), Once(중복 요청 방지)
│  ├─ errors.py       AppError와 오류 응답 형태
│  ├─ storage.py      업로드 파일 저장. 저장소를 바꿀 때는 이 파일만 고칩니다
│  ├─ routers/        경로 정의. 로직 없이 services를 부릅니다
│  ├─ schemas/        요청·응답 모델 (Pydantic)
│  └─ services/       실제 로직
│     ├─ store.py       공통 도우미: 내 자료 확인, revision 조건부 저장, 최근 작업 기록, 중복 요청 방지
│     ├─ items.py       글(기획 항목·AI 정리 내용·산출물). 종류별 규칙은 RULES에 있습니다
│     ├─ economics.py, chat.py, overview.py
│     └─ auth.py, brands.py, projects.py, assets.py
└─ tests/             API 단위로 검증하는 테스트
```

**새 API를 추가할 때**
1. `schemas/`에 요청·응답 모델을 만듭니다. 요청 모델은 `RequestModel`을 상속하면 모르는 필드를 거부합니다.
2. `services/`에 로직을 씁니다. 자료를 꺼낼 때는 `store.py`의 `get_owned_*`를 써야 남의 자료가 막힙니다.
3. `routers/`에 경로를 만들고 `Depends(current_user)`를 붙입니다. 프로젝트 밑의 API는 `ws: Workspace = Depends()` 하나로 로그인과 소유권 확인이 끝납니다. 고치는 API는 `save_revision`/`upsert_revision`으로 저장합니다. 응답은 `to_api()`로 감싸면 `_id`가 `id`로, ObjectId가 문자열로 바뀝니다.
4. 오류는 `raise AppError(상태, "CODE", "메시지")`로 냅니다.
5. 새 컬렉션이나 필드는 `init_db.py`에 추가하고 `python init_db.py`(위 "실행" 4번)로 Atlas에 적용합니다.
6. `tests/`에 테스트를 추가합니다.

## 아직 없는 것

- **AI 채팅**: 조원 파이프라인의 엔진을 `app/ai/`로 옮겨 채팅 답변(스트리밍, 긴 대화 압축, 기획 항목·참고 프로젝트 읽기, 중지, 사용량), 웹 검색, 문서 초안과 TXT·MD 내보내기를 붙였습니다. 설계는 `docs/superpowers/specs/2026-11-19-ai-chat-run-design.md`. 참고 파일 읽기, 기록 검토안, 기존 산출물 참고, 문서 출처·변경 확인, DOCX·PDF 내보내기까지 붙였습니다(4단계). 다음에 해야 할 일(`next_action`)도 아직 채우지 않습니다.
- **프로토타입에 있지만 아직 안 만든 것**: 가입 이메일 인증과 비밀번호 재설정, 참고 자료의 사진/견적서 구분과 20MB·프로젝트당 20개·PDF/GIF/HEIC 등 형식(지금은 2MB, 기존 형식 그대로), 전체 작업 백업. 작성 중인 새 프로젝트 마법사 임시 저장은 화면에서 보관합니다.
- **`ai_jobs.job_type`**: 작업이 다루는 기획 항목 `ck_01`~`ck_06` 중 하나입니다. `brand_id`는 선택이라 브랜드 없는 프로젝트의 작업도 만들 수 있습니다. 6개에 들지 않는 작업(`others`와 상세 문자열)은 아직 정하지 않았습니다.
- **실제 화면**: `front/src/App.jsx`는 임시입니다.
- **배포 준비**: HTTPS, 로그인 횟수 제한, 백엔드 8000 포트 닫기, 큰 파일 업로드 방식.
