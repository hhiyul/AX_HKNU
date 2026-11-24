"""MongoDB 컬렉션·검증 규칙·인덱스 생성 스크립트.

설계가 바뀌면 아래 COLLECTIONS, INDEXES만 고치고 다시 실행하면 된다.
    docker compose run --rm --build backend python init_db.py 으로 db 테이블 생성인데 이미 있어서 굳이 안해도됨

- 문서는 건드리지 않고 규칙과 인덱스만 맞춘다.
- 여기 없는 필드도 저장할 수 있다. 정의한 필드의 타입과 필수 여부만 검사한다.
- 인덱스는 추가만 한다. 여기서 지운 인덱스는 Atlas에서 직접 지워야 한다.
- 컬렉션도 지우지 않는다. 더는 쓰지 않는 item_versions, scenarios, proposals는 Atlas에서 직접 지운다.
"""
import os
import sys
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import BulkWriteError

DUPLICATE_KEY = 11000

DEFAULT_DB_NAME = "ax_platform"

STRING_LIST = {"bsonType": "array", "items": {"bsonType": "string"}}
TIMESTAMPS = {"created_at": "date", "updated_at": "date"}
# 프로젝트에 딸린 자료가 공통으로 갖는 필드
OWNED = {"user_id": "objectId", "project_id": "objectId"}
# 상품기획 6개 항목 순서대로임. app/services/items.py의 PLAN_LABELS와 같아야 한다.
PLAN_KEYS = ["ck_01", "ck_02", "ck_03", "ck_04", "ck_05", "ck_06"]


def enum(*values):
    return {"bsonType": "string", "enum": list(values)}


def build_schema(required, optional):
    properties = {}
    for name, rule in required.items():
        properties[name] = rule if isinstance(rule, dict) else {"bsonType": rule}
    # 선택 필드는 null도 허용
    for name, types in optional.items():
        properties[name] = {"bsonType": [*types, "null"]}
    return {"bsonType": "object", "required": list(required), "properties": properties}


