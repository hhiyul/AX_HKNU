# 백엔드 기본 틀 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DB 슬라이드 5개 컬렉션을 쓰는 인증·브랜드·상품·파일·작업 조회 API와 OpenAI 키 연결 테스트를 FastAPI로 만든다.

**Architecture:** `main.py`가 `app/routers/*`를 등록하고, 라우터는 `app/schemas`로 입력을 검증한 뒤 `app/services`에서 DB를 다룬다. 파일은 `app/storage.py`로 분리한다. 인증은 HttpOnly 쿠키의 JWT.

**Tech Stack:** Python 3.11, FastAPI, PyMongo `AsyncMongoClient`, argon2-cffi, PyJWT, email-validator, httpx, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-11-05-backend-skeleton-design.md`

## Global Constraints

- 브랜치 `feature/backend-skeleton`에서만 작업. `main` 직접 커밋과 push 금지
- `front/` 수정 금지. AI 생성 호출과 파이프라인 코드 금지
- `backend/init_db.py`와 Atlas 규칙 변경 금지. 테스트는 Atlas 접속 금지
- `docs/`는 커밋하지 않음 (사용자가 요청할 때까지)
- 오류 형식은 항상 `{"code": str, "message": str}`. 사용자 입력을 오류 응답에 되돌려주지 않음
- 응답의 ID는 문자열 `id`, 참조 ID도 문자열. `password_hash`는 응답 금지
- 다른 사용자의 자료와 잘못된 형식의 ID는 모두 404 `NOT_FOUND`
- 파일: TXT·PNG·JPEG·WebP, 2MB(2097152바이트)까지. JWT 만료 7일. 기본 DB 이름 `ax_platform`
- 주석과 메시지는 한국어, 기존 `init_db.py` 말투를 따름

## Review Focus

- 비ASCII·개행이 든 OpenAI 키: 500 없이 422를 반환하고 키가 응답에 남지 않음
- 변조·만료 JWT, 삭제된 사용자의 JWT: 500 없이 401
- `PATCH`에 `keywords: null`, `status: null`처럼 null 불가 필드: Atlas 검증 오류(500)가 아닌 422
- 볼륨이 비워져 파일만 사라진 경우: `/assets/{id}/file`이 500이 아닌 404 `FILE_MISSING`
- 한글·경로 문자가 든 파일 이름: 원래 이름은 표시용으로만 쓰고 저장 경로에 절대 쓰이지 않음

---

### Task 1: 앱 뼈대, 설정, 테스트 환경

**Files:**
- Create: `backend/app/__init__.py`, `app/config.py`, `app/db.py`, `app/errors.py`
- Create: `backend/requirements-dev.txt`, `backend/pytest.ini`, `backend/tests/conftest.py`, `backend/tests/test_app.py`, `docker-compose.test.yml`
- Modify: `backend/main.py`, `backend/requirements.in`, `backend/requirements.txt`, `backend/.dockerignore`, `docker-compose.yml`

**Interfaces:**
- Produces: `get_settings() -> Settings` (`mongo_details`, `jwt_secret`, `cookie_secure: bool`, `upload_dir`, 기본 `/data/uploads`)
- Produces: `get_db() -> AsyncDatabase` (FastAPI 의존성. 미설정이면 `AppError(503, "DB_NOT_CONFIGURED", ...)`), `close_db()`
- Produces: `AppError(status: int, code: str, message: str)`, `register_error_handlers(app)`
- Produces: pytest 픽스처 `client` (`httpx.AsyncClient`), `db` (테스트마다 컬렉션 문서를 비움)

- [ ] **Step 1: 브랜치 생성** — `git switch -c feature/backend-skeleton`. 사용자의 `main.py` 변경은 그대로 따라온다.
- [ ] **Step 2: 패키지 추가** — `requirements.in`에 `argon2-cffi`, `pyjwt`, `email-validator` 추가. `python:3.11` 컨테이너에서 `pip-compile --output-file=requirements.txt requirements.in` 실행 (기존 고정 버전 유지). `requirements-dev.txt`는 `-r requirements.txt`, `pytest`, `pytest-asyncio`.
- [ ] **Step 3: 테스트 환경** — `docker-compose.test.yml`: `test-mongo`(mongo:8)와 `test-runner`(`./backend` 빌드, `./backend:/app` 마운트, `MONGO_DETAILS=mongodb://test-mongo:27017/ax_test`, `JWT_SECRET=test-secret-at-least-32-bytes-long!!`, `UPLOAD_DIR=/tmp/uploads`). `conftest.py`는 호스트가 `test-mongo`가 아니면 즉시 실패하고, `pytest.importorskip("init_db")`로 스키마를 적용한다.
- [ ] **Step 4: 실패 테스트 작성** — `tests/test_app.py`:

