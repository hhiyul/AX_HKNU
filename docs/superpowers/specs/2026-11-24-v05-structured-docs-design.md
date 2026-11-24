# v05 설계: 항목형 전용 문서 · 항목별 AI · 할 일과 일정 · 계정 홈

- 작성일: 2026-11-24
- 브랜치: `feature/backend-v05` (`feature/backend-prototype`의 `09bad55`에서 시작)
- 기준 문서: 「AX STUDIO 서비스 구조 & MVP 기획서 — 인터뷰 반영 v2」(2026-11-23). 아래에서 "기획서"
- 목적: 11/26 UT에서 기획서의 사용자 과업 4개를 백엔드가 받칠 수 있게 한다.

## 기획서에서 그대로 가져온 원칙

- 최우선은 펀딩 상세페이지·사업계획서 두 전용 문서의 항목별 작성이다. 두 문서는 같은 편집 기능을 쓴다.
- AI 답변은 원문을 자동으로 바꾸지 않는다. 후보는 본문과 따로 있고, 적용하면 임시 입력에만 들어간다. 저장과 검토 완료는 사용자가 따로 한다.
- AI에는 사용자가 고른 참고 정보만 보낸다. 대화 전체, 다른 프로젝트, 첨부 원본을 자동으로 보내지 않는다. 첨부 원본의 자동 분석은 하지 않는다.
- 할 일 후보는 확인 전에는 일정이 아니다. 확인한 것만 일정에 등록하고, 같은 제안은 다시 등록되지 않는다. 일정에서 원래 문서 항목으로 돌아갈 수 있다.
- 문서 검토 완료, 업무 완료, 사업 성과는 서로 다른 정보다.
- 기존 기능(6개 기획 항목, 정리 내용, 평문 산출물, 자유 AI 대화, 경제성, 파일)은 그대로 둔다.

## 범위

| 기획서 P0 | v05 백엔드 |
| --- | --- |
| 공통 항목형 편집 + 두 작성 틀 | 작성 틀 2개(코드에 고정, 버전 1), 문서 생성·목록·휴지통, 항목 임시 입력·저장·검토 완료, 진행도, 마지막 항목, 미리보기·md/txt/docx/pdf |
| 실제 AI의 항목별 작성 도움 | 항목별 "초안 만들기/다듬기" 실행, 고른 참고만 전송, 후보 저장, 적용(버전 확인), 대화 기록 연결 |
| 할 일 제안·확인·일정 연결 | 일정(할 일) 컬렉션과 API, AI 할 일 후보, 확인 후 등록, 같은 제안 중복 방지, 항목 복귀 정보 |
| 홈 복귀·상태 요약 | 계정 홈: 최근 문서·오늘 할 일·기한 지난 일·진행 중 문서·규칙 기반 추천, 브랜드 범위 |

넣지 않는 것: 통합 검색(P1), 백업·가져오기(백엔드는 서버 저장이라 브라우저 백업이 없다), 외부 양식·제출, 이미지 편집.

## 데이터

### 작성 틀 (코드: `app/services/templates.py`)

`funding` v1: F01 작품·프로젝트 소개(필수), F02 고객과 구매 가치(필수), F03 제작 배경·브랜드 이야기(선택), F04 특징·구성·사용 장면(필수), F05 리워드·가격·수량(필수), F06 제작·배송 계획(필수), F07 사용·관리·이용 안내(선택), F08 위험 요인·남은 확인(필수). 필수 6개.

`business_plan` v1: B01~B08 모두 필수(사업 개요·목표, 고객·문제·필요, 상품·서비스와 차별점, 시장·경쟁·근거, 수익 구조·비용·가격, 홍보·판매 실행 계획, 운영 역량·자원·추진 일정, 성과 목표·위험·검증 계획). 필수 8개.

항목마다 제목, 작성 질문, 작성 가이드(AI가 지킬 것 포함), 필수 여부. 문서는 만들 때의 `template_version`을 고정해 뒤에 틀이 바뀌어도 기존 문서의 필수 개수가 바뀌지 않는다.

### 컬렉션 (Claude가 정함)

- `structured_docs`: `user_id, project_id, title, template_id, template_version, last_section_id, deleted_at, revision, created_at, updated_at`. 기존 평문 산출물(`items` kind=document)과 따로 둔다. 평문 문서는 기존 편집기 그대로다.
- `doc_sections`: `user_id, project_id, doc_id, section_id, text, draft, draft_revision, revision, reviewed_revision, material_ids, updated_at`. 문서를 만들 때 틀의 항목을 모두 만든다(빈 본문, revision 0). (doc_id, section_id) 유일.
- `tasks`: `user_id, project_id, title, date(YYYY-MM-DD, 필수), note, done, source_doc_id, source_section_id, source_proposal_id, deleted_at, revision, created_at, updated_at`. 같은 사용자의 `source_proposal_id`는 하나만.
- `messages`에 선택 필드: `purpose`(`document_assist`, `task_proposal`), `target_doc_id`, `target_section_id`, `request_id`(실행 id), `candidate`, `proposals`.

### 항목 상태 (기획서 그대로)

