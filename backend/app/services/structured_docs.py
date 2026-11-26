"""항목형 전용 문서(펀딩 상세페이지·사업계획서). 기획서 v2의 공통 항목형 편집.

- 문서를 만들 때 작성 틀의 항목을 모두 만든다. 항목마다 저장 본문(text)과 임시 입력(draft)이 따로 있다.
- 임시 입력은 draft_revision, 저장 본문과 자료 연결은 revision으로 동시 수정을 막는다.
- 검토 완료는 비어 있지 않은 저장 본문의 지금 revision을 사용자가 확인한 것이다(reviewed_revision == revision).
- 저장 본문이 원본이다. 미리보기·내보내기는 저장 본문을 목차 순서로 합친 글이고 따로 고칠 수 없다.
"""
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.services import templates
from app.services.store import conflict, not_found, owned_ids, parse_object_id, utcnow

UNWRITTEN, WRITING, REVIEWED = "UNWRITTEN", "WRITING", "REVIEWED"


def section_state(stored: dict) -> str:
    if not (stored.get("text") or "").strip():
        return UNWRITTEN
    return REVIEWED if stored.get("reviewed_revision") == stored.get("revision") else WRITING


def section_view(meta: templates.Section, stored: dict) -> dict:
    return {
        "section_id": meta.section_id,
        "title": meta.title,
        "question": meta.question,
        "guide": meta.guide,
        "required": meta.required,
        "state": section_state(stored),
        "text": stored.get("text") or "",
        "draft": stored.get("draft"),
        "has_draft": stored.get("draft") is not None,
        "draft_revision": stored.get("draft_revision", 0),
        "revision": stored.get("revision", 0),
        "reviewed_revision": stored.get("reviewed_revision"),
        "material_ids": stored.get("material_ids") or [],
        "updated_at": stored.get("updated_at"),
    }


def progress(template: templates.Template, stored: dict[str, dict]) -> dict:
    counts = {"required_total": 0, "required_reviewed": 0, "optional_total": 0, "optional_reviewed": 0}
    for meta in template.sections:
        kind = "required" if meta.required else "optional"
        counts[f"{kind}_total"] += 1
        if section_state(stored.get(meta.section_id, {})) == REVIEWED:
            counts[f"{kind}_reviewed"] += 1
    return counts


async def _sections(db: AsyncDatabase, doc_id: ObjectId) -> dict[str, dict]:
    return {s["section_id"]: s async for s in db.doc_sections.find({"doc_id": doc_id})}


async def doc_view(db: AsyncDatabase, doc: dict, with_sections: bool = True) -> dict:
    template = templates.get(doc["template_id"], doc["template_version"])
    stored = await _sections(db, doc["_id"])
    last = template.section(doc.get("last_section_id") or "")
    view = {
        **{key: value for key, value in doc.items() if key != "user_id"},
        "status": "trash" if doc.get("deleted_at") else "active",
        "progress": progress(template, stored),
        "last_section_title": last.title if last else None,
    }
    if with_sections:
        view["sections"] = [section_view(meta, stored.get(meta.section_id, {})) for meta in template.sections]
    return view


async def create_doc(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, template_id: str, title: str | None,
                     origin_message_id: str | None = None, product_ids: list[str] = ()) -> dict:
    template = templates.latest(template_id)
    from app.services import catalog, reference_scope
    project = await db.projects.find_one({"_id": project_id, "user_id": user_id})
    snapshots = await catalog.snapshot(db, project, user_id, product_ids)
    origin = None
    scope = reference_scope.normalize(None)
    if origin_message_id:
        origin = await db.messages.find_one({"_id": parse_object_id(origin_message_id), "project_id": project_id, "role": "ASSISTANT"})
        if not origin or origin.get("completion", "COMPLETE") != "COMPLETE" or not origin.get("conversation_id"):
            raise AppError(422, "INVALID_ORIGIN", "완료된 대화 답변 한 건을 선택해 주세요.")
        conversation = await db.conversations.find_one({"_id": origin["conversation_id"], "project_id": project_id, "deleted_at": None})
        if not conversation:
            raise not_found()
        scope = reference_scope.normalize(conversation.get("reference_scope"))
        scope["message_ids"] = []  # Inherit exclusions, not unrelated past-chat selections.
    now = utcnow()
    doc = {
        "user_id": user_id, "project_id": project_id, "title": title or template.name,
        "template_id": template.template_id, "template_version": template.version, "last_section_id": None,
        "deleted_at": None, "revision": 1, "created_at": now, "updated_at": now,
        "purpose_ids": [template_id], "product_snapshots": snapshots,
        "origin_message_id": origin["_id"] if origin else None,
        "origin_conversation_id": origin["conversation_id"] if origin else None,
        "origin_scope": scope, "origin_section_id": template.sections[0].section_id,
    }
    doc["_id"] = (await db.structured_docs.insert_one(doc)).inserted_id
    await db.doc_sections.insert_many([
        {
            "user_id": user_id, "project_id": project_id, "doc_id": doc["_id"], "section_id": meta.section_id,
            "text": "", "draft": None, "draft_revision": 0, "revision": 0, "reviewed_revision": None,
            "material_ids": [], "updated_at": now,
        }
        for meta in template.sections
    ])
    return await doc_view(db, doc)