```python
async def test_hello_still_works(client):
    assert (await client.get("/hello/hknu")).json() == {"message": "Hello hknu"}

async def test_validation_error_format_hides_input(client):
    res = await client.post("/auth/login", json={"email": "not-an-email", "password": "SECRET-INPUT"})
    assert res.status_code == 422
    assert res.json()["code"] == "VALIDATION_ERROR"
    assert "SECRET-INPUT" not in res.text
```
`/auth/login`은 Task 2에서 생기므로 두 번째 테스트는 Task 2에서 통과한다.

- [ ] **Step 5: 구현** — 위 인터페이스 구현. `RequestValidationError`, `HTTPException`, 처리되지 않은 예외(500 `INTERNAL_ERROR`)를 공통 형식으로 변환. `main.py`의 기존 경로와 주석 유지, lifespan에서 `close_db()`. `.dockerignore`에 `tests/` 추가. `docker-compose.yml` backend에 `JWT_SECRET: "${JWT_SECRET:-}"`, `COOKIE_SECURE: "${COOKIE_SECURE:-false}"`, `UPLOAD_DIR: /data/uploads`, 이름 있는 볼륨 `backend_uploads:/data/uploads` 추가.
- [ ] **Step 6: 확인** — `docker compose -f docker-compose.test.yml run --rm --build test-runner sh -c "pip install -q -r requirements-dev.txt && python -m pytest tests/test_app.py -k hello -v"` → PASS. `MONGO_DETAILS` 없이도 `/hello`가 동작.
- [ ] **Step 7: 커밋** — `git add` 위 파일들, `feat: 백엔드 앱 뼈대와 테스트 환경 추가`

### Task 2: 회원가입·로그인·프로필

**Files:**
- Create: `app/deps.py`, `app/services/__init__.py`, `app/services/auth.py`, `app/schemas/__init__.py`, `app/schemas/auth.py`, `app/routers/__init__.py`, `app/routers/auth.py`, `app/routers/profile.py`, `tests/test_auth.py`
- Modify: `backend/main.py`

