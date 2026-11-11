# 백엔드 기본 틀 설계

작성일: 2026-11-05 / 상태: 승인 대기 (2차) / 나중에 바뀔 수 있는 초안

## 목적

Atlas에 만든 컬렉션 5개를 기준으로, 실제로 저장되는 핵심 API와 앞으로 기능을 붙일
FastAPI 뼈대를 만든다. 흐름과 화면 구성은 프로토타입(`Ddeugowa_Offline_UX_Demo`)을 참고한다.
프론트와 DB 설계가 확정되지 않았으므로, 나중에 고치기 쉬운 것을 우선한다.

## 사용자가 정한 것

- 설계가 다르면 **DB 설계**가 기준, 프로토타입은 참고
- 회원가입·이메일 로그인
- 브랜드·상품 기본 정보 입력 (프로토타입 흐름 참고)
- OpenAI 키 입력은 Swagger(`/docs`)에서 연결을 테스트하는 용도
- 파일 업로드 포함
- 범위는 핵심 API만, 추후 수정 가능
- 작업은 main이 아닌 새 브랜치에서 진행

## 범위

**만드는 것**: 회원가입·로그인·로그아웃, 프로필, 브랜드 CRUD, 상품 CRUD, 파일 업로드, 작업 조회, OpenAI 키 연결 테스트

**만들지 않는 것**
- AI 생성 호출과 파이프라인 일체 (다른 조원 담당, 폴더도 만들지 않음)
- `front/` 수정
- 채팅, PLAN, 비용 계산·시나리오, 휴지통 (경로도 만들지 않음)

## 검토한 방식

| 방식 | 내용 | 결과 |
|---|---|---|
| A. 핵심 API만 | 실제 동작하는 API만 만들고 나머지는 추가할 때 만듦 | **선택.** 고칠 범위가 작음 |
| B. A + 501 경로 | 나머지 경로를 자리만 등록 | 프로토타입이 바뀌면 같이 고쳐야 해서 제외 |
| `main.py` 하나에 작성 | 파일 분리 없음 | 조원 작업과 충돌하기 쉬워 제외 |

## 구조 (Claude가 정한 것)

```
backend/
  main.py            앱 생성, 라우터 등록, 기존 연결 테스트 경로 유지
  init_db.py         변경 없음 (git 제외 상태 유지)
  app/
    config.py        환경변수
    db.py            AsyncMongoClient 연결
    errors.py        공통 오류 형식
    deps.py          현재 사용자
    routers/         auth, profile, brands, projects, assets, jobs, openai_test
    schemas/         요청·응답 모델
    services/        DB 처리와 규칙
    storage.py       파일 저장 (교체할 수 있게 분리)
  tests/
```

필드를 바꿀 때는 `schemas/`와 `init_db.py`만 고치면 되도록 나눈다.
`MONGO_DETAILS`가 없어도 서버와 연결 테스트 경로는 뜨고, DB를 쓰는 API만 503을 반환한다.

## API

게이트웨이를 거치면 앞에 `/api`가 붙는다.

| 메서드와 경로 | 동작 |
|---|---|
| `POST /auth/signup` | 가입 후 바로 로그인 (`email`, `password`, `creator_name`) |
| `POST /auth/login` | 로그인 |
| `POST /auth/logout` | 로그아웃 |
| `GET /auth/me` | 내 정보, 로그인 전이면 401 |
| `PATCH /profile` | `creator_name` 수정 |
| `GET·POST /brands` | 내 브랜드 목록, 생성 |
| `GET·PATCH·DELETE /brands/{id}` | 조회, 수정, 삭제 |
| `GET /projects?brand_id=` | 내 상품 목록 |
| `POST /projects` | 상품 생성 (`brand_id` 필수) |
| `GET·PATCH·DELETE /projects/{id}` | 조회, 수정, 삭제 |
| `GET·POST /assets` | 브랜드별 파일 목록, 업로드 |
| `GET·DELETE /assets/{id}` | 파일 정보 조회, 삭제 |
| `GET /assets/{id}/file` | 파일 내려받기 |
| `GET /jobs`, `GET /jobs/{id}` | 내 작업 조회 (생성·실행·취소 없음) |
| `POST /openai/test` | OpenAI 키 연결 테스트 |

### 공통 규칙

- **오류 형식**: `{"code": "NOT_FOUND", "message": "..."}`
- **ID**: 응답에서 `_id` 대신 문자열 `id`
- **권한**: 다른 사용자의 자료는 존재 여부를 숨기고 404. 상품과 파일에는 `user_id`가 없으므로 소속 브랜드의 `user_id`로 판단
- **삭제**: DB에 삭제 표시 필드가 없으므로 바로 지운다.
  - 브랜드: 상품이나 파일이 남아 있으면 409 `BRAND_IN_USE`
  - 상품: 연결된 파일이 남아 있으면 409 `PROJECT_IN_USE`
  - 파일: 브랜드의 대표 로고였다면 `logo_asset_id`도 비운다.

### 브랜드·상품 기본 정보

