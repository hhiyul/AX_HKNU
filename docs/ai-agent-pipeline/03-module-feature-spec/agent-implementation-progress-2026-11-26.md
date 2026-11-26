# AG-P0~5 구현·검증 기록

기준일: 2026-11-26. 사용자 승인 범위: 기존 MVP 안에서 Phase 0~5 코드 구현, 단위·통합 테스트, 실제 UAT 직전까지. 실제 사용자 UAT·배포·커밋/푸시는 이번 기록의 완료 대상이 아니다.

후속 기록: 같은 날 추가 승인된 **GPT 브라우저 화면 검증과 실제 유료 모델 호출**은 [브라우저 검증 기록](agent-browser-uat-2026-11-26.md)에 정리했다. 아래의 화면 미실행·실제 provider 미사용은 최초 구현 종료 시점의 기록이다. 후속 검증에서는 핵심 흐름을 관찰하고 오류를 수정했으며, 백엔드 467개·프런트 6개 테스트가 통과했다. 긴 답변 품질 문제와 미검증 인수 항목은 후속 기록에 별도로 남겼다.

## 기준선

- 로컬 `feature/ai-agent-pipeline`, 시작 HEAD `393e6ec`, clean. GitHub 연결 도구에서 `hhiyul/AX_HKNU` 저장소 확인.
- Figma AI-11·13, DOC-13 및 PRD-05~09·DOC-02의 현재 기능명세 재조회. 기존 GD-046~070과 첫 출시 범위 유지.
- 최초 Docker 테스트는 샌드박스의 엔진 접근 거부로 시작하지 못함. 격리 Compose 명령의 실행 권한을 받아 같은 `test-mongo`로 재실행.
- 최초 프런트 build는 로컬에 `react-markdown`이 설치되지 않아 실패. `npm ci`로 기존 잠금 파일의 의존성을 설치한 뒤 재검증. 패키지 버전 변경 없음.
- 백엔드 기준선: **445 passed in 35.54s**. 명령: `docker compose -f docker-compose.test.yml run --rm test-runner sh -c "pip install -q -r requirements-dev.txt && pytest -q"`.
- 프런트 기준선: lint 오류 0, 기존 경고 13개. build 성공(318 modules). 실제 UI UAT 아님.

## 구현 결과와 검증 범위

실제 앱의 `backend/main.py` → 기존 인증/Workspace → MongoDB 경로에 구현했다. P5 별도 서버나 SQLite를 제품 정본으로 사용하지 않는다. 기존 대화·문서·일정 데이터를 삭제하거나 자동 완료 처리하는 이행은 없다.

| Phase | 구현한 동작 | 자동 검증 상태 |
| --- | --- | --- |
| AG-P0 | 현재 브랜치·Figma·실제 앱 진입점·회귀 기준선 확인 | PASS |
| AG-P1 | 대화별 참고 유형/개별 제외/직접 선택한 과거 메시지, revision 충돌, 출처·파생 이력/요약 제외, 참고 설정과 출처 화면 | 단위·API 통합 PASS. 화면 실행 확인 미완료 |
| AG-P2 | 검색 페이지와 원문 구간 읽기, 일정 날짜/완료 상태 조회, 파일 읽기 상태, 브랜드 공통·제품·거래처 Mongo 정본 | 단위·API 통합 PASS. 실제 모델의 자료 선택은 UAT 대상 |
| AG-P3 | 일반 채팅 할 일 후보, 공통 확인 화면, 날짜·목적 선택, 건별 등록 결과/조회, 기록 후보 확인 저장 | 동시성·부분 실패·응답 유실 모의 API PASS, 프런트 결과 병합 단위 PASS |
| AG-P4 | 답변 한 건→제품 선택→전용 문서, 제외 승계, 항목별 선택 복원·연속 이력·같은 문서 저장본, 비교 후 임시 반영 | 모의 provider 입력과 실제 Mongo 결과 PASS. UI 인수 미실행 |
| AG-P5 | 모델 없는 저장본 검사, 명시 AI 점검, 새 run으로 이어서 생성, 기존 중지/사용량/실행 잠금 회귀 | 단위·API 통합 PASS. DOC-POL-01 최종 완료 정책은 OPEN |