async def list_docs(db: AsyncDatabase, project_id: ObjectId, status: str) -> list[dict]:
    query = {"project_id": project_id, "deleted_at": {"$ne": None} if status == "trash" else None}
    cursor = db.structured_docs.find(query).sort("updated_at", -1)
    return [await doc_view(db, doc, with_sections=False) async for doc in cursor]


async def get_doc(db: AsyncDatabase, project_id: ObjectId, doc_id: str, active: bool = False) -> dict:
    doc = await db.structured_docs.find_one({"_id": parse_object_id(doc_id), "project_id": project_id})
    if doc is None:
        raise not_found()
    if active and doc.get("deleted_at"):
        raise AppError(409, "DOC_IN_TRASH", "휴지통에 있는 문서입니다. 먼저 복원해 주세요.")
    return doc


async def rename_doc(db: AsyncDatabase, project_id: ObjectId, doc_id: str, title: str, revision: int) -> dict:
    doc = await get_doc(db, project_id, doc_id)
    updated = await db.structured_docs.find_one_and_update(
        {"_id": doc["_id"], "revision": revision},
        {"$set": {"title": title, "updated_at": utcnow()}, "$inc": {"revision": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise conflict()
    return await doc_view(db, updated)


async def set_trashed(db: AsyncDatabase, project_id: ObjectId, doc_id: str, trashed: bool) -> dict:
    doc = await get_doc(db, project_id, doc_id)
    now = utcnow()
    updated = await db.structured_docs.find_one_and_update(
        {"_id": doc["_id"]},
        {"$set": {"deleted_at": now if trashed else None, "updated_at": now}, "$inc": {"revision": 1}},
        return_document=ReturnDocument.AFTER,
    )
    return await doc_view(db, updated)


async def touch(db: AsyncDatabase, doc_id: ObjectId, section_id: str) -> None:
    await db.structured_docs.update_one(
        {"_id": doc_id}, {"$set": {"last_section_id": section_id, "updated_at": utcnow()}}
    )


async def _update_section(db, doc: dict, section_id: str, match: dict, update: dict) -> dict:
    template = templates.get(doc["template_id"], doc["template_version"])
    meta = template.section(section_id)
    if meta is None:
        raise not_found()
    stored = await db.doc_sections.find_one_and_update(
        {"doc_id": doc["_id"], "section_id": section_id, **match},
        {**update, "$set": {**update.get("$set", {}), "updated_at": utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if stored is None:
        raise conflict()
    await touch(db, doc["_id"], section_id)
    return section_view(meta, stored)


async def save_draft(db, project_id: ObjectId, doc_id: str, section_id: str, text: str, draft_revision: int) -> dict:
    doc = await get_doc(db, project_id, doc_id, active=True)
    return await _update_section(
        db, doc, section_id, {"draft_revision": draft_revision}, {"$set": {"draft": text}, "$inc": {"draft_revision": 1}}
    )


async def discard_draft(db, project_id: ObjectId, doc_id: str, section_id: str, draft_revision: int) -> dict:
    doc = await get_doc(db, project_id, doc_id, active=True)
    return await _update_section(
        db, doc, section_id, {"draft_revision": draft_revision}, {"$set": {"draft": None}, "$inc": {"draft_revision": 1}}
    )


async def save_section(
    db, user_id: ObjectId, project_id: ObjectId, doc_id: str, section_id: str, text: str,
    material_ids: list[str] | None, revision: int,
) -> dict:
    """본문(과 자료 연결)을 저장한다. 임시 입력은 비우고, 검토 완료는 다시 확인해야 한다."""
    doc = await get_doc(db, project_id, doc_id, active=True)
    changes: dict = {"text": text, "draft": None}
    if material_ids is not None:
        changes["material_ids"] = await owned_ids(db, "assets", user_id, material_ids)
    return await _update_section(
        db, doc, section_id, {"revision": revision}, {"$set": changes, "$inc": {"revision": 1, "draft_revision": 1}}
    )


async def review_section(db, project_id: ObjectId, doc_id: str, section_id: str, revision: int) -> dict:
    doc = await get_doc(db, project_id, doc_id, active=True)
    stored = await db.doc_sections.find_one({"doc_id": doc["_id"], "section_id": section_id})
    if stored is None:
        raise not_found()
    if not (stored.get("text") or "").strip():
        raise AppError(422, "SECTION_EMPTY", "저장된 본문이 있어야 검토 완료할 수 있습니다.")
    return await _update_section(
        db, doc, section_id, {"revision": revision}, {"$set": {"reviewed_revision": revision}}
    )


async def compose(db: AsyncDatabase, project_id: ObjectId, doc_id: str) -> dict:
    """저장 본문을 목차 순서로 합친다. 비어 있는 항목은 넣지 않는다."""
    doc = await get_doc(db, project_id, doc_id)
    template = templates.get(doc["template_id"], doc["template_version"])
    stored = await _sections(db, doc["_id"])
    chunks = [f"# {doc['title']}"]
    for meta in template.sections:
        text = (stored.get(meta.section_id, {}).get("text") or "").strip()
        if text:
            chunks.append(f"## {meta.title}\n\n{text}")
    return {
        "doc_id": doc["_id"],
        "title": doc["title"],
        "text": "\n\n".join(chunks),
        "has_unsaved_drafts": any(s.get("draft") is not None for s in stored.values()),
    }
