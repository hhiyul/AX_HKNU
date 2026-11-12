# OpenAI 백엔드 송수신 구현 계획

기준일: 2026-11-12. 상태: **계획 작성 / 코드·테스트 구현 및 실행 NOT_RUN**. 이 문서는 구현 순서와 검증 목표이며 제품 구현 완료나 실서비스 통합 승인이 아닙니다. [사용자 기능 v0.2](../02-user-feature-spec/ai-pipeline-user-feature-spec-v0.2.md), [04 사용자 기능](../02-user-feature-spec/planning-output-user-spec.md), [모듈 명세](ai-pipeline-module-feature-spec-v0.1.md), [결정표](grill-feature-decisions.md)를 유지합니다.

## 1. 목표와 범위

팀원 시스템 통합을 기다리지 않고 Python 백엔드가 `OPENAI_API_KEY`를 서버 환경에서 읽고, 합성 텍스트로 OpenAI Responses 요청을 전송해 응답과 실패를 구분하는 경로를 구현·시험합니다. 03 대화와 04 문서 생성의 초기 모델은 각각 D-UF-109·GD-040의 `gpt-6-luna`입니다. 문서 생성·CK 적용·04 저장을 완성했다는 의미는 아닙니다.

이번에 요청받은 산출물은 **계획**입니다. 아래 단계·파일·명령은 구현 시 제안이며 지금 코드 작성, DB 변경, 키 읽기, 실호출, 테스트 실행 또는 배포를 수행했다는 뜻이 아닙니다. 세부 기술은 합의된 사용자 결과를 보존하는 범위에서 권장안으로 진행하고, 충돌·사용자 결과 변경이 발견되면 해당 결정과 명세를 먼저 갱신한 뒤 작은 단위로 조정합니다.

## 2. 실제 기준선과 현재 상태

| 구분 | 확인 결과와 영향 |
| --- | --- |
| 현재 작업 트리 | GitHub `feature/ai-agent-pipeline` 및 로컬 HEAD는 `39110d07219d53d50c6200e0236837f44f92250c`, 확인 시 작업 트리 clean. 이 문서를 작성한 뒤 HEAD 자체는 바뀌지 않음 |
| 요청된 `feature/backend-prototype` | GitHub 플러그인의 브랜치 검색은 결과 없음, 해당 ref의 커밋·파일 조회는 422/404. 원격 브랜치 삭제/권한 상태는 단정하지 않으며 **현재 원격 HEAD 미확인** |
| 로컬에 남은 원격 추적 ref | `origin/feature/backend-prototype`은 `f8a9575fea5eeee80c8e96048f8e15a86cbc5773`(2026-11-12 10:18 +09:00)이고 해당 커밋 객체는 GitHub에서 조회됨. 이 로컬 참조와 HEAD 사이 `backend/`의 최종 파일 diff는 0. 체리픽된 파일의 일치 증거이지 현재 원격 ref의 최신성 증거가 아님 |
| 기존 OpenAI 경로 | `backend/app/routers/openai_test.py`의 `POST /openai/test`는 로그인 후 **요청 본문의 키**로 `GET /v1/models`만 조회함. 생성 테스트나 서버 환경 키 사용이 아님. 기존 동작은 독립 기능으로 유지 |
| 환경과 의존성 | Python 3.11, FastAPI, Pydantic 설정, `httpx==0.28.1`, pytest/pytest-asyncio. `Settings`와 실행 compose는 현재 `OPENAI_API_KEY`를 전달·조회하도록 구성돼 있지 않음. `httpx`로 시작하면 SDK 추가 없이 기존 의존성 사용 가능 |
| 현재 pytest | `backend/tests/conftest.py`는 전체 세션에서 `test-mongo`와 `init_db.py`를 요구하고 DB fixture가 컬렉션을 비움. 현재 `backend/init_db.py`는 Git 추적 파일이지만 conftest의 옛 주석은 이를 반영하지 못함. Provider 단위시험은 이 하위 conftest 밖에 두어 DB 없이 실행 |
| 기존 API/데이터 | 여섯 `plan` 항목의 내용·정수 revision 및 조건부 저장, `projection`, `record`·`document` CRUD, 메시지 저장, 요청 결과 조회가 코드에 존재. 전체 snapshot·승인·동시성의 약속까지 검증된 것은 아님 |