Phase의 **코드 연결·자동 검증**과 사용자 흐름 인수는 별개다. AG-P6 실제 모델·실제 사용자 UAT와 배포는 실행하지 않았다. 기존 명세와 Grill Decisions를 새로운 제품 결정으로 덮어쓰지 않았다.

## 실행 기록

| 단계 | 실제 결과 | 범위·제한 |
| --- | --- | --- |
| 참고 범위·출처·조회 도구 첫 회귀 | 50 passed in 14.91s | 신규 출처 제외/요약 재유입, 날짜 조건 일정 조회, 내부 검색과 기존 chat/conversation/context/attachment |
| 일반 채팅 후보 도구 연결 후 회귀 | 49 passed in 14.39s | agent capabilities·tasks·records·sources·chat. 실모델/UAT 아님 |
| 확인 등록·부분 실패/응답 유실 회귀 | 20 passed in 9.81s | 실제 task owner와 격리 MongoDB |
| 초기 전체 회귀 | 453 passed in 35.86s | 기존 445개 + 신규 기능 8개 당시 스냅샷 |
| 제품/거래처·문서 연결·제외·파일 회귀 | 57 passed in 8.17s | 신규 정본과 항목 맥락 포함 |
| 파일 기본 동작/이미지 안내 정리 | 35 passed in 7.05s | 명시 TXT 원문 기본 읽음, 미독 이미지 표시, OFF 설정 회귀 |
| 최종 기능 코드 전체 회귀 | 463 passed in 42.42s | 원문 기본 읽기 변경 후 기존 인덱스/삭제/내보내기/실행 회귀 포함 |
| 마지막 인수 반례 보강 | 20 passed in 5.15s | 신규 에이전트 테스트 전체. 여섯 번째 문서의 잘린 구간 이후 원문, 미승인 일정 완료 도구 차단 포함 |
| 프런트 단위 | 3 passed | 실패 건 재시도 시 성공 결과 유지, 늦은 unknown 응답의 성공 상태 훼손 차단, 휴지통 일정 연결 유지 |
| 프런트 lint | 오류 0 / 기존 파일 경고 11 | 기준선 13개. 추가 컴포넌트의 신규 경고 없음 |
| 프런트 build | PASS, 326 modules | 주 번들 520.11 kB, gzip 157.79 kB. Vite의 500 kB 경고가 남음 |

중간 실패도 수정 후 회귀 확인했다. 첫 패키지 설치 재시도는 패키지 인덱스 JSON 응답 오류였으며 테스트 자체는 시작하지 못했다. 자료가 사라진 경우의 기존 404 계약, 거래처 날짜 직렬화, TXT 기본 읽기 전환으로 달라진 구버전 테스트 기대, 읽지 않은 이미지 안내를 정리했다. 출처를 읽었다고 주장하지 않는 반례는 유지했다.

최종 통합 스냅샷: **465 passed in 38.24s**. 기존 445개와 신규 에이전트 계약 20개를 함께 실행했다. skip·실제 모델 호출 없음.

## 실제 저장/API 계약

인증 주체와 소유 프로젝트는 기존 Workspace가 결정한다. 아래 경로는 `/projects/{project_id}` 아래이며, 요청의 소유 ID를 모델이 넓히지 못한다.

