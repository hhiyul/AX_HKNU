# AX_HKNU

브랜드와 상품 기획을 AI와 함께 만들어 가는 서비스의 저장소입니다.
지금은 **백엔드 기본 틀**(로그인, 브랜드, 상품, 파일, 작업 조회)까지 있고, AI 생성 기능과 실제 화면은 아직 없습니다.

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
 └─ brands (브랜드)          brands.user_id   → users
     ├─ projects (상품)      projects.brand_id → brands
     ├─ assets (파일)        assets.brand_id   → brands, assets.project_id → projects (선택)
     └─ ai_jobs (AI 작업)    ai_jobs.user_id, brand_id, project_id (선택)
```

- 상품과 파일에는 `user_id`가 없습니다. **"내 것인지"는 소속 브랜드의 주인으로 판단**합니다(`services/ownership.py`).
- 컬렉션의 필드 타입, 필수 여부, 인덱스는 `backend/init_db.py`가 원본입니다. 필드를 추가하면 이 파일도 같이 고쳐야 합니다.

## API

모든 경로는 화면에서 부를 때 앞에 `/api`가 붙습니다. `/auth/signup`, `/auth/login`, `/auth/logout`을 뺀 나머지는 로그인이 필요합니다.

| 기능 | 경로 | 비고 |
|---|---|---|
| 인증 | `POST /auth/signup`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | 가입과 로그인은 쿠키를 심어 줍니다 |
| 프로필 | `PATCH /profile` | 창작자 이름 변경 |
| 브랜드 | `GET·POST /brands`, `GET·PATCH·DELETE /brands/{id}` | |
| 상품 | `GET·POST /projects`, `GET·PATCH·DELETE /projects/{id}` | 목록은 `?brand_id=`로 거를 수 있습니다 |
| 파일 | `GET·POST /assets`, `GET·DELETE /assets/{id}`, `GET /assets/{id}/file` | 목록은 `?brand_id=` 필수. 업로드는 multipart |
| AI 작업 | `GET /jobs`, `GET /jobs/{id}` | 조회만. 생성과 상태 변경은 파이프라인 담당 |
| OpenAI | `POST /openai/test` | 키가 유효한지만 확인. 키는 저장하지 않습니다 |

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
| 413 | `FILE_TOO_LARGE` | 2MB 초과 |
| 422 | `VALIDATION_ERROR`, `NAME_REQUIRED`, `INVALID_ASSET`, `LOGO_NOT_IMAGE` | 입력값 문제 |
| 503 | `AUTH_NOT_CONFIGURED`, `DB_NOT_CONFIGURED`, `DB_UNAVAILABLE` | `.env` 누락 또는 Atlas 연결 실패 |

**초안 저장 (브랜드, 상품)**
- 생성하면 항상 `status: "draft"`로 시작합니다. 브랜드는 `brand_type`, 상품은 `brand_id`만 필수입니다.
- `PATCH`는 **보낸 필드만** 바꿉니다. 글자 필드에 빈 문자열을 보내면 `null`로 저장됩니다.
- `status: "completed"`로 바꾸려면 `name`이 있어야 합니다.
- 정의하지 않은 필드를 보내면 422입니다. 상품의 `brand_id`는 만든 뒤 바꿀 수 없습니다.
- `keywords`, `colors`, `features`는 항목당 100자, 50개까지입니다.

**목록 조회**
- 최신순이며 `?limit=`(기본 50, 최대 100)과 `?skip=`으로 나눠 받습니다.

**파일**
- TXT, PNG, JPEG, WebP만 받고 2MB까지입니다. 형식은 확장자가 아니라 **파일 내용**으로 판별합니다.
- `asset_type`이 `logo`, `banner`, `product_image`면 이미지만 올릴 수 있습니다. `document`는 전부 됩니다.
- 브랜드 로고는 파일을 올린 뒤 `PATCH /brands/{id}`에 `logo_asset_id`를 보내 지정합니다. 이미지만 됩니다.
- 응답의 `file_url`은 `/assets/{id}/file`입니다. 화면에서는 앞에 `/api`를 붙여 씁니다.

**삭제**
- 브랜드는 상품이나 파일이 남아 있으면 지울 수 없습니다(409). 상품은 파일이 남아 있으면 지울 수 없습니다.
- 진행 중(`queued`, `running`)인 AI 작업이 있어도 지울 수 없습니다. 끝난 작업 기록은 함께 지워집니다.
- 파일을 지우면 그 파일을 로고로 쓰던 브랜드의 `logo_asset_id`가 비워집니다.

## 백엔드 코드 구조

```
backend/
├─ main.py            앱 생성, 라우터 등록
├─ init_db.py         컬렉션 검증 규칙과 인덱스 (DB 설계의 원본)
├─ app/
│  ├─ config.py       환경변수
│  ├─ db.py           Mongo 연결
│  ├─ deps.py         current_user(로그인 확인), Page(목록 범위)
│  ├─ errors.py       AppError와 오류 응답 형태
│  ├─ storage.py      업로드 파일 저장. 저장소를 바꿀 때는 이 파일만 고칩니다
│  ├─ routers/        경로 정의. 로직 없이 services를 부릅니다
│  ├─ schemas/        요청·응답 모델 (Pydantic)
│  └─ services/       실제 로직
│     ├─ ownership.py   "내 자료인지" 확인. 아니면 404
│     ├─ drafts.py      초안 저장, 완료 조건, 삭제 시 작업 기록 정리
│     └─ auth.py, brands.py, projects.py, assets.py
└─ tests/             API 단위로 검증하는 테스트
```

**새 API를 추가할 때**
1. `schemas/`에 요청·응답 모델을 만듭니다. 요청 모델은 `RequestModel`을 상속하면 모르는 필드를 거부합니다.
2. `services/`에 로직을 씁니다. 자료를 꺼낼 때는 `get_owned_*`를 써야 남의 자료가 막힙니다.
3. `routers/`에 경로를 만들고 `Depends(current_user)`를 붙입니다. 응답은 `to_api()`로 감싸면 `_id`가 `id`로, ObjectId가 문자열로 바뀝니다.
4. 오류는 `raise AppError(상태, "CODE", "메시지")`로 냅니다.
5. 새 컬렉션이나 필드는 `init_db.py`에 추가하고 `python init_db.py`(위 "실행" 4번)로 Atlas에 적용합니다.
6. `tests/`에 테스트를 추가합니다.

## 아직 없는 것

- **AI 생성과 챗봇**: 파이프라인 코드를 받아 백엔드에 이식할 예정입니다. 지금은 `ai_jobs` 조회와 OpenAI 키 확인만 있습니다.
- **기획서(IA v0.1)의 나머지 영역**: 프로젝트 개요, 상품기획 6개 항목과 항목별 상태, 경제성, 기획 산출물 버전 관리. 코드에서 `TODO(IA v0.1)`로 검색하면 자리가 표시돼 있습니다.
- **`ai_jobs.job_type`**: 지정 6개 + `others`의 enum과 상세 문자열 필드로 바꿀 계획이며 값은 미정입니다.
- **실제 화면**: `front/src/App.jsx`는 임시입니다.
- **배포 준비**: HTTPS, 로그인 횟수 제한, 백엔드 8000 포트 닫기, 큰 파일 업로드 방식.

## 명세 문서

- [AI 에이전트 파이프라인 명세](docs/ai-agent-pipeline/README.md): 시스템 기능명세 → 사용자 기능명세 → 모듈 기능명세
