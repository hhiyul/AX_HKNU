"""요청별 참고 권한과 출처. 기존 원문은 보존하고 이후 입력만 거른다."""
from copy import deepcopy

from pymongo import ReturnDocument

from app.schemas.ai_scope import ReferenceScope
from app.services import conversations
from app.services.store import conflict, utcnow


def kind_of(key: str) -> str:
    if key.startswith("ref:"):
        return kind_of(key.split(":", 2)[-1])
    if key.startswith("ck_"):
        return "plan"
    head = key.split(":", 1)[0]
    return {"project_basic": "project", "linked_brand": "brand", "doc": "document", "tasks": "task",
            "project_memo": "memo", "reference_document": "document", "attachments_not_read": "file"}.get(head, head)


def normalize(scope: dict | None) -> dict:
    return ReferenceScope.model_validate(scope or {}).model_dump()


def allows(scope: dict, key: str) -> bool:
    if key == "today":
        return True
    if key.startswith("target:"):
        return key.split(":", 2)[1] == scope.get("_target_doc_id") and not any(key == excluded or key.startswith(excluded + ":") for excluded in scope["excluded"])
    if key.startswith("message:"):
        return key.split(":", 1)[1] in scope.get("message_ids", []) and key not in scope["excluded"]
    if kind_of(key) not in scope["kinds"]:
        return False
    return not any(key == excluded or key.startswith(excluded + ":") for excluded in scope["excluded"])


def restricted(scope: dict) -> bool:
    return bool(scope["excluded"]) or set(scope["kinds"]) != set(normalize(None)["kinds"])


def filter_sections(sections: dict, scope: dict) -> dict:
    result = {}
    for key, value in sections.items():
        if not allows(scope, key):
            continue
        deps = value.get("derived_source_keys")
        if value.get("derived") and ((deps is None and restricted(scope)) or any(not allows(scope, dep) for dep in (deps or []))):
            continue
        value = deepcopy(value)
        if key == "tasks":
            original = value.get("open", [])
            value["open"] = [t for t in original if allows(scope, f"task:{t.get('id', '')}")]
            if any(k.startswith("task:") for k in scope["excluded"]):
                value.pop("done_count", None)
        # 총액에서 제외한 거래 금액을 역산할 수 없도록 요약도 제외한다.
        if key == "ledger" and any(k.startswith("ledger:") for k in scope["excluded"]):
            continue
        result[key] = value
    return result


def manifest(sections: dict) -> list[dict]:
    now = utcnow().isoformat()
    result = []
    for key, value in sections.items():
        if key == "today" or key == "attachments_not_read":
            continue
        result.append({"key": key, "kind": kind_of(key), "title": value.get("title") or value.get("name") or key,
                       "revision": value.get("revision"), "fetched_at": now, "status": value.get("read_status", "read"),
                       "length": value.get("length"), "next_offset": value.get("next_offset")})
        if key == "tasks":
            result.extend({"key": f"task:{t['id']}", "kind": "task", "title": t["title"],
                           "revision": t.get("revision"), "fetched_at": now, "status": "read"}
                          for t in value.get("open", []) if t.get("id"))
    return result


async def get(db, project_id, conversation_id: str) -> dict:
    conversation = await conversations.get(db, project_id, conversation_id)
    return {"scope": normalize(conversation.get("reference_scope")), "revision": conversation.get("scope_revision", 0)}


async def save(db, project_id, conversation_id: str, body) -> dict:
    conversation = await conversations.get(db, project_id, conversation_id)
    revision_match = {"$in": [None, 0]} if body.revision == 0 else body.revision
    updated = await db.conversations.find_one_and_update(
        {"_id": conversation["_id"], "scope_revision": revision_match, "deleted_at": None},
        {"$set": {"reference_scope": body.scope.model_dump(), "scope_updated_at": utcnow()}, "$inc": {"scope_revision": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise conflict()
    return {"scope": updated["reference_scope"], "revision": updated["scope_revision"]}