| 경로 | 계약 |
| --- | --- |
| `GET/PUT /conversations/{id}/scope` | `scope={kinds,excluded,message_ids}`와 `revision`. 이전 이력을 지우지 않고 다음 요청부터 적용. 다른 대화는 선택한 메시지만 입력 |
| `GET /ai-sources`, `GET /ai-sources/read` | 검색 `query/offset`, 원문 `key/offset`. 목록 20개, 원문 20,000자 단위와 `next_offset`. 목록 노출은 `metadata`, 원문은 `read/partial`. 읽지 못함은 `unavailable/unsupported/disabled/failed` |
| 모델 `search_sources/read_source/query_tasks` | 같은 SourceReader 및 owner 조회. `query_tasks`는 예정 날짜 기준이며 완료 처리 날짜로 가장하지 않음. 완료 시점 분석/기존 일정 변경 도구 없음 |
| `POST /tasks/registrations`, `GET /tasks/registrations/{request_id}` | 확인 payload hash 고정, 건별 `registered/already_linked/failed/unknown`. 한 후보의 실제 일정 고유 인덱스 유지. 조회로 원 요청 결과를 복구하며 성공 건은 재시도에서 보존 |
| 기존 `POST /items/record`, `GET /requests/{key}` | 사용자 확인한 제목/본문 저장. 원 대화·메시지와 파생 출처 보존. 응답 미확인은 조회 우선. 기록 저장은 CK/홈 메모 수정 권한이 아님 |
| `GET/POST /products`, `PUT /products/{id}` | 실제 제품 정본. 초안/등록 완료, 사진·의도·재료·제작 과정·옵션·가격·최초 수량. 원가/기본 판매가/옵션 판매가 분리. 옵션 가격 null은 기본가 승계, 0은 명시한 0원. 예상 가격도 등록 가능 |
| `GET/POST /contacts`, `PUT /contacts/{id}` | 사용자 직접 관리하는 거래처 정본. 현재 프로젝트 또는 연결 브랜드 공통. 다른 프로젝트의 전용 거래처 자동 조회 금지. 에이전트에는 읽기만 제공 |
| 기존 `POST /docs` 확장 | `origin_message_id/product_ids`. 등록 완료 제품만 snapshot. 선택 답변 한 건은 첫 항목의 제거 가능한 참고. 전체 대화 입력·본문 자동 적용 없음 |
| `GET/PUT /docs/{id}/sections/{sid}/assist-context` | 항목별 references/scope/revision. 다른 항목에 선택 복사 없음. 원 대화의 제외를 승계하며 무관한 과거 메시지 선택은 복사하지 않음 |
| 기존 항목 `/assist`, `/apply` | 현재 대상·같은 항목 요청/답변·같은 문서 저장 항목·명시 참고. 다른 항목 draft/도움 이력 제외. 적용 전 저장/초안 revision 확인, 임시 입력만 변경 |
| `GET /docs/{id}/inspection` | 저장 버전의 필수 공백/확인 필요/미검토/임시 입력/제품 정본 변경·접근 불가 검사. 모델 0회, 본문·검토 완료 상태 변경 없음 |
| 항목 `/assist`의 `action=check` | 사용자가 명시한 AI 점검. 기준 revision과 의견만 저장하며 적용 가능한 candidate를 만들지 않음 |
| 기존 `POST /runs`의 `continuation_of` | 종료가 확인된 부분 답변에서 새 run. 원 요청·허용된 부분 답변·현재 scope로 남은 작업만 요청. 원 부분 답변 불변. UNKNOWN 자동 재전송 금지 |

제품의 초기·제작·기존 판매 수량은 등록 시 한 번 확정하고 재편집에서 다시 반영하지 않는다. 문서 생성은 선택 제품의 당시 값을 저장하며 이후 제품 가격 변경으로 기존 문서 내용을 덮어쓰지 않는다. 반복 판매/재고 운영·정산 쓰기는 추가하지 않았다.

### 파일과 출처

