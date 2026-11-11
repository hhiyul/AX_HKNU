"""MongoDB 컬렉션·검증 규칙·인덱스 생성 스크립트.

설계가 바뀌면 아래 COLLECTIONS, INDEXES만 고치고 다시 실행하면 된다.
    docker compose run --rm --build backend python init_db.py 으로 db 테이블 생성인데 이미 있어서 굳이 안해도됨

- 문서는 건드리지 않고 규칙과 인덱스만 맞춘다.
- 여기 없는 필드도 저장할 수 있다. 정의한 필드의 타입과 필수 여부만 검사한다.
- 인덱스는 추가만 한다. 여기서 지운 인덱스는 Atlas에서 직접 지워야 한다.
"""
import os
import sys

from pymongo import ASCENDING, DESCENDING, MongoClient

DEFAULT_DB_NAME = "ax_platform"

STRING_LIST = {"bsonType": "array", "items": {"bsonType": "string"}}
TIMESTAMPS = {"created_at": "date", "updated_at": "date"}
# 프로젝트에 딸린 자료가 공통으로 갖는 필드
OWNED = {"user_id": "objectId", "project_id": "objectId"}


def enum(*values):
    return {"bsonType": "string", "enum": list(values)}


ITEM_KEY = enum("ck_01", "ck_02", "ck_03", "ck_04", "ck_05", "ck_06")


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
    # 상품기획 6개 항목. 프로젝트마다 항목당 문서 하나.
    "plan_items": build_schema(
        required={**OWNED, "item_key": ITEM_KEY, "version": "int", "revision": "int", **TIMESTAMPS},
        optional={"draft": ["object"], "decided": ["object"]},
    ),
    # 확정할 때마다 쌓이는 이력
    "plan_versions": build_schema(
        required={**OWNED, "item_key": ITEM_KEY, "version": "int", "content": "string", "decided_at": "date"},
        optional={"amount": ["int", "long"], "source_ref": ["objectId"]},
    ),
    # 추가 기록. 프로젝트당 문서 하나.
    "records": build_schema(
        required={**OWNED, "entries": "array", "revision": "int", **TIMESTAMPS},
        optional={},
    ),
    # 경제성 비용 입력. 프로젝트당 문서 하나.
    "cost_sheets": build_schema(
        required={**OWNED, "items": "array", "revision": "int", **TIMESTAMPS},
        optional={},
    ),
    "scenarios": build_schema(
        required={
            **OWNED,
            "label": "string",
            "price": ["int", "long"],
            "unit_cost": ["int", "long"],
            "fee": ["int", "long"],
            "profit": ["int", "long"],
            "complete": "bool",
            "uses_assumptions": "bool",
            "excluded": "array",
            "sheet_revision": "int",
            "created_at": "date",
        },
        optional={"margin_rate": ["double", "int", "long"], "adopted_at": ["date"]},
    ),
    "documents": build_schema(
        required={
            **OWNED,
            "doc_type": enum("PLAN", "INTRO", "DETAIL_PAGE", "MARKETING", "PREP"),
            "title": "string",
            "content": "string",
            "state": enum("DRAFT", "FINAL"),
            "basis": STRING_LIST,
            "plan_snapshot": "object",
            "version": "int",
            "revision": "int",
            **TIMESTAMPS,
        },
        optional={},
    ),
    "document_versions": build_schema(
        required={
            **OWNED,
            "document_id": "objectId",
            "version": "int",
            "title": "string",
            "content": "string",
            "finalized_at": "date",
        },
        optional={},
    ),
    "proposals": build_schema(
        required={
            **OWNED,
            "target": "string",
            "content": "string",
            "state": enum("PENDING", "HELD", "APPLIED", "REJECTED"),
            "base_revision": "int",
            **TIMESTAMPS,
        },
        optional={"message_id": ["objectId"], "applied_content": ["string"], "decided_at": ["date"]},
    ),
    # AI 채팅. 프로젝트당 하나.
    "chats": build_schema(
        required={
            **OWNED,
            "search_enabled": "bool",
            "file_refs": "array",
            "chip_refs": "array",
            "message_count": "int",
            "revision": "int",
            **TIMESTAMPS,
        },
        optional={},
    ),
    "messages": build_schema(
        required={
            **OWNED,
            "chat_id": "objectId",
            "ordinal": "int",
            "role": enum("USER", "ASSISTANT"),
            "content": "string",
            "asset_ids": "array",
            "completion": enum("COMPLETE", "PARTIAL", "UNKNOWN"),
            "created_at": "date",
        },
        optional={},
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
    "ai_jobs": build_schema(
        required={
            "user_id": "objectId",
            "brand_id": "objectId",
            # 설계안에 "등"으로 열려 있어 값 목록은 검사하지 않음
            "job_type": "string",
            "input": "object",
            "status": enum("queued", "running", "succeeded", "failed"),
            **TIMESTAMPS,
        },
        optional={
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
    ],
    "assets": [
        ([("user_id", ASCENDING), ("created_at", DESCENDING)], {}),
        ([("brand_id", ASCENDING), ("created_at", DESCENDING)], {}),
        ([("project_id", ASCENDING)], {}),
        ([("ai_job_id", ASCENDING)], {}),
    ],
    "ai_jobs": [
        ([("user_id", ASCENDING), ("created_at", DESCENDING)], {}),
        ([("brand_id", ASCENDING), ("created_at", DESCENDING)], {}),
    ],
    "plan_items": [([("project_id", ASCENDING), ("item_key", ASCENDING)], UNIQUE)],
    "plan_versions": [([("project_id", ASCENDING), ("item_key", ASCENDING), ("version", DESCENDING)], {})],
    "records": [([("project_id", ASCENDING)], UNIQUE)],
    "cost_sheets": [([("project_id", ASCENDING)], UNIQUE)],
    "scenarios": [([("project_id", ASCENDING), ("created_at", DESCENDING)], {})],
    "documents": [([("project_id", ASCENDING), ("updated_at", DESCENDING)], {})],
    "document_versions": [([("document_id", ASCENDING), ("version", DESCENDING)], {})],
    "proposals": [([("project_id", ASCENDING), ("state", ASCENDING), ("created_at", DESCENDING)], {})],
    "chats": [([("project_id", ASCENDING)], UNIQUE), ([("chip_refs", ASCENDING)], {})],
    "messages": [([("project_id", ASCENDING), ("ordinal", ASCENDING)], {})],
    "requests": [
        ([("user_id", ASCENDING), ("key", ASCENDING)], UNIQUE),
        ([("created_at", ASCENDING)], {"expireAfterSeconds": 7 * 24 * 60 * 60}),
    ],
}


def migrate(db):
    """v0.3 이전 문서를 새 구조에 맞춘다. 여러 번 실행해도 결과가 같다."""
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
    return moved


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