- 미작성(`UNWRITTEN`): 저장 본문이 비어 있다. 화면을 열거나 AI를 부르는 것만으로 바뀌지 않는다.
- 작성 중(`WRITING`): 저장 본문이 있고 지금 revision을 확인하지 않았다. 임시 입력이 있으면 `has_draft`.
- 검토 완료(`REVIEWED`): 비어 있지 않은 저장 본문의 지금 revision을 사용자가 확인했다(`reviewed_revision == revision`). 본문·자료 연결을 다시 저장하면 작성 중으로 돌아간다.
- 진행도: 확인한 필수 항목 수 / 필수 항목 수. 선택 항목은 따로 센다.

## API (모두 로그인·내 프로젝트 확인)

- `GET /templates`
- `GET·POST /projects/{id}/docs`(생성은 `Idempotency-Key`로 중복 클릭 한 번), `GET·PATCH·DELETE /projects/{id}/docs/{doc_id}`, `POST .../restore`, `GET ...?status=trash`
- `PUT .../sections/{sid}/draft`(임시 입력, `draft_revision`), `DELETE .../sections/{sid}/draft`, `PUT .../sections/{sid}`(저장, `revision`), `POST .../sections/{sid}/review`(검토 완료, `revision`)
- `GET .../preview`(저장 본문을 목차 순서로 합친 글 + 저장 안 한 임시 입력이 있는지), `GET .../export/{md|txt|docx|pdf}`
- `POST .../sections/{sid}/assist`(202, 실행 id) → 후보는 답변 메시지의 `candidate`. `POST .../sections/{sid}/apply`(후보를 임시 입력에 적용), `GET .../sections/{sid}/messages`(이 항목의 AI 기록)
- `POST .../sections/{sid}/task-proposals`(202) → 답변 메시지의 `proposals`
- `GET·POST /projects/{id}/tasks`, `PATCH·DELETE /projects/{id}/tasks/{task_id}`, `POST .../restore`. 같은 `source_proposal_id`로 다시 등록하면 기존 일정을 돌려준다(200, `existing: true`).
- `GET /home?brand_id=&today=`

## 항목별 AI (Claude가 정함)

- 기존 AI 실행 구조(`ai_runs`, 프로젝트 잠금, SSE, 중지, 실패 처리)를 그대로 쓴다. 한 프로젝트에 실행은 하나.
- 보내는 것: 항목의 제목·질문·가이드, 지금 저장 본문과 임시 입력, 사용자 요청, 사용자가 고른 참고(`plan` 기획 항목, `section` 같은 문서의 다른 항목, `brand` 연결 브랜드, `record` 정리 내용, `message` 대화 메시지, `note` 사용자가 적은 발췌). 이전 대화·다른 프로젝트·파일 원본은 보내지 않는다. 도구도 주지 않는다(검색 없음).
- 지시: 기록에 없는 가격·날짜·통계·소재·실적을 만들지 않고, 부족한 사실은 "확인 필요"로 적는다.
- 결과: 답변 메시지에 `candidate = {doc_id, section_id, base_revision, base_draft_revision, text}`. 실패·중지면 후보 없음.
- 적용: 후보를 만든 뒤 본문이나 임시 입력이 바뀌었으면 409 `CANDIDATE_OUTDATED`. `confirm: true`면 그래도 적용한다. 적용은 임시 입력만 바꾸고 검토 상태는 그대로다.

## 할 일 제안 (Claude가 정함)

- 같은 실행 구조. AI에게 `suggest_tasks` 도구(최대 5개, 제목·날짜(모르면 null)·메모)를 주고 그 호출만 받는다. 답변 메시지에 `proposals = [{proposal_id, title, suggested_date, note}]`.
- 등록은 화면이 확인한 후보를 `POST /tasks`로 보낸다. 날짜는 필수(기존 일정 조건). `source_proposal_id`는 이 프로젝트의 실제 제안이어야 한다.
- 모의 Provider(`AI_PROVIDER=demo`)는 시연용으로 할 일 후보 1개(날짜 없음)를 낸다.

## 계정 홈 (Claude가 정함)

- 범위: 내 진행 중 프로젝트 전체, 또는 `brand_id`(`none`이면 브랜드 없는 프로젝트).
- 오늘 날짜: 화면이 `today`를 보낼 수 있고, 없으면 한국 시간 기준.
- 내용: 최근 문서 5개(마지막 항목·진행도·프로젝트 이름), 오늘 할 일, 기한 지난 일, 진행 중 문서, 규칙 기반 추천(기한 지난 일, 최근 문서의 필수 미작성 항목, 7일 넘게 멈춘 문서). AI 호출 없음.

## 기존 기능과의 관계

- 자유 AI 대화는 그대로 둔다. 다만 첨부 파일 자동 읽기는 기획서 원칙에 맞춰 기본으로 끈다(`AI_READ_ATTACHMENTS=false`). 켜면 이전처럼 동작한다.
- 6개 기획 항목은 문서의 참고 후보일 뿐 문서로 바뀌지 않는다. 문서를 고쳐도 기획 값은 바뀌지 않는다.