- TXT/MD/DOCX 원문 읽기는 기본 ON이며 `AI_READ_ATTACHMENTS=false`로 끄면 미독으로 알린다. 업로드 가능한 PDF의 AI 원문 읽기를 새로 지원했다고 주장하지 않는다.
- 채팅 텍스트 파일은 최초 10개/파일당 50,000자 구간과 미독/부분 상태를 알리고, 추가 원문 도구는 페이지를 이어 읽는다. 상한 밖 파일을 모두 읽었다고 표시하지 않는다.
- 항목 도움은 명시한 텍스트 파일을 구간 조회로 읽되 100,000자를 넘으면 자료 분할을 요청한다. 지원되지 않거나 읽기에 실패한 명시 자료는 조용히 누락하지 않는다.
- PNG/JPEG/WebP는 설정이 켜져 있을 때 이미지 입력으로 보낸다. 한 요청 최대 4장. 항목의 명시 이미지와 선택 제품 사진도 같은 이미지 계수 경로를 사용하며 실제 읽은 수를 구분한다.
- 출처 안내는 목록 확인/읽음/일부 읽음/미지원/실패를 구분한다. 원본 확인과 파일 다운로드를 제공한다. 답변 당시 revision/구간과 현재 원본이 다를 수 있음을 안내한다.
- 제외된 출처가 섞인 기존 답변·요약·선택 메시지를 분리 검증할 수 없으면 그 단위를 후속 입력에서 뺀다. 출처 없는 옛 이력도 제한된 scope에서는 보수적으로 처리하며 저장된 대화는 보존한다. 사용자가 직접 작성한 새 요청의 출처를 AI가 읽은 전체 프로젝트 출처로 덮어쓰지 않는다.

## ACAP 연결

이 표의 PASS는 **모의 provider + 실제 API/격리 MongoDB의 계약 증거**다. 제품 인수나 실제 모델의 자연어 판단 PASS가 아니다.

| ACAP | 자동 검증 근거 | 남은 실제 확인 |
| --- | --- | --- |
| 01~02 | 오래된 기록 페이지/원문, 여섯 번째 문서와 20,000자 이후 원문 테스트 | 모델의 추가 탐색 선택·부분 결과 설명 |
| 03~04 | 30개 밖 일정 조건 조회, `scheduled_date` 명시, 기존 일정 owner 테스트 | 한국어 상대 날짜·완료 시점 미제공 안내 |
| 05 | 브랜드 공통 연락처 빈 값과 실제 원문, 다른 프로젝트 전용 자료 차단 | 실제 연락처 표현과 오류 UI |
| 06 | scope CAS/0개, 제외된 요약·답변·파생 기록·삭제 원문·continuation | 화면 선택·재방문 |
| 07~11 | 후보 무저장, 수정 확인값, 목적 0개/복수, 동시성, 부분 실패, 응답 유실, 휴지통·현재 완료 상태 | 여러 탭·날짜 입력·등록 결과 화면 |
| 12 | 기존 AI record 테스트 + 출처 연결 + 결과 조회 UI | 저장 응답 유실의 브라우저 조작 |
| 13~14 | 선택 답변 한 건, 제품 초안 차단/당시 값 보존, 항목 이력·선택·다른 draft 격리 | 메뉴→제품 선택→비교→반영의 실제 화면 |
| 15 | 첨부·이미지 회귀, 항목 지정 파일, 부분 읽기/기본 ON/OFF | 실제 모델 원문·이미지 활용 및 실제 사용량 |
| 16 | 기본 검사 모델 호출 0회, AI 점검 candidate 없음/적용 차단, 기존 실패 회귀 | 실제 점검 의견의 품질 |
| 17 | continuation 새 run/원문 보존/출처 재검사, 기존 중지·취소·UNKNOWN·사용량 테스트 | 실제 provider 지연·재접속 |
| 18 | 미승인 `complete_task` 실행 실패/상태 불변, 프로젝트·자료 격리 | 자연어 관리 경로 안내 |

## 데이터·운영 준비

- `init_db.py`에 products/contacts/task_registrations 검증 규칙과 조회 인덱스를 추가했다. 서버 시작도 새 조회 인덱스를 생성한다. 기존 tasks의 후보 고유 인덱스를 계속 사용한다.
- 대화 scope, source manifest, 문서 origin/product snapshots/assist context, 일정 목적·출처는 선택 필드 확장이다. 옛 문서는 독립 문서로 읽고, 옛 일정의 목적은 빈 목록, 문서 후보 목적은 기존 template으로 해석한다.
- 프로젝트 영구 삭제의 기존 cascade에 새 프로젝트 소유 정본/등록 결과를 포함했다. 브랜드 공통 거래처는 브랜드 삭제 경로와 연결했다. 실제 사용자 데이터의 이행/삭제는 실행하지 않았다.
- task registration 결과에 새 TTL을 임의로 추가하지 않았다. 운영 보존 기간·백업/복구와 배포 환경 인덱스 설치 확인은 별도 운영 인수 대상이다.
- 테스트는 `test-mongo`만 사용했다. 모의 UI 서버도 localhost 전용으로 띄웠다가 종료했다. 운영 서버·실제 계정 데이터·실제 provider를 사용하지 않았다.

