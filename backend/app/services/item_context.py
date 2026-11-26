"""항목별 선택은 독립 저장하고 원 대화의 제외만 초기값으로 승계한다."""
from pymongo import ReturnDocument

from app.services import reference_scope, structured_docs, templates
from app.services.store import conflict, not_found


async def get(db, project_id, doc_id, section_id):
    doc = await structured_docs.get_doc(db, project_id, doc_id, active=True)
    if templates.get(doc["template_id"], doc["template_version"]).section(section_id) is None:
        raise not_found()
    section = await db.doc_sections.find_one({"doc_id": doc["_id"], "section_id": section_id})
    context = section.get("assist_context") or {}
    default_refs = [{"type": "message", "id": str(doc["origin_message_id"])}] if doc.get("origin_message_id") and section_id == doc.get("origin_section_id") else []
    return {"references": context.get("references", default_refs),
            "scope": reference_scope.normalize(context.get("scope", doc.get("origin_scope"))),
            "revision": context.get("revision", 0)}


async def save(db, project_id, doc_id, section_id, body):
    doc = await structured_docs.get_doc(db, project_id, doc_id, active=True)
    current = await get(db, project_id, doc_id, section_id)
    if current["revision"] != body.revision:
        raise conflict()
    context = {"references": [r.model_dump() for r in body.references], "scope": body.scope.model_dump(), "revision": body.revision + 1}
    revision_match = {"$in": [None, 0]} if body.revision == 0 else body.revision
    updated = await db.doc_sections.find_one_and_update(
        {"doc_id": doc["_id"], "section_id": section_id, "assist_context.revision": revision_match},
        {"$set": {"assist_context": context}}, return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise conflict()
    return context
