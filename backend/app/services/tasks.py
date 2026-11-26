"""할 일(일정). 기획서 v2: 제목·날짜(필수)·메모·완료는 일정이 원본이고, 문서에는 일정 상태를 따로 두지 않는다.

- 문서 항목에서 만든 일정은 source_doc_id·source_section_id로 원래 항목에 돌아간다.
- AI 할 일 후보(proposal)는 확인 전에는 일정이 아니다. 같은 후보로 다시 등록하면 기존 일정을 돌려준다.
- 업무 완료(done)는 문서 검토 완료와 서로 바꾸지 않는다.
"""
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.errors import AppError
from app.services import templates
from app.services.store import conflict, not_found, parse_object_id, utcnow


async def _source(db: AsyncDatabase, task: dict) -> dict | None:
    if not task.get("source_doc_id"):
        return None
    doc = await db.structured_docs.find_one({"_id": task["source_doc_id"]})
    if doc is None:
        return {"doc_id": task["source_doc_id"], "doc_title": None, "doc_status": "missing",
                "section_id": task.get("source_section_id"), "section_title": None}
    meta = templates.get(doc["template_id"], doc["template_version"]).section(task.get("source_section_id") or "")
    return {
        "doc_id": doc["_id"], "doc_title": doc["title"], "doc_status": "trash" if doc.get("deleted_at") else "active",
        "section_id": task.get("source_section_id"), "section_title": meta.title if meta else None,
    }


async def task_view(db: AsyncDatabase, task: dict, existing: bool = False) -> dict:
    return {
        **{key: value for key, value in task.items() if key != "user_id"},
        "status": "trash" if task.get("deleted_at") else "active",
        "source": await _source(db, task),
        "existing": existing,
    }


async def _check_source(db, project_id: ObjectId, doc_id: str | None, section_id: str | None) -> ObjectId | None:
    if not doc_id:
        if section_id:
            raise not_found()
        return None
    doc = await db.structured_docs.find_one({"_id": parse_object_id(doc_id), "project_id": project_id, "deleted_at": None})
    if doc is None:
        raise not_found()
    if section_id and templates.get(doc["template_id"], doc["template_version"]).section(section_id) is None:
        raise not_found()
    return doc["_id"]


async def _proposal_source(db, project_id: ObjectId, body) -> tuple[ObjectId, str | None, str | None]:
    """제안의 문서·항목을 쓴다. 화면이 다른 문서·항목을 보내면 422."""
    ident = parse_object_id(body.source_proposal_id)
    message = await db.messages.find_one(
        {"project_id": project_id, "proposals.proposal_id": ident}
    )
    if message is None:
        raise not_found()
    if message.get("completion", "COMPLETE") != "COMPLETE":
        raise AppError(422, "INCOMPLETE_PROPOSAL", "완료된 요청의 후보만 등록할 수 있습니다.")
    if message.get("conversation_id") and not await db.conversations.find_one({"_id": message["conversation_id"], "project_id": project_id, "deleted_at": None}):
        raise not_found()
    doc_id = str(message["target_doc_id"]) if message.get("target_doc_id") else None
    section_id = message.get("target_section_id")
    sent = (body.source_doc_id, body.source_section_id)
    if (sent[0] and sent[0] != doc_id) or (sent[1] and sent[1] != section_id):
        raise AppError(422, "PROPOSAL_SOURCE_MISMATCH", "할 일 후보를 만든 문서·항목과 다릅니다.")
    return ident, doc_id, section_id