COLLECTIONS = {
    "users": build_schema(
        required={
            "email": "string",
            "password_hash": "string",
            "creator_name": "string",
            **TIMESTAMPS,
        },
        optional={},
    ),
    "brands": build_schema(
        required={
            "user_id": "objectId",
            "brand_type": enum("new", "existing"),
            "keywords": STRING_LIST,
            "colors": STRING_LIST,
            "status": enum("draft", "completed"),
            **TIMESTAMPS,
        },
        optional={
            "name": ["string"],
            "description": ["string"],
            "target_customer": ["string"],
            "logo_asset_id": ["objectId"],
        },
    ),
    "projects": build_schema(
        required={
            "user_id": "objectId",
            "name": "string",
            "current_step": "string",
            "revision": "int",
            **TIMESTAMPS,
        },
        optional={
            # 브랜드 없이도 프로젝트를 만들 수 있다.
            "brand_id": ["objectId"],
            "description": ["string"],
            # AI 채팅 설정과 메시지 수. 채팅은 프로젝트당 하나라 따로 컬렉션을 두지 않는다.
            # 진행 단계(planning, prototype, ready, selling), 메모장, AI가 채우는 다음 할 일
            "stage": ["string"],
            "note": ["string"],
            "next_action": ["string"],
            # 보관함·휴지통에 들어간 시각. 둘 다 없으면 진행 중이다.
            "archived_at": ["date"],
            "deleted_at": ["date"],
            # 기획 6개 항목 중 작성한 개수와 최근 작업 20건
            "plan_filled": ["int"],
            "recent": ["array"],
            "search_enabled": ["bool"],
            "file_refs": ["array"],
            "chip_refs": ["array"],
            "message_count": ["int"],
            # 진행 중인 AI 채팅 실행과 압축한 예전 대화 {text, covered_ids, revision}
            "active_run": ["objectId"],
            "chat_capsule": ["object"],
            # 대화별 압축 요약 {conversation_id: {text, covered_ids, revision}}
            "chat_capsules": ["object"],
        },
    ),
    "assets": build_schema(
        required={
            "user_id": "objectId",
            "asset_type": enum("logo", "banner", "product_image", "document"),
            "source": enum("upload", "ai"),
            "file_url": "string",
            "file_name": "string",
            "mime_type": "string",
            **TIMESTAMPS,
        },
        optional={
            # 브랜드와 프로젝트 중 하나에는 속한다.
            "brand_id": ["objectId"],
            "project_id": ["objectId"],
            "ai_job_id": ["objectId"],
        },
    ),
    # 프로젝트에 저장하는 글. kind로 구분한다: plan(기획 6개 항목), record(AI와 함께 정리한 내용), document(산출물).
    # 종류마다 내용이 조금씩 달라 공통 필드만 검사한다. 나머지 규칙은 services/items.py에 있다.
    "items": build_schema(
        required={
            **OWNED,
            "kind": enum("plan", "record", "document"),
            "key": "string",
            "revision": "int",
            **TIMESTAMPS,
        },
        optional={
            "title": ["string"],
            "content": ["string"],
            "amount": ["int", "long"],
            "skipped": ["bool"],
            # 이 글을 저장한 AI 답변
            "message_id": ["objectId"],
            # 산출물 편집기의 임시 저장
            "draft": ["object"],
            # AI가 만든 산출물의 출처 목록
            "sources": ["array"],
        },
    ),
    # 경제성 입력. 프로젝트당 문서 하나. cost는 지금 입력한 값, base는 변경 전 비교 기준이다.
    "cost_sheets": build_schema(
        required={**OWNED, "revision": "int", **TIMESTAMPS},
        optional={"cost": ["object"], "base": ["object"]},
    ),
    "messages": build_schema(
        required={
            **OWNED,
            "ordinal": "int",
            "role": enum("USER", "ASSISTANT"),
            "content": "string",
            "asset_ids": "array",
            "completion": enum("COMPLETE", "PARTIAL", "UNKNOWN"),
            "created_at": "date",
        },
        # doc은 AI가 만든 문서 초안, record_key와 doc_key는 이 답변에서 저장한 글
        # source_ids는 AI 답변이 읽은 참고 파일
        optional={"doc": ["object"], "record_key": ["string"], "doc_key": ["string"], "source_ids": ["array"], "record_draft": ["object"],
                  # 항목별 AI·할 일 제안: 목적, 대상 문서·항목, 실행 id, 후보, 할 일 후보
                  "purpose": ["string"], "target_doc_id": ["objectId"], "target_section_id": ["string"],
                  "request_id": ["objectId"], "candidate": ["object"], "proposals": ["array"],
                  # 채팅 대화. 항목별 AI·할 일 제안 메시지는 없음
                  "conversation_id": ["objectId"]},
    ),
    # AI 채팅 대화 목록. 제목이 없으면 첫 질문으로 정한다.
    "conversations": build_schema(
        required={
            **OWNED,
            "message_count": "int",
            "revision": "int",
            **TIMESTAMPS,
        },
        optional={"title": ["string"], "preview": ["string"], "last_message_at": ["date"], "deleted_at": ["date"]},
    ),
    # 정산 수입·지출 내역. 금액은 원 단위 양수
    "ledger_entries": build_schema(
        required={
            **OWNED,
            "type": enum("income", "expense"),
            "title": "string",
            "amount": {"bsonType": ["int", "long"], "minimum": 1},
            # YYYY-MM-DD
            "date": "string",
            "revision": "int",
            **TIMESTAMPS,
        },
        optional={"note": ["string"], "deleted_at": ["date"]},
    ),
    # 중복 요청 방지 기록. 7일 뒤 자동으로 지워진다.
    "requests": build_schema(
        required={
            "user_id": "objectId",
            "key": "string",
            "state": enum("PROCESSING", "APPLIED", "NOT_APPLIED"),
            "created_at": "date",
        },
        optional={"error": ["object"]},
    ),
    # 항목형 전용 문서(펀딩 상세페이지·사업계획서). 항목은 doc_sections에 있다.
    "structured_docs": build_schema(
        required={
            **OWNED,
            "title": "string",
            "template_id": enum("funding", "business_plan"),
            "template_version": "int",
            "revision": "int",
            **TIMESTAMPS,
        },
        optional={"last_section_id": ["string"], "deleted_at": ["date"]},
    ),
    # 문서 항목. text는 저장 본문, draft는 임시 입력. reviewed_revision == revision이면 검토 완료.
    "doc_sections": build_schema(
        required={
            **OWNED,
            "doc_id": "objectId",
            "section_id": "string",
            "text": "string",
            "draft_revision": "int",
            "revision": "int",
            "material_ids": "array",
            "updated_at": "date",
        },
        optional={"draft": ["string"], "reviewed_revision": ["int"]},
    ),
    # 할 일(일정). 문서 항목에서 만들었으면 source_*로 원래 항목에 돌아간다.
    "tasks": build_schema(
        required={
            **OWNED,
            "title": "string",
            # YYYY-MM-DD
            "date": "string",
            "done": "bool",
            "revision": "int",
            **TIMESTAMPS,
        },
        optional={
            "note": ["string"],
            "source_doc_id": ["objectId"],
            "source_section_id": ["string"],
            "source_proposal_id": ["objectId"],
            "deleted_at": ["date"],
        },
    ),
    # AI 채팅 실행 기록. 메시지 하나를 보내면 실행 하나가 생긴다.
    "ai_runs": build_schema(
        required={
            "user_id": "objectId",
            "project_id": "objectId",
            "status": enum("running", "completed", "failed", "stopped", "unknown"),
            **TIMESTAMPS,
        },
        optional={
            # 잠금을 잡은 직후 저장한 사용자 메시지와, 저장했다면 AI 답변
            "user_message_id": ["objectId"],
            "assistant_message_id": ["objectId"],
            "error_code": ["string"],
            "delivery": ["string"],
            "usage": ["object"],
            "finished_at": ["date"],
            # 실행 중 15초마다 갱신. 60초 넘게 멈추면 죽은 실행으로 정리한다.
            "heartbeat_at": ["date"],
            # 실행을 갖지 않은 서버가 받은 중지 요청
            "stop_requested": ["bool"],
        },
    ),
    "ai_jobs": build_schema(
        required={
            "user_id": "objectId",
            # 작업이 다루는 기획 항목
            "job_type": enum(*PLAN_KEYS),
            "input": "object",
            "status": enum("queued", "running", "succeeded", "failed"),
            **TIMESTAMPS,
        },
        optional={
            # 브랜드 없는 프로젝트의 작업도 있다.
            "brand_id": ["objectId"],
            "project_id": ["objectId"],
            "result": ["object"],
            "error_message": ["string"],
            "completed_at": ["date"],
        },
    ),
}