OpenAI 공식 [모델 문서](https://developers.openai.com/api/docs/models/gpt-6-luna)는 `gpt-6-luna`의 Responses API 지원을, [Responses API 참조](https://developers.openai.com/api/reference/resources/responses/methods/create)는 요청·응답 형식을 설명합니다. 실제 계정의 모델 접근·한도·비용·보존 조건은 확인 전입니다. `store:false`는 모든 Provider 보존이 0이라는 약속이 아닙니다.

## 3. 이번 이터레이션의 기술 경계

**권장 아키텍처:** 합성 입력 → 요청 조립 → `httpx` 전송 포트 → 응답 검증·분류 → 호출자에게 결과. 서비스는 DB·FastAPI 요청·전역 설정 객체에 종속하지 않고 전송과 설정을 주입받습니다. 기존 FastAPI의 인증/오류 전달은 DB 없는 테스트 전용 ASGI 앱에서 서비스에 연결해 확인합니다. 실제 서비스 `main.py`에 미인증 생성 엔드포인트를 추가하지 않습니다. 추후 제품 API 연결은 인증·세션·승인 경계가 준비된 별도 단계입니다.

1. `OPENAI_API_KEY`는 서버 프로세스 환경에서 읽고, 미설정/공백이면 네트워크 호출 전 설정 오류로 반환합니다. 키나 Authorization·원본 요청/응답을 로그, 테스트 결과, 예외, DB, 응답, 문서에 남기지 않습니다. 가짜 키 fixture만 소스에 둡니다.
2. 전송은 기존 `httpx.AsyncClient`와 주입 가능한 `AsyncBaseTransport`를 사용합니다. HTTPX 자체 요청·JSON 직렬화·파싱은 실제 코드를 사용하고 테스트에서 네트워크만 `MockTransport`로 대체합니다. SDK 도입이 필요해지면 의존성·자동 재시도·파서의 변경을 별도 기록합니다.
3. 첫 요청은 `POST https://api.openai.com/v1/responses`, `gpt-6-luna`, 서버 환경 키, 합성 텍스트, 비스트리밍, `store:false`, `background:false`, `reasoning.effort:none`을 사용합니다. 이는 기존 03 M-01/06의 첫 전송 프로필을 이 연결 시험에 적용하는 권장안이며, 04의 최종 prompt·예산·도구 계약이 확정됐다는 뜻은 아닙니다. live 시험에는 작은 명시 출력 한도를 사용합니다. 실제 프로젝트 데이터·첨부·검색·도구·업무 저장은 포함하지 않습니다.
4. `completed` 응답의 실제 `output` 항목을 검증해 텍스트를 추출하고, 예상치 못한 구조·빈 텍스트·거절·incomplete·failed·전송 오류를 성공과 구별합니다. 텍스트는 output의 특정 배열 위치에 있다고 가정하지 않습니다. 거절은 생성 성공으로 표시하지 않고 incomplete의 부분 텍스트가 있더라도 완료와 구분합니다. 사용량은 있으면 확인된 값만 취급합니다.
5. 키 누락, 인증/권한, 429, 5xx, timeout/연결 오류, 200이어도 JSON/구조 오류, Provider 비완료를 분류합니다. 실행 결과 미확인과 호출 전 실패를 구별하며 생성 요청은 자동 재전송하지 않습니다. 오류 원문과 인증정보는 외부에 그대로 노출하지 않습니다. 내부 오류 이름과 최종 HTTP 상태는 구현 시 앱 오류 규약에 맞추되 이 문서는 공개 API 계약이 아닙니다.
6. 가짜 transport를 이용한 단위시험은 CI에서도 실행 가능하게 하고, 실제 모델 호출은 **별도의 opt-in 유료 연결 시험**으로 분리합니다. 환경변수 존재만으로 실행하지 않습니다. 기본 시험에서 실제 키·외부 네트워크·MongoDB를 사용하지 않습니다. 실호출은 별도 승인 후 합성 입력과 작은 출력 한도로 한 번 실행하고 자동 재시도하지 않습니다.

## 4. 작업 계획표

| 단계 | 구현 내용과 예상 위치 | 완료 조건 | 의존·상태 |
| --- | --- | --- | --- |
| P0 기준 고정 | 이 계획, 기존 13절 프로필, HEAD 및 대상 테스트 범위 검토. 새 baseline의 코드/명세 차이 표를 기준으로 선행 제품 결정 보존 | 선택 모델·환경 키·무DB 단위/유료 live 분리, 실제 owner 경계 확인 | 문서 작성 완료; 코드 NOT_RUN |
| P1 Provider 설정·DTO | `backend/app/` 아래 AI 전용 설정·요청/결과 DTO와 주입 가능한 비밀 취급 경계. 정확한 폴더·이름은 기존 관례에 맞춰 구현 | 환경 변수 누락·공백 안전 실패, 예외/표현/로그 키 미노출, 앱 기존 설정·`/openai/test` 회귀 없음 | 구현 대기 |
| P2 Responses 전송 | AI 전용 서비스/adapter에 고정 URL·httpx 전송·timeout·요청 직렬화·응답 파싱. 도구·DB·제품 라우터 비연결 | fake transport에서 실제 serializer/parser로 요청과 결과·예외를 검증. generation 자동 재전송 0 | P1; 구현 대기 |
| P3 백엔드 경계와 독립 시험 | `backend/tests_unit/` 등 `backend/tests/conftest.py`의 하위가 아닌 위치에 unit fixture와 테스트 전용 ASGI 연결. pytest를 명시 경로로 실행 | DB·Docker·키·네트워크 없이 요청→서비스→가짜 OpenAI→응답 전 경로 검증. 기본 앱에 임시 공개 endpoint 없음 | P1~2; 구현 대기 |
| P4 실연결 smoke | `backend/tests_live/` 등 별도 opt-in 시험. 환경 키는 프로세스 주입; 합성 요청, `max_output_tokens` 소규모, 호출 1회 | 실제 Responses 생성 및 읽을 수 있는 응답 또는 구체 실패 기록. 모델 접근·비용 발생 여부를 실제 관측에 따라 표시 | P1~3 통과 + 키·계정 접근 + **별도 유료 호출 승인**; NOT_RUN |
| P5 실제 사용자 기능 연결 | 03 인증·프로젝트 1:N Chat·컨텍스트·Field별 승인, 04 자동 라우팅·독립 문서·변경 감지·출력의 단계별 연결 | 아래 6절의 충돌과 owner 경계에 대한 제품·통합 계약 및 별도 인수 시험 통과 | P4와 별개의 후속. 통합은 지금 착수 조건이 아님 |

실제 라이브 smoke의 합성 프롬프트는 테스트 코드에 고정하고 출력 한도는 예를 들어 128토큰으로 제한합니다. 응답 텍스트의 정확한 문구 대신 완료 상태와 비어 있지 않은 텍스트, 실제 응답 모델/사용량이 제공된 경우의 유효성을 확인합니다. 테스트 한도는 제품의 8k 출력 예산을 바꾸지 않습니다. API 환경의 제약으로 incomplete면 실제 실패로 기록하며 임의 재호출하지 않습니다.

## 5. 실행 가능한 시험 설계와 판정

| 분류 | 시나리오 | 확인할 결과 |
| --- | --- | --- |
| 설정 | 키 누락·공백, 다른 설정은 기존 기본값 | HTTP dispatch 0, 구체적인 비밀 없는 오류. 기존 앱 생성·키 테스트 계약 유지 |
| 전송 | fake 키·합성 입력·주입 transport | POST 경로, 서버에서 온 인증 헤더, 모델·본문·출력 한도·옵션, 요청 1회. 키는 fixture에만 존재 |
| 성공 파싱 | message 전후 다른 output 항목, 여러 텍스트 조각, usage 누락 | 실제 구조를 순회, 허용 텍스트·상태만 전달. usage 누락은 미확인 |
| 실패 파싱 | refusal, incomplete, failed, 빈 출력, 비JSON, 필수 필드 누락·잘못된 타입 | 성공으로 꾸미지 않음. 미완료와 거절·형식 오류 분리 |
| 전송 실패 | 401/403, 429, 5xx, 연결 실패, 응답 중 timeout | 오류 범주와 발송 전/후 확인 가능 범위를 구분. 응답 상실을 미실행으로 추정하지 않음. 자동 재전송 0 |
| 비밀과 격리 | 합성 키가 포함된 예외·Provider error 및 caplog, 입력 검증 실패 | 응답·로그·오류 문자열에 키가 없음. 기본 테스트의 외부 전송 0, DB 접근 0 |
| FastAPI 연결 | 인증 의존성과 provider를 테스트 전용 앱에서 주입 | 합성 요청이 서비스까지 전달되고 성공·실패를 안전하게 반환. production 인증을 우회한 배포 엔드포인트를 만들지 않음 |
| live smoke | opt-in, 서버 환경 키, 합성 입력 | Responses 요청 1회와 응답 검증 또는 명시적 실패. 무키면 skip/설정 오류; live 미실행을 통과로 표시하지 않음 |

계획하는 기본 실행(구현 후 `backend`에서): `python -m pytest -q tests_unit`. `backend/pytest.ini`의 `testpaths=tests`가 있으므로 테스트 경로를 명시합니다. live는 별도 폴더·명령과 명시 opt-in으로 운영하며, 기존 `tests/conftest.py`를 우회하기 위해 프로덕션 DB 보호 장치를 약화하지 않습니다. 현재 compose test-runner에는 키가 전달되지 않습니다. Docker에서 live를 실행한다면 별도의 명시적 환경 전달·분리 실행 방법을 구현 시 확인하고, compose 설정 출력·명령줄에 키 값을 쓰지 않습니다. 기존 통합 테스트는 일회용 `test-mongo`에서 별도로 실행합니다.

## 6. 새 백엔드와 제품 계약의 차이: 후속 통합 blocker

| 현재 코드 관찰 | 승인된 사용자 계약 | 다음 처리 |
| --- | --- | --- |
| `services/chat.py`는 프로젝트별 단일 대화와 프로젝트 단위 ordinal/설정을 전제 | GD-027~034: 프로젝트 1:N 독립 세션과 세션 귀속 | 03/04 실제 연결 전 식별·저장·삭제·동시 실행 설계. 하나의 가짜 세션을 실제 1:N 지원이라고 표시하지 않음 |
| `items.py` plan state는 `UNSET/SKIPPED/WRITTEN`, 초안·확정 구분 없음 | 기존 CK의 `UNSET/DRAFT/DECIDED`, Field별 선택 승인 | CK별 revision/CAS 존재는 인정. 상태와 승인·출처는 별도 정합·이행 필요. `WRITTEN`을 `DECIDED`로 임의 매핑 금지 |
| `record`는 답변별 별도 글 최대 200개, 메시지 재검수 연결 | UF-AI-16: 프로젝트당 하나의 추가 기록; AI 제안은 기존 여섯 CK에 반영 | 두 자원의 의미·이행을 정합. record를 04 다중 문서나 새로운 CK 정본으로 간주하지 않음 |
| `document` CRUD·편집 draft·revision과 메시지 연결이 존재하나 manifest/참조 revision·4형식 export는 이 경로에서 확인되지 않음 | Q4·Q10·Q13~15·Q19, GD-035~039 | 메시지 링크와 독립 Document 수명, 전체 재생성 새 ID·변경 감지·출력 및 승인 검증 필요 |
| projection은 각 섹션을 읽고 revision 문자열의 해시를 만들며 브랜드 revision에는 `updated_at` 사용 | 생성 시작 선택 자료의 일관된 고정·실제 입력 출처와 revision | 순차 조회·짧은 hash만으로 원자적 snapshot이나 변경 누락 방지 보장하지 않음. owner 확인/경합 시험 필요 |
| 프로젝트 휴지통과 영구 삭제가 분리되고, 영구 삭제는 진행 중 job이 있으면 거부 | GD-020·026의 진행 중 프로젝트 삭제·늦은 결과 차단 및 소유 파일 정리 | 제품 의미·실제 삭제 경계와 race를 정합. 현재 코드가 모든 삭제 경합을 해결했다고 가정하지 않음 |
| 저장 요청 결과 기록·`Idempotency-Key`가 존재하나 요청 기록과 업무 쓰기가 별도 동작 | 원 요청 결과 확인, 안전한 재전송 경계 | 실제 충돌·응답 유실·중복 키/다른 payload·영구 삭제 경쟁을 계약 시험. 404나 현재 값만으로 미반영 추정 금지 |

이 차이는 P1~P4의 **합성·무저장 Provider 연결 시험을 막지 않습니다.** 실제 프로젝트 자료를 연결하거나 제품 기능 완료를 주장하는 단계에서는 해당 경계를 해소해야 합니다. 이전 [구현 준비 대장](implementation-readiness-contracts.md)의 `c35f32e` 소스 관찰은 역사적 기준이며 현행 소스의 부재 증거가 아닙니다.

## 7. 이터레이션 종료 조건

- P1~P3: fake transport·실제 serializer/parser·테스트 전용 ASGI 경계가 비밀/DB/네트워크 없이 통과하고 기존 앱 경로를 손상하지 않음.
- P4: 별도 승인한 경우에만 live 1회 결과와 모델·실행 시각·성공/실패·관측된 사용량을 비밀·본문 없이 기록. 미실행이면 NOT_RUN. 실연결 성공은 AI 품질·승인·데이터 일관성 또는 03/04 제품 인수가 아님.
- P5: 사용자 정본 및 GD/Q를 유지하는 기능별 인수 시나리오·격리 테스트·owner 계약이 준비될 때만 통합. `OPENAI_API_KEY`를 사용할 수 있다는 사실을 팀원 API·권한·DB 계약의 완료로 해석하지 않음.