## 화면 확인과 UAT 직전 남은 일

인앱 브라우저에서 `127.0.0.1:15173` 접근이 `ERR_CONNECTION_TIMED_OUT`으로 실패했다. 로컬 Edge 복구도 최소화된 창의 상태 캡처 오류로 실패했다. 따라서 화면 클릭 흐름·초점·레이아웃·여러 탭 동작의 실제 관찰 결과나 스크린샷 증거는 없다. 프런트 lint/build/단위 테스트를 UI 인수로 바꾸어 기록하지 않는다.

`backend/tests/ui_smoke_server.py`는 이 검사를 재현하기 위한 모의 provider 서버다. `test-mongo` 이외의 DB 호스트에서는 실행을 거부한다. 다음 UAT 준비 시 이 모의 화면 점검을 먼저 완료하고, 실제 모델 UAT는 별도로 진행한다.

1. 참고 범위 변경과 재방문, 명시한 과거 메시지 선택, 원본 열기/다운로드를 확인한다.
2. 날짜 미정 후보 취소, 일부 선택·목적 0개/복수, 응답 유실 뒤 결과 조회를 화면에서 확인한다.
3. 제품 등록·예상 가격·제품 선택→문서 이동, 참고 해제/교체, 항목 변경 중 늦은 후보, 비교·임시 반영·별도 저장을 확인한다.
4. 기본 검사/직접 검토/명시 AI 점검을 구분하고, 현재 저장본 PDF/DOCX 및 실제 변환 환경을 확인한다.
5. 실제 provider로 검색·파일·한국어 날짜·중지·재접속·이어서 생성·토큰 계수를 확인한다.

AG-OPEN-01 기존 일정의 채팅 변경 권한, AG-OPEN-02 반복 운영, DOC-POL-01 핵심 원가 최종 완료 분기는 계속 OPEN이다. 해당 미결 정책을 구현·인수 완료로 선언하지 않는다. 견적 미기록은 대화·작성·저장·검토 전 다운로드를 막지 않는다.

## 재현 명령

```powershell
# 저장소 루트 — 실제 서비스 DB로 대체하지 않는다.
docker compose -f docker-compose.test.yml run --rm test-runner sh -c "pip install -q -r requirements-dev.txt && pytest -q"

# front 디렉터리
npm ci
npm test
npm run lint
npm run build

# 선택: 모의 UI 확인. 별도 터미널에서 실행하고 검사 후 종료한다.
docker compose -f docker-compose.test.yml run --rm -p 127.0.0.1:18091:8000 test-runner python tests/ui_smoke_server.py
# front 디렉터리의 PowerShell
$env:AX_API_PROXY_TARGET='http://127.0.0.1:18091'
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 15173 --strictPort
```

## 최종 확인

최종 전체 회귀 **465 passed**, 프런트 단위 **3 passed**, lint 오류 0·기존 경고 11, build PASS. 한국어 문서 UTF-8 및 `git diff --check`를 최종 확인한다. 커밋·푸시는 하지 않는다.

완료한 범위는 AG-P0 기준선과 AG-P1~5의 코드·단위/API 통합·프런트 정적 검사다. 화면 관찰은 도구 연결 실패로 미완료이며, 실제 사용자·모델 인수는 AG-P6에 남긴다. ‘제품 인수 완료’ 또는 ‘배포 완료’로 해석하지 않는다.

추천 커밋 메시지: `feat(ai): integrate scoped agent workflows through AG-P5`