UNIQUE = {"unique": True}

INDEXES = {
    "users": [([("email", ASCENDING)], UNIQUE)],
    "brands": [([("user_id", ASCENDING), ("created_at", DESCENDING)], {})],
    "projects": [
        ([("user_id", ASCENDING), ("updated_at", DESCENDING)], {}),
        ([("brand_id", ASCENDING), ("created_at", DESCENDING)], {}),
        ([("chip_refs", ASCENDING)], {}),
    ],
    "assets": [
        ([("user_id", ASCENDING), ("created_at", DESCENDING)], {}),
        ([("brand_id", ASCENDING), ("created_at", DESCENDING)], {}),
        ([("project_id", ASCENDING)], {}),
        ([("ai_job_id", ASCENDING)], {}),
    ],
    "structured_docs": [([("project_id", ASCENDING), ("updated_at", DESCENDING)], {}), ([("user_id", ASCENDING), ("updated_at", DESCENDING)], {})],
    "doc_sections": [([("doc_id", ASCENDING), ("section_id", ASCENDING)], UNIQUE), ([("project_id", ASCENDING)], {})],
    "tasks": [
        ([("project_id", ASCENDING), ("date", ASCENDING)], {}),
        ([("user_id", ASCENDING), ("date", ASCENDING)], {}),
        # 같은 AI 제안은 일정 하나만
        ([("user_id", ASCENDING), ("source_proposal_id", ASCENDING)],
         {"unique": True, "partialFilterExpression": {"source_proposal_id": {"$type": "objectId"}}}),
    ],
    "ai_runs": [
        ([("project_id", ASCENDING), ("created_at", DESCENDING)], {}),
        ([("status", ASCENDING), ("heartbeat_at", ASCENDING)], {}),
    ],
    "ai_jobs": [
        ([("user_id", ASCENDING), ("created_at", DESCENDING)], {}),
        ([("brand_id", ASCENDING), ("created_at", DESCENDING)], {}),
        ([("project_id", ASCENDING)], {}),
    ],
    "items": [
        ([("project_id", ASCENDING), ("kind", ASCENDING), ("key", ASCENDING)], UNIQUE),
        ([("project_id", ASCENDING), ("kind", ASCENDING), ("updated_at", DESCENDING)], {}),
    ],
    "cost_sheets": [([("project_id", ASCENDING)], UNIQUE)],
    "messages": [
        ([("project_id", ASCENDING), ("ordinal", ASCENDING)], {}),
        ([("conversation_id", ASCENDING), ("ordinal", ASCENDING)], {}),
    ],
    "conversations": [([("project_id", ASCENDING), ("last_message_at", DESCENDING)], {})],
    "ledger_entries": [([("project_id", ASCENDING), ("date", DESCENDING)], {})],
    "requests": [
        ([("user_id", ASCENDING), ("key", ASCENDING)], UNIQUE),
        ([("created_at", ASCENDING)], {"expireAfterSeconds": 7 * 24 * 60 * 60}),
    ],
}


