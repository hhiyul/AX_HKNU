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
 ├─ projects (프로젝트)              brand_id는 선택. AI 채팅 설정과 메시지 수도 여기 있다
 │   ├─ items / item_versions        프로젝트에 저장하는 글과 확정 이력. kind로 구분한다
 │   │                                 plan 상품기획 6개 항목 · record 추가 기록 · document 기획 산출물
 │   ├─ cost_sheets / scenarios      경제성: 비용표(프로젝트당 하나), 가격 시나리오
 │   ├─ messages                     AI 채팅 메시지
 │   └─ proposals                    AI 제안
 ├─ assets (파일)                    brand_id, project_id 중 하나 이상
 ├─ ai_jobs (AI 작업)
 └─ requests                         중복 요청 방지 기록 (7일 뒤 자동 삭제)
```

- **모든 문서에 `user_id`가 있고**, "내 것인지"는 그 값으로 판단합니다(`services/store.py`).
- 상품기획 항목, 추가 기록, 기획 산출물은 모두 "글 + 초안/확정 + 이력"이라 **`items` 한 컬렉션**에 `kind`로 구분해 저장합니다. 새 종류가 생기면 컬렉션이나 API를 늘리지 않고 `kind`만 추가합니다.
- 컬렉션의 필드 타입, 필수 여부, 인덱스는 `backend/init_db.py`가 원본입니다. 필드를 추가하면 이 파일도 같이 고쳐야 합니다.
- 설계에서 내린 결정과 그 이유는 `docs/superpowers/specs/2026-11-11-backend-v03-design.md`에 있습니다.

## API

모든 경로는 화면에서 부를 때 앞에 `/api`가 붙습니다. `/auth/signup`, `/auth/login`, `/auth/logout`을 뺀 나머지는 로그인이 필요합니다.

| 기능 | 경로 | 비고 |
|---|---|---|
| 인증 | `POST /auth/signup`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | 가입과 로그인은 쿠키를 심어 줍니다 |
| 프로필 | `PATCH /profile` | 창작자 이름 변경 |
| 브랜드 | `GET·POST /brands`, `GET·PATCH·DELETE /brands/{id}` | |
| 프로젝트 | `GET·POST /projects`, `GET·PATCH·DELETE /projects/{id}` | 이름만 있으면 만들 수 있습니다. 목록은 `?brand_id=`로 거를 수 있습니다. AI 채팅 설정(`search_enabled`, `file_refs`, `chip_refs`)도 `PATCH`로 바꿉니다 |
| 파일 | `GET·POST /assets`, `GET·DELETE /assets/{id}`, `GET /assets/{id}/file` | 목록은 `?brand_id=`나 `?project_id=` 필수. 업로드는 multipart |
| AI 작업 | `GET /jobs`, `GET /jobs/{id}` | 조회만. 생성과 상태 변경은 파이프라인 담당 |
| OpenAI | `POST /openai/test` | 키가 유효한지만 확인. 키는 저장하지 않습니다 |
| 요청 결과 | `GET /requests/{key}` | `Idempotency-Key`로 보낸 저장 요청이 반영됐는지 확인 |

아래는 모두 `/projects/{id}` 밑에 붙습니다.

| 영역 | 경로 | 비고 |
|---|---|---|
| 개요 | `GET /overview` | 항목 상태, 대기 중인 제안, 최신화가 필요한 문서 등 요약 |
| AI 맥락 | `GET /projection?sections=` | 파이프라인에 넘길 프로젝트 내용과 `bundle_revision` |
| 글 | `GET·POST /items/{kind}`, `GET·PUT·DELETE /items/{kind}/{key}`, `DELETE /items/{kind}/{key}/draft`, `GET /items/{kind}/{key}/versions` | `kind`는 `plan`, `record`, `document`. `PUT`의 `state`가 `DRAFT`면 초안 저장, `DECIDED`면 확정 |
| 글 (부가) | `POST /items/batch`, `GET /items/export?format=md\|txt`, `POST /items/{kind}/similar`, `POST /items/document/{key}/sync` | 기획 항목 일괄 저장·내보내기, 비슷한 글 찾기, 문서 최신화 확인 |
| 경제성 | `GET·PUT /economics/costs`, `GET·POST /economics/scenarios`, `DELETE /economics/scenarios/{sid}`, `POST /economics/scenarios/{sid}/adopt` | 채택하면 `plan/ck_05`의 초안으로 넘어갑니다 |
| AI 채팅 | `GET·POST /messages` | 사용자 메시지 저장만. 답변 생성은 파이프라인 |
| AI 제안 | `GET·POST /proposals`, `GET /proposals/{pid}`, `POST /proposals/{pid}/hold`, `/reject`, `/apply` | 반영(`apply`)해야 기획에 들어갑니다 |

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
| 409 | `EMAIL_TAKEN`, `BRAND_IN_USE`, `PROJECT_IN_USE` | 중복 가입, 지울 수 없는 상태 |
| 409 | `REVISION_CONFLICT`, `REQUEST_IN_PROGRESS`, `PROPOSAL_CLOSED` | 다른 곳에서 먼저 수정됨, 같은 요청 처리 중, 이미 닫힌 제안 |
| 413 | `FILE_TOO_LARGE` | 2MB 초과 |
| 422 | `VALIDATION_ERROR`, `NAME_REQUIRED`, `INVALID_ASSET`, `LOGO_NOT_IMAGE`, `ASSET_OWNER_REQUIRED`, `AMOUNT_NOT_ALLOWED`, `NO_DRAFT`, `NO_DECIDED`, `TITLE_REQUIRED`, `ITEM_LIMIT` | 입력값 문제 |
| 503 | `AUTH_NOT_CONFIGURED`, `DB_NOT_CONFIGURED`, `DB_UNAVAILABLE` | `.env` 누락 또는 Atlas 연결 실패 |

**버전(revision)과 동시 수정**
- 고칠 수 있는 자료(프로젝트, 글, 비용표)는 응답에 정수 `revision`이 있습니다.
- 수정 요청에는 **읽었을 때의 `revision`을 그대로** 실어 보냅니다. 그 사이 다른 탭이나 기기에서 바뀌었으면 409 `REVISION_CONFLICT`이고 아무것도 저장되지 않습니다. 화면은 최신 내용을 다시 읽어 사용자에게 보여 주면 됩니다.
- 기획 항목과 비용표는 아직 저장한 적이 없으면 `revision: 0`으로 조회되고, 처음 저장할 때 0을 보냅니다.

**중복 요청 방지**
- 저장 요청에 `Idempotency-Key` 헤더(요청마다 새로 만든 값)를 붙이면 같은 키의 요청은 한 번만 처리됩니다. 같은 키로 다시 보내면 처음 결과를 그대로 돌려줍니다.
- 응답을 못 받았으면 다시 보내기 전에 `GET /requests/{key}`로 확인합니다. `APPLIED`(반영됨), `NOT_APPLIED`(거부됨), `PROCESSING`(아직 모름).

**브랜드 초안 저장**
- 생성하면 항상 `status: "draft"`로 시작하고 `brand_type`만 필수입니다.
- `PATCH`는 **보낸 필드만** 바꿉니다. 글자 필드에 빈 문자열을 보내면 `null`로 저장됩니다.
- `status: "completed"`로 바꾸려면 `name`이 있어야 합니다.
- 정의하지 않은 필드를 보내면 422입니다. `keywords`, `colors`는 항목당 100자, 50개까지입니다.

**글 (`/items`)**
- 글마다 초안(`draft`)과 확정본(`decided`)을 따로 갖습니다. `state`는 초안이 있으면 `DRAFT`, 확정본만 있으면 `DECIDED`, 둘 다 없으면 `UNSET`입니다.
- 확정된 글을 다시 고치는 동안에도 기존 확정본은 남아 있습니다. `DELETE .../draft`로 초안만 버리면 확정 상태로 돌아갑니다.
- 확정할 때마다 이력이 쌓입니다. 내용은 다듬지 않고 받은 그대로 저장합니다.
- 종류별로 다른 점은 아래뿐입니다.

| `kind` | 무엇 | 다른 점 |
|---|---|---|
| `plan` | 상품기획 6개 항목 | `key`가 `ck_01` 상품 선정, `ck_02` 고객 선정, `ck_03` 구매 가치, `ck_04` 상품 구성, `ck_05` 가격·원가, `ck_06` 제작 가능성으로 고정. 새로 만들 수 없고, `DELETE`는 비워서 미작성으로 되돌립니다. 금액(`amount`)은 `ck_05`에만 넣습니다. 5,000자 |
| `record` | 추가 기록 | `POST`로 만들고 `key`는 서버가 정합니다. 5,000자, 200개까지 |
| `document` | 기획 산출물 | 만들 때 `doc_type` 필수. 50,000자. 기획이 바뀌어도 자동으로 고치지 않고, 문서를 만든 뒤 확정본이 바뀐 항목을 `stale_items`로 알려 줍니다 |

- `POST /items/batch`는 기획 항목별로 `APPLIED`/`REJECTED`를 돌려주고 통과한 항목만 저장합니다.

**경제성**
- 비용의 `amount: null`은 "아직 모름"이며 0원과 다릅니다. 모르는 값과 승인하지 않은 가정(`basis: "ASSUMPTION"`, `approved: false`)은 계산에서 빼고 시나리오의 `excluded`에 남깁니다. 이때 `complete`는 `false`입니다.
- 계산식은 임시입니다(`services/economics.py`의 `compute`).

**AI 제안**
- 제안은 기획과 따로 보관되고 `apply`해야 기획 항목이나 추가 기록에 들어갑니다. `apply`에는 사용자가 고친 최종 내용과 기획 항목의 현재 `revision`을 보냅니다.

**목록 조회**
- 최신순이며 `?limit=`(기본 50, 최대 100)과 `?skip=`으로 나눠 받습니다.

**파일**
- TXT, MD, DOCX, PNG, JPEG, WebP만 받고 2MB까지입니다. 브랜드나 프로젝트 중 하나에는 속해야 합니다. 형식은 확장자가 아니라 **파일 내용**으로 판별합니다.
- `asset_type`이 `logo`, `banner`, `product_image`면 이미지만 올릴 수 있습니다. `document`는 전부 됩니다.
- 브랜드 로고는 파일을 올린 뒤 `PATCH /brands/{id}`에 `logo_asset_id`를 보내 지정합니다. 이미지만 됩니다.
- 응답의 `file_url`은 `/assets/{id}/file`입니다. 화면에서는 앞에 `/api`를 붙여 씁니다.

**삭제**
- 브랜드는 연결된 프로젝트나 파일이 남아 있으면 지울 수 없습니다(409).
- **프로젝트를 지우면 딸린 자료(글, 경제성, 채팅, 제안)가 모두 지워집니다.** 휴지통이나 복구는 없습니다. 프로젝트에만 속한 파일은 지워지고, 브랜드에도 속한 파일은 연결만 풀립니다.
- 진행 중(`queued`, `running`)인 AI 작업이 있으면 지울 수 없습니다. 끝난 작업 기록은 함께 지워집니다.
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
│     ├─ store.py       공통 도우미: 내 자료 확인, revision 조건부 저장, 중복 요청 방지
│     ├─ items.py       글(기획 항목·추가 기록·산출물). 종류별 규칙은 RULES에 있습니다
│     ├─ economics.py, chat.py, proposals.py, overview.py
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

- **AI 생성과 챗봇 답변**: 파이프라인 코드를 받아 백엔드에 이식할 예정입니다. 저장 구조는 준비돼 있습니다. 답변은 `services/chat.py`의 `add_message(role="ASSISTANT")`, 제안은 `services/proposals.py`의 `create_proposal`, 글 저장은 `services/items.py`의 `save_item`·`create_item`, 프로젝트 내용은 `services/overview.py`의 `get_projection`을 쓰면 됩니다.
- **전체 채팅(프로젝트 밖 채팅)**: UX v0.3에는 있지만 파이프라인 명세가 프로젝트당 채팅 하나로 정해 만들지 않았습니다.
- **경제성 계산 기준**: 세금, 배송비 배부 등은 미정이라 임시 식을 씁니다.
- **`ai_jobs.job_type`**: 지정 6개 + `others`의 enum과 상세 문자열 필드로 바꿀 계획이며 값은 미정입니다.
- **실제 화면**: `front/src/App.jsx`는 임시입니다.
- **배포 준비**: HTTPS, 로그인 횟수 제한, 백엔드 8000 포트 닫기, 큰 파일 업로드 방식.