**Interfaces:**
- Produces: `current_user(request, db) -> dict` (users 문서. 쿠키 이름 `access_token`, 실패 시 401 `UNAUTHORIZED`)
- Produces: `hash_password(str) -> str`, `verify_password(str, str) -> bool`, `create_token(user_id: ObjectId) -> str`, `decode_token(str) -> ObjectId | None`
- Produces: 픽스처 `login(client, email="a@test.com") -> dict` (conftest에 추가, 가입 후 사용자 응답 반환)
- 응답 사용자: `{id, email, creator_name, created_at, updated_at}`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_auth.py`:
  - `test_signup_sets_httponly_cookie_and_hides_hash`: 201, `set-cookie`에 `HttpOnly`, `SameSite=lax`, 응답에 `password_hash` 없음, DB 값은 `$argon2id$`로 시작
  - `test_email_is_normalized_and_duplicate_rejected`: `A@Test.com` 가입 후 `a@test.com` 재가입 → 409 `EMAIL_TAKEN`
  - `test_password_length_rules`: 7자, 129자 → 422
  - `test_login_failure_message_is_same`: 없는 이메일과 틀린 비밀번호 → 둘 다 401 `INVALID_CREDENTIALS`, 본문 동일
  - `test_logout_then_me_is_401`
  - `test_tampered_expired_and_deleted_user_tokens_are_401`
  - `test_missing_jwt_secret_returns_503`: `AUTH_NOT_CONFIGURED`
  - `test_patch_profile_changes_creator_name_only`: `email` 전송 → 422
- [ ] **Step 2: 실행** — `... python -m pytest tests/test_auth.py -v` → FAIL (경로 없음)
- [ ] **Step 3: 구현** — 스펙의 `/auth/*`, `/profile`. 비밀번호 8~128자, 이메일은 소문자로 정규화, `creator_name`은 공백 제거 후 1~50자. `DuplicateKeyError` → 409. 모든 스키마 `extra="forbid"`. 존재하지 않는 이메일도 해시 검증을 수행해 응답 시간 차이를 줄임. 쿠키 `max_age` 7일, `secure=settings.cookie_secure`.
- [ ] **Step 4: 실행** — `tests/test_auth.py`, `tests/test_app.py` 전체 PASS
- [ ] **Step 5: 커밋** — `feat: 회원가입과 로그인 API 추가`

### Task 3: 브랜드·상품 기본 정보

**Files:**
- Create: `app/schemas/common.py`, `app/schemas/brands.py`, `app/schemas/projects.py`, `app/services/ownership.py`, `app/services/brands.py`, `app/services/projects.py`, `app/routers/brands.py`, `app/routers/projects.py`, `tests/test_brands.py`, `tests/test_projects.py`
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `current_user`, `get_db`, `AppError`, 픽스처 `login`
- Produces: `to_api(doc: dict) -> dict` (`_id`→`id`, ObjectId·참조→문자열)
- Produces: `parse_object_id(value: str) -> ObjectId` (실패 시 404), `get_owned_brand(db, user_id, brand_id: str) -> dict`, `get_owned_project(db, user_id, project_id: str) -> dict`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_brands.py`, `tests/test_projects.py`:
  - `test_create_brand_defaults`: `{"brand_type":"new"}` → 201, `status=="draft"`, `keywords==[]`, `colors==[]`
  - `test_partial_patch_keeps_other_fields`: `description`만 수정해도 `name` 유지, `updated_at` 갱신
  - `test_null_not_allowed_fields_return_422`: `keywords`, `colors`, `status`, `brand_type`, `features`, `current_step`에 null → 422
  - `test_completed_requires_name`: 이름 없이·공백 이름으로 `completed` → 422 `NAME_REQUIRED`. 완료 상태에서 `name: null` → 같은 오류
  - `test_other_users_brand_is_404`: 조회, 수정, 삭제 모두. 잘못된 ID 형식도 404
  - `test_logo_must_belong_to_brand`: 없는 파일이나 다른 브랜드 파일의 `logo_asset_id` → 404
  - `test_delete_brand_blocked_by_project`: 409 `BRAND_IN_USE`, 상품 삭제 후 204
  - `test_project_requires_owned_brand`: `brand_id` 없음 → 422, 다른 사용자 브랜드 → 404
  - `test_project_defaults_and_price`: `status=="draft"`, `current_step=="basic"`, `features==[]`. `expected_price` 15000과 10**12 저장, -1과 `"1000"`, 2**63 → 422
  - `test_project_brand_id_cannot_be_patched`: 422
  - `test_list_projects_by_brand`: `?brand_id=`로 필터, 다른 사용자 상품은 안 보임
- [ ] **Step 2: 실행** → FAIL
- [ ] **Step 3: 구현** — 스펙의 필드 표 그대로. `name`, `description`, `target_customer`는 공백 제거 후 빈 값이면 null. `expected_price`는 `Int64`로 저장. 목록은 `created_at` 내림차순. PATCH는 `exclude_unset`으로 받은 필드만 저장. 삭제 차단의 파일 검사(`assets`)도 여기서 구현.
- [ ] **Step 4: 실행** → 전체 PASS
- [ ] **Step 5: 커밋** — `feat: 브랜드와 상품 기본 정보 API 추가`

### Task 4: 파일 업로드

**Files:**
- Create: `app/storage.py`, `app/schemas/assets.py`, `app/services/assets.py`, `app/routers/assets.py`, `tests/test_assets.py`
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `get_owned_brand`, `get_owned_project`, `parse_object_id`, `to_api`
- Produces: `save_file(asset_id: ObjectId, data: bytes) -> None`, `file_path(asset_id: ObjectId) -> Path`, `delete_file(asset_id: ObjectId) -> None` (저장 파일 이름은 asset `_id`. DB 필드를 늘리지 않음)

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_assets.py`:
  - `test_upload_png_saved_with_slide_fields`: 201, `source=="upload"`, `file_url=="/assets/{id}/file"`, `mime_type=="image/png"`
  - `test_download_returns_same_bytes_and_safe_headers`: `content-disposition: attachment`, `x-content-type-options: nosniff`
  - `test_too_large_file_rejected`: 2097153바이트 → 413 `FILE_TOO_LARGE`, 2097152바이트 → 201
  - `test_disallowed_and_fake_types_rejected`: PDF, `image/png`라고 속인 텍스트, UTF-8이 아닌 TXT, 빈 파일 → 422 `INVALID_ASSET`
  - `test_korean_and_traversal_filename`: `../../evil 고양이.png` 업로드 후 저장 폴더 밖에 파일 없음, 내려받기 정상
  - `test_project_must_belong_to_brand`: 다른 브랜드 상품 → 404
  - `test_other_users_asset_is_404`: 정보 조회, 내려받기, 삭제
  - `test_missing_file_on_disk_returns_404`: 파일만 지운 뒤 → `FILE_MISSING`
  - `test_delete_clears_logo_and_file`: 대표 로고 삭제 시 `logo_asset_id`가 null, 파일도 삭제
  - `test_delete_project_blocked_by_asset`: 409 `PROJECT_IN_USE`
  - `test_delete_brand_blocked_by_asset`: 409 `BRAND_IN_USE`
- [ ] **Step 2: 실행** → FAIL
- [ ] **Step 3: 구현** — `POST /assets`는 multipart `file`, `brand_id`, `asset_type`(logo/banner/product_image/document), 선택 `project_id`. `GET /assets?brand_id=` 필수. 형식은 내용으로 판별: PNG·JPEG·WebP 시그니처, 그 외 UTF-8 해독 가능한 TXT. `mime_type`은 판별 결과를 저장(클라이언트 값 불신). `await file.read(2097153)`로 크기 제한. `file_name`은 경로를 떼고 길이 255 제한. 파일 저장 후 문서 삽입, 삽입 실패 시 파일 삭제.
- [ ] **Step 4: 실행** → 전체 PASS
- [ ] **Step 5: 커밋** — `feat: 파일 업로드 API 추가`

### Task 5: 작업 조회와 OpenAI 키 연결 테스트

**Files:**
- Create: `app/schemas/openai_test.py`, `app/routers/jobs.py`, `app/routers/openai_test.py`, `tests/test_jobs.py`, `tests/test_openai_test.py`
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `current_user`, `get_db`, `parse_object_id`, `to_api`
- Produces: `check_openai_key(api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> dict`

- [ ] **Step 1: 실패 테스트 작성**
  - `test_jobs_list_only_mine`, `test_other_users_job_is_404`, `test_jobs_have_no_write_routes`: POST → 405
  - `test_openai_requires_login`: 401
  - `test_openai_success`: 가짜 전송으로 모델 2개 → `{"ok": true, "model_count": 2}`
  - `test_openai_invalid_key`: 업스트림 401 → `{"ok": false, "reason": "invalid_key"}`
  - `test_openai_timeout_and_upstream_error`: `reason`이 `timeout`, `upstream_error`
  - `test_openai_only_calls_models_endpoint`: 요청이 `GET https://api.openai.com/v1/models`, `Authorization: Bearer ...`
  - `test_openai_bad_key_format_is_422_and_hidden`: 한글, 개행, 빈 값 → 422, 입력값이 응답에 없음
  - `test_openai_key_not_stored_or_logged`: 호출 후 DB 전체 컬렉션과 `caplog`, 응답에 키 문자열 없음
- [ ] **Step 2: 실행** → FAIL
- [ ] **Step 3: 구현** — 키는 `SecretStr`, 형식은 공백 없는 출력 가능 ASCII 1~512자. 타임아웃 10초. 업스트림 응답 본문은 응답에 포함하지 않음. httpx 로거가 헤더를 남기지 않는지 확인.
- [ ] **Step 4: 실행** → 전체 PASS
- [ ] **Step 5: 커밋** — `feat: 작업 조회와 OpenAI 키 연결 테스트 추가`

### Task 6: 전체 확인

**Files:** 변경 없음 (문제 발견 시 해당 파일 수정)

- [ ] **Step 1: 전체 테스트** — `docker compose -f docker-compose.test.yml run --rm --build test-runner sh -c "pip install -q -r requirements-dev.txt && python -m pytest -v"` → 전체 PASS. 이후 `docker compose -f docker-compose.test.yml down -v`
- [ ] **Step 2: 금지 범위 확인** — `git diff --stat main...HEAD -- front backend/init_db.py` → 출력 없음
- [ ] **Step 3: 게이트웨이 확인** — Atlas에 쓰기가 생기지 않도록 컴포즈 오버라이드로 `MONGO_DETAILS`를 임시 로컬 Mongo로 지정해 띄우고 `http://localhost:8080/api/auth/signup`, `/api/brands`, 파일 업로드를 `curl`로 확인. `/docs`에 경로가 보이는지 확인.
- [ ] **Step 4: 사용자에게 보고** — `.env`에 `JWT_SECRET` 넣는 방법을 안내. `main` 병합과 push는 요청이 있을 때만.