def fill_plan(db):
    """기획 항목 문서가 6개보다 적은 프로젝트에 빠진 항목을 건너뜀(빈 내용)으로 채운다."""
    added = 0
    now = datetime.now(timezone.utc)
    for project in db.projects.find({}, {"user_id": 1, "created_at": 1, "updated_at": 1}):
        if project.get("user_id") is None:
            continue
        ident = {"project_id": project["_id"], "kind": "plan"}
        existing = {document["key"] for document in db["items"].find(ident, {"key": 1})}
        missing = [
            {
                **ident,
                "user_id": project["user_id"],
                "key": key,
                "content": "",
                "amount": None,
                "revision": 1,
                "created_at": project.get("created_at") or now,
                "updated_at": project.get("updated_at") or now,
            }
            for key in PLAN_KEYS
            if key not in existing
        ]
        if not missing:
            continue
        try:
            added += len(db["items"].insert_many(missing, ordered=False).inserted_ids)
        except BulkWriteError as error:
            # 실행 중인 서버가 그 사이 같은 항목을 만들었으면 그 항목만 넘어간다.
            if any(fault["code"] != DUPLICATE_KEY for fault in error.details["writeErrors"]):
                raise
            added += error.details["nInserted"]
    return added


def migrate(db):
    """예전 문서를 새 구조에 맞춘다. 여러 번 실행해도 결과가 같다."""
    owners = {brand["_id"]: brand["user_id"] for brand in db.brands.find({}, {"user_id": 1})}
    moved = 0
    for name in ("projects", "assets"):
        for document in db[name].find({"user_id": {"$exists": False}}, {"brand_id": 1}):
            owner = owners.get(document.get("brand_id"))
            if owner is not None:
                moved += db[name].update_one({"_id": document["_id"]}, {"$set": {"user_id": owner}}).modified_count
    moved += db.projects.update_many({"revision": {"$exists": False}}, {"$set": {"revision": 1}}).modified_count
    # 이름 없이 저장된 예전 프로젝트
    moved += db.projects.update_many({"name": None}, {"$set": {"name": "이름 없는 프로젝트"}}).modified_count
    if "items" in db.list_collection_names():
        # 초안·확정으로 나뉘어 있던 글은 지금 보이던 내용(초안이 있으면 초안) 하나로 합친다.
        for document in db["items"].find({"content": {"$exists": False}}):
            part = document.get("draft") or document.get("decided") or {}
            changes = {"content": part.get("content") or "", "amount": part.get("amount"), "draft": None}
            moved += db["items"].update_one({"_id": document["_id"]}, {"$set": changes}).modified_count
        for project in db.projects.find({"plan_filled": {"$exists": False}}, {"_id": 1}):
            filled = db["items"].count_documents(
                {"project_id": project["_id"], "kind": "plan", "content": {"$nin": ["", None]}}
            )
            moved += db.projects.update_one({"_id": project["_id"]}, {"$set": {"plan_filled": filled}}).modified_count
    return moved + fill_plan(db)


def apply_collection(db, name, schema):
    # moderate: 이미 규칙에 안 맞는 기존 문서는 수정할 때 검사를 건너뜀
    options = {"validator": {"$jsonSchema": schema}, "validationLevel": "moderate"}
    if name in db.list_collection_names():
        db.command("collMod", name, **options)
        return "규칙 갱신"
    db.create_collection(name, **options)
    return "생성"


def main():
    uri = os.environ.get("MONGO_DETAILS")
    if not uri:
        sys.exit("MONGO_DETAILS가 비어 있습니다. .env에 Atlas 연결 문자열을 넣으세요.")

    with MongoClient(uri, serverSelectionTimeoutMS=10000) as client:
        db = client.get_default_database(default=DEFAULT_DB_NAME)
        print(f"DB: {db.name}")
        # 규칙을 바꾸기 전에 옮겨야 예전 문서도 새 규칙을 통과한다.
        print(f"  예전 문서 {migrate(db)}건 보정")
        for name, schema in COLLECTIONS.items():
            result = apply_collection(db, name, schema)
            for keys, options in INDEXES.get(name, []):
                db[name].create_index(keys, **options)
            print(f"  {name}: {result}, 인덱스 {len(INDEXES.get(name, []))}개")


if __name__ == "__main__":
    main()