async def create_task(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, body) -> tuple[dict, bool]:
    """(일정, 새로 만들었는지). 같은 제안으로 다시 등록하면 기존 일정을 그대로 돌려준다."""
    proposal_id, source_doc, source_section = None, body.source_doc_id, body.source_section_id
    if body.source_proposal_id:
        existing = await db.tasks.find_one({"user_id": user_id, "project_id": project_id, "source_proposal_id": parse_object_id(body.source_proposal_id)})
        if existing:
            return await task_view(db, existing, existing=True), False
        proposal_id, source_doc, source_section = await _proposal_source(db, project_id, body)
    doc_id = await _check_source(db, project_id, source_doc, source_section)
    origin = await db.messages.find_one({"project_id": project_id, "proposals.proposal_id": proposal_id}) if proposal_id else None
    purposes = list(dict.fromkeys(body.purpose_ids))
    if doc_id:
        doc = await db.structured_docs.find_one({"_id": doc_id, "project_id": project_id})
        if not set(purposes) <= set(doc.get("purpose_ids", [doc["template_id"]])):
            raise AppError(422, "INVALID_PURPOSE", "원 문서의 목적 중에서 선택해 주세요.")
    if proposal_id:
        existing = await db.tasks.find_one({"user_id": user_id, "source_proposal_id": proposal_id})
        if existing:
            return await task_view(db, existing, existing=True), False
    now = utcnow()
    task = {
        "user_id": user_id, "project_id": project_id, "title": body.title, "date": body.date.isoformat(),
        "note": body.note, "done": False, "source_doc_id": doc_id, "source_section_id": source_section,
        "source_proposal_id": proposal_id, "deleted_at": None, "revision": 1, "created_at": now, "updated_at": now,
        "purpose_ids": purposes, "source_conversation_id": (origin or {}).get("conversation_id"),
        "source_message_id": (origin or {}).get("_id"),
    }
    try:
        task["_id"] = (await db.tasks.insert_one(task)).inserted_id
    except DuplicateKeyError:
        # 같은 제안을 동시에 두 번 등록한 경우: 먼저 만들어진 일정을 돌려준다.
        existing = await db.tasks.find_one({"user_id": user_id, "source_proposal_id": proposal_id})
        return await task_view(db, existing, existing=True), False
    return await task_view(db, task), True


async def annotate_proposals(db: AsyncDatabase, messages: list[dict]) -> list[dict]:
    """할 일 후보마다 이미 등록한 일정(task_id, task_status)을 붙인다. 재방문해도 등록 여부를 알 수 있다."""
    ids = [p["proposal_id"] for m in messages for p in (m.get("proposals") or [])]
    if not ids:
        return messages
    found = {t["source_proposal_id"]: t async for t in db.tasks.find({"source_proposal_id": {"$in": ids}})}
    for message in messages:
        for proposal in message.get("proposals") or []:
            task = found.get(proposal["proposal_id"])
            proposal["task_id"] = task["_id"] if task else None
            proposal["task_status"] = ("trash" if task.get("deleted_at") else "active") if task else None
            proposal["task"] = {"id": task["_id"], "title": task["title"], "date": task["date"], "done": task["done"],
                                "purpose_ids": task.get("purpose_ids", [])} if task else None
    return messages


async def list_account_tasks(db: AsyncDatabase, projects: dict, start: str | None, end: str | None) -> list[dict]:
    """여러 프로젝트의 일정(전체 일정 화면). 휴지통 일정은 빼고 날짜순."""
    query: dict = {"project_id": {"$in": list(projects)}, "deleted_at": None}
    if start or end:
        query["date"] = {**({"$gte": start} if start else {}), **({"$lte": end} if end else {})}
    cursor = db.tasks.find(query).sort([("date", 1), ("created_at", 1)])
    return [
        {**await task_view(db, task), "project_name": projects[task["project_id"]].get("name")}
        async for task in cursor
    ]


async def list_tasks(db: AsyncDatabase, project_id: ObjectId, status: str) -> list[dict]:
    query = {"project_id": project_id, "deleted_at": {"$ne": None} if status == "trash" else None}
    cursor = db.tasks.find(query).sort([("date", 1), ("created_at", 1)])
    return [await task_view(db, task) async for task in cursor]


async def _get(db, project_id: ObjectId, task_id: str) -> dict:
    task = await db.tasks.find_one({"_id": parse_object_id(task_id), "project_id": project_id})
    if task is None:
        raise not_found()
    return task


async def update_task(db: AsyncDatabase, project_id: ObjectId, task_id: str, changes: dict, revision: int) -> dict:
    task = await _get(db, project_id, task_id)
    if "date" in changes:
        changes["date"] = changes["date"].isoformat()
    updated = await db.tasks.find_one_and_update(
        {"_id": task["_id"], "revision": revision},
        {"$set": {**changes, "updated_at": utcnow()}, "$inc": {"revision": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise conflict()
    return await task_view(db, updated)


async def set_trashed(db: AsyncDatabase, project_id: ObjectId, task_id: str, trashed: bool) -> dict:
    task = await _get(db, project_id, task_id)
    now = utcnow()
    updated = await db.tasks.find_one_and_update(
        {"_id": task["_id"]},
        {"$set": {"deleted_at": now if trashed else None, "updated_at": now}, "$inc": {"revision": 1}},
        return_document=ReturnDocument.AFTER,
    )
    return await task_view(db, updated)