작성 중에 나눠서 저장할 수 있도록 `POST`로 초안을 만들고 `PATCH`로 일부 필드씩 저장한다.

| | 입력 필드 | 서버 기본값 |
|---|---|---|
| 브랜드 | `brand_type`(필수), `name`, `description`, `target_customer`, `keywords`, `colors`, `logo_asset_id` | `status=draft`, `keywords=[]`, `colors=[]` |
| 상품 | `brand_id`(필수), `name`, `description`, `target_customer`, `features`, `expected_price`, `current_step` | `status=draft`, `current_step=basic`, `features=[]` |

- `status=completed`로 바꾸려면 `name`이 있어야 한다.
- `logo_asset_id`는 그 브랜드의 파일만 지정할 수 있다.

**프로토타입에만 있어 넣지 않는 필드**: 브랜드 `craft_type`, `customer_note`, `must_keep`, `dislikes` / 상품 `goal`, `stage`.
또한 프로토타입은 브랜드 없이 상품을 만들 수 있지만, DB 설계에 따라 `brand_id`를 필수로 한다.

### 로그인 유지

- DB 컬렉션을 5개로 유지하기 위해 세션 컬렉션 없이 JWT를 쓴다.
- JWT는 `HttpOnly`, `SameSite=Lax` 쿠키로 전달한다. Swagger에서도 로그인 후 그대로 동작한다. 만료는 7일.
- 비밀번호는 Argon2 해시로 저장한다. 로그인 실패 메시지는 이메일 존재 여부를 드러내지 않는다.
- 서명 키는 `.env`의 `JWT_SECRET`에서 읽는다. 없으면 인증 API가 503을 반환한다.
- 쿠키의 `Secure` 속성은 환경변수로 켠다. 로컬 HTTP에서는 끈다.
- **한계**: 로그아웃은 쿠키만 지우므로, 토큰을 복사해 둔 경우 만료 전까지 유효하다. 강제 로그아웃이 필요해지면 세션 컬렉션 추가를 검토한다.

### 파일 업로드

- `POST /assets`는 multipart로 `file`, `brand_id`, `asset_type`, 선택 `project_id`를 받고 `source=upload`로 저장한다.
- 프로토타입과 같이 TXT·PNG·JPEG·WebP, 파일당 2MB까지 허용한다. 이미지는 실제 파일 내용도 확인한다.
- 저장소는 **Docker 볼륨의 로컬 디스크**이고 파일 이름은 서버가 무작위로 정한다. `storage.py`만 바꾸면 S3 등으로 옮길 수 있다.
- `file_url`에는 `/assets/{id}/file`을 저장한다.
- 내려받을 때 첨부 형식과 `X-Content-Type-Options: nosniff`를 붙인다.

### OpenAI 키 연결 테스트

- `POST /openai/test`에 `api_key`를 보내면 OpenAI의 모델 목록 API(`GET /v1/models`)를 한 번 호출해 결과를 알려준다.
- 생성 호출이 아니므로 파이프라인과 연결되지 않는다.
- 키는 **저장하지 않고** 로그와 오류 응답에도 남기지 않는다.
- 로그인한 사용자만 호출할 수 있다.
- 응답: 성공 여부와 모델 개수. 키 오류, 시간 초과는 구분해서 알려준다.

## 컬렉션

`init_db.py`와 Atlas 규칙은 **변경하지 않는다.** 슬라이드의 5개 컬렉션을 그대로 쓴다.
기존 샘플 문서 5개도 그대로 두며, 삭제는 사용자가 결정한다.

## 설정 변경

- `docker-compose.yml`의 `backend`에 `JWT_SECRET`, `COOKIE_SECURE` 환경변수와 업로드 볼륨을 추가한다.
- 사용자는 `.env`에 `JWT_SECRET`을 넣어야 한다.
- `requirements.in`, `requirements.txt`에 Argon2, PyJWT, 이메일 검증 패키지를 추가한다. 테스트 도구는 운영 이미지에서 분리한다.

## 조원에게 넘길 지점

- `ai_jobs`는 지금 조회만 한다. 작업 생성과 상태 변경은 파이프라인 담당 조원이 정한다.
- 저장된 키가 없으므로, 파이프라인에서 키를 어떻게 받을지는 조원과 정한다.

## 작업 방식

- 새 브랜치 `feature/backend-skeleton`에서 작업한다. `main`에 직접 커밋하지 않는다.
- 사용자의 `backend/main.py` 연결 테스트 경로와 주석은 유지한다.

## 테스트

- pytest로 API 전체를 검사한다. 임시 로컬 MongoDB 컨테이너만 쓰고 Atlas에는 접속하지 않는다.
- OpenAI 호출은 가짜 응답으로 검사하고 실제 키를 쓰지 않는다.
- 가입·로그인·로그아웃, 비밀번호 저장 형태, 다른 사용자 자료 차단, 기본 정보 저장, 완료 조건, 삭제 차단, 업로드 제한, 키가 응답에 남지 않는지를 확인한다.
- 게이트웨이 `/api` 경유 동작을 `docker compose`로 확인한다.
