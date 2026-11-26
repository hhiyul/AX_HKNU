"""GD-050/068/069 항목 도움: 대상·같은 항목 이력·같은 문서 저장본·명시 참고.

후보 적용은 임시 입력만 바꾼다. 별도 AI 점검은 의견이며 후보나 검토 완료가 아니다.
외부 참고 제외는 파생 이력에도 적용하며 다른 항목의 미저장 글과 대화는 넣지 않는다.
"""
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.ai.context import Budget, ContextError, prepare
from app.ai.runtime import Runtime
from app.errors import AppError
from app.services import ai_chat, ai_context, ai_images, ai_tools, ai_workspace, items, structured_docs, tasks, templates, item_context, reference_scope
from app.config import get_settings
from app.services.source_reader import SourceReader
from app.schemas.docs import Reference
from app.services.store import conflict, not_found, parse_object_id, utcnow

PURPOSE = "document_assist"
ACTIONS = {"draft": "초안 만들기", "refine": "현재 글 다듬기", "check": "AI 내용 점검"}
INSTRUCTION = (
    "You help a Korean creator write one section of a {doc_name} document. {action_rule} "
    "Write only the section body in Korean, ready to paste. Use only facts in the supplied section text and "
    "selected references. Never invent prices, dates, statistics, materials, quantities, achievements or policies. "
    "When a needed fact is missing, write it as a line starting with '확인 필요:' instead of guessing. "
    "Section guide: {guide}"
)
ACTION_RULES = {
    "draft": "Draft the section from the references and the question.",
    "refine": "Improve the current text (the draft if present, otherwise the saved text) without changing its facts.",
    "check": "Return an advisory list of unsupported facts, contradictions and questions. Do not rewrite the body or declare review complete.",
}


def _invalid(message: str) -> AppError:
    return AppError(422, "INVALID_REFERENCE", message)


async def _brand_reference(db: AsyncDatabase, project: dict) -> dict:
    brand = await db.brands.find_one({"_id": project.get("brand_id")}) if project.get("brand_id") else None
    if brand is None:
        raise AppError(422, "NO_BRAND", "연결된 브랜드가 없습니다.")
    fields = ("name", "description", "target_customer", "keywords", "colors")
    return {"type": "brand", "id": str(brand["_id"]), "content": {key: brand.get(key) for key in fields}}


async def _one_reference(db, project: dict, doc: dict, reference) -> dict:
    kind, ident = reference.type, reference.id
    if kind == "note":
        if not (reference.text or "").strip():
            raise _invalid("note에는 text가 필요합니다.")
        return {"type": "note", "content": reference.text}
    if kind == "brand":
        return await _brand_reference(db, project)
    if not ident:
        raise _invalid(f"{kind}에는 id가 필요합니다.")
    if kind == "plan":
        item = await items.get_item(db, project["_id"], "plan", ident)
        return {"type": "plan", "id": ident, "title": item["title"], "state": item["state"], "content": item["content"]}
    if kind == "record":
        item = await items.get_item(db, project["_id"], "record", ident)
        return {"type": "record", "id": ident, "title": item["title"], "content": item["content"]}
    if kind == "section":
        template = templates.get(doc["template_id"], doc["template_version"])
        meta = template.section(ident)
        stored = await db.doc_sections.find_one({"doc_id": doc["_id"], "section_id": ident}) if meta else None
        if stored is None:
            raise not_found()
        return {"type": "section", "id": ident, "title": meta.title, "content": stored.get("text") or ""}
    message = await db.messages.find_one({"_id": parse_object_id(ident), "project_id": project["_id"]})
    if message is None:
        raise not_found()
    return {"type": "message", "id": ident, "role": message["role"], "content": message["content"]}


async def resolve_references(db, project: dict, doc: dict, references: list) -> list[dict]:
    """사용자가 고른 참고만 읽는다. 이 프로젝트 밖의 것은 404."""
    return [await _one_reference(db, project, doc, reference) for reference in references]


class AssistJob(ai_chat._RunJob):
    """고정된 항목의 후보 또는 점검 의견. 기존 잠금·SSE·사용량·중지 경로를 공유한다."""

    def __init__(self, db, runtime, user_id, project_id, run_id, user_message_id, text, *, target: dict):
        super().__init__(db, runtime, user_id, project_id, run_id, user_message_id, text)
        self.target = target
        self.scope = target["scope"]
        self.scope_revision = target["scope_revision"]
        self.source_manifest = target["source_manifest"]
        self.history_source_keys = set(target["history_source_keys"])
        self.images = target["images"]

    def _prefix(self) -> list[dict]:
        t = self.target
        section = {
            "key": f"target:{t['doc_id']}:{t['section_id']}", "doc_title": t["doc_title"], "section_id": t["section_id"], "title": t["title"],
            "question": t["question"], "saved_text": t["text"], "draft": t["draft"],
        }
        # 오늘 날짜(한국)는 "다음 주 금요일" 같은 말을 날짜로 바꿀 때 쓴다.
        today = {"key": "today", **ai_workspace.today_section()}
        return [section, *t["saved_sections"], *({"key": "reference", **reference} for reference in t["references"]), today]

    def _instruction(self) -> str:
        t = self.target
        rule = INSTRUCTION.format(doc_name=t["doc_name"], action_rule=ACTION_RULES[t["action"]], guide=t["guide"])
        request = t["request"].strip()
        return rule + ("\n\nUser request: " + request if request else "")

    async def _generate(self) -> None:
        self.sections = {}
        self.toolbox = ai_tools.ToolBox(self.emit, self.provider, {}, False, "", self._search_allowed, read_offered=False)
        await self.emit("context_ready", "고른 참고 정보를 확인했습니다.")
        self.budget = Budget()
        prepared = await prepare(
            self.provider, self._prefix(), self.target["history"], self._instruction(), [], self.emit, self.runtime.model,
            self.budget, None, False, None, self._observe_count, self.images,
        )
        await self.emit("generating", "후보를 만들고 있습니다.")
        if await self._stream_once(prepared.wire):
            raise ContextError("TOOL_NOT_ALLOWED")

    def _message_extra(self, text: str, completion: str) -> dict:
        t = self.target
        extra = {
            "purpose": PURPOSE, "target_doc_id": t["doc_id"], "target_section_id": t["section_id"],
            "request_id": self.run_id, "assist_action": t["action"],
            "checked_revision": t["revision"] if t["action"] == "check" else None,
        }
        if completion == "COMPLETE" and t["action"] != "check":
            extra["candidate"] = {
                "doc_id": t["doc_id"], "section_id": t["section_id"], "base_revision": t["revision"],
                "base_draft_revision": t["draft_revision"], "text": text,
                "references": [{"type": r["type"], "id": r.get("id")} for r in t["references"]],
            }
        return extra

    async def _after_save(self, message: dict) -> None:
        if message.get("candidate"):
            await self.emit("assist_candidate", str(message["_id"]))


async def build_target(
    db: AsyncDatabase, project: dict, doc_id: str, section_id: str, action: str, request: str, references: list
) -> dict:
    """요청 당시의 문서·항목·버전과 고른 참고. 응답은 이 대상에만 적용된다."""
    doc = await structured_docs.get_doc(db, project["_id"], doc_id, active=True)
    template = templates.get(doc["template_id"], doc["template_version"])
    meta = template.section(section_id)
    stored = await db.doc_sections.find_one({"doc_id": doc["_id"], "section_id": section_id}) if meta else None
    if stored is None:
        raise not_found()
    context = await item_context.get(db, project["_id"], doc_id, section_id)
    refs = references if references is not None else [Reference.model_validate(r) for r in context["references"]]
    scope = context["scope"]
    scope["_target_doc_id"] = doc_id
    scope["message_ids"] = list(dict.fromkeys([*scope["message_ids"], *(r.id for r in refs if r.type == "message" and r.id)]))
    used = []
    reader = SourceReader(db, project, project["user_id"], scope, used)
    resolved, images = [], []
    for ref in refs:
        if ref.type == "note":
            resolved.append(await _one_reference(db, project, doc, ref))
            continue
        key = {"plan": ref.id, "brand": "linked_brand", "section": f"target:{doc_id}:{ref.id}"}.get(ref.type, f"{ref.type}:{ref.id}")
        if not reference_scope.allows(scope, key):
            continue
        if ref.type == "message":
            if not await reader.available(key):
                raise not_found()
            selected = await reader.selected_messages()
            if key not in selected:
                raise _invalid("선택한 대화의 출처를 현재 참고 범위에서 사용할 수 없습니다. 참고를 해제하거나 다시 선택해 주세요.")
            value = {"type": ref.type, "id": ref.id, **selected[key]}
        elif ref.type in ("file", "product", "contact", "record"):
            asset = await db.assets.find_one({"_id": parse_object_id(ref.id), "user_id": project["user_id"]}) if ref.type == "file" else None
            if asset and asset.get("mime_type") in ai_images.IMAGE_TYPES:
                if not get_settings().ai_read_images or len(images) >= ai_images.MAX_IMAGES:
                    raise _invalid("이미지 읽기가 꺼져 있거나 한 요청의 이미지 수를 초과했습니다.")
                inputs, read = await ai_images.image_inputs(db, project["user_id"], [asset["_id"]])
                if not read:
                    raise _invalid("선택 이미지를 읽지 못했습니다.")
                images.extend(inputs)
                value = {"type": "file", "id": ref.id, "title": asset["file_name"], "content": "The selected original image is included in this request."}
                resolved.append(value)
                used.extend(reference_scope.manifest({key: value}))
                continue
            value = await reader.read(key)
            if value["status"] == "unavailable":
                raise not_found()
            if value["status"] not in ("read", "partial"):
                raise _invalid(f"선택 자료를 읽지 못했습니다: {value['status']}")
            # Every explicitly selected file is read fully, within the item context budget.
            while value.get("next_offset") is not None:
                more = await reader.read(key, value["next_offset"])
                value["content"] += more.get("content", "")
                value["next_offset"] = more.get("next_offset")
                if len(value["content"]) > 100000:
                    raise _invalid("선택 자료가 너무 큽니다. 필요한 내용을 나누어 선택해 주세요.")
            value.update(type=ref.type, id=ref.id)
        else:
            value = await _one_reference(db, project, doc, ref)
        resolved.append(value)
        used.extend(reference_scope.manifest({key: value}))
    saved_sections = []
    for product in doc.get("product_snapshots", []):
        key = f"product:{product['_id']}"
        if await reader.available(key):
            from fastapi.encoders import jsonable_encoder
            from app.schemas.common import to_api
            saved_sections.append({"key": key, "product_snapshot_at_document_creation": jsonable_encoder(to_api(product)), "revision": product["revision"]})
            photo_ids = [ObjectId(ident) for ident in product.get("photo_ids", []) if await reader.available(f"file:{ident}")]
            if get_settings().ai_read_images and photo_ids and len(images) < ai_images.MAX_IMAGES:
                inputs, read = await ai_images.image_inputs(db, project["user_id"], photo_ids[:ai_images.MAX_IMAGES - len(images)])
                images.extend(inputs)
                used.extend({"key": f"file:{ident}", "kind": "file", "title": "선택 제품 사진", "status": "read"} for ident in read)
                saved_sections[-1]["image_read_count"] = len(read)
            saved_sections[-1]["image_total_count"] = len(product.get("photo_ids", []))
    async for other in db.doc_sections.find({"doc_id": doc["_id"]}):
        key = f"target:{doc_id}:{other['section_id']}"
        if other["section_id"] != section_id and other.get("text") and reference_scope.allows(scope, key):
            saved_sections.append({"key": key, "section_id": other["section_id"], "saved_text": other["text"], "revision": other["revision"]})
    used.extend(reference_scope.manifest({s["key"]: s for s in saved_sections}))
    used.extend(reference_scope.manifest({f"target:{doc_id}:{section_id}": stored}))
    history, history_keys = [], set()
    async for message in db.messages.find({"project_id": project["_id"], "target_doc_id": doc["_id"], "target_section_id": section_id,
                                           "purpose": PURPOSE}).sort("ordinal", 1):
        deps = message.get("source_keys")
        if deps is None and reference_scope.restricted(scope):
            continue
        if all([await reader.available(key) for key in (deps or [])]):
            history.append(ai_context._history_item(message))
            history_keys.update(deps or [])
    return {
        "doc_id": doc["_id"], "doc_title": doc["title"], "doc_name": template.name, "section_id": section_id,
        "title": meta.title, "question": meta.question, "guide": meta.guide, "text": stored.get("text") or "",
        "draft": stored.get("draft"), "revision": stored.get("revision", 0),
        "draft_revision": stored.get("draft_revision", 0), "action": action, "request": request,
        "references": resolved, "scope": scope, "scope_revision": context["revision"], "source_manifest": used,
        "saved_sections": saved_sections, "history": history, "history_source_keys": list(history_keys),
        "images": images,
    }


def shown_text(target: dict, label: str) -> str:
    request = target["request"].strip()
    return f"[{target['title']}] {label}" + (f": {request}" if request else "")


async def start_assist(
    db: AsyncDatabase, runtime: Runtime, user_id: ObjectId, project: dict, doc_id: str, section_id: str, body
) -> ObjectId:
    target = await build_target(db, project, doc_id, section_id, body.action, body.request, body.references)
    shown = shown_text(target, ACTIONS[body.action])
    extra = {"purpose": PURPOSE, "target_doc_id": target["doc_id"], "target_section_id": section_id}

    def make_job(run_id: ObjectId, message: dict) -> AssistJob:
        return AssistJob(db, runtime, user_id, project["_id"], run_id, message["_id"], shown, target=target)

    return await ai_chat.launch(db, runtime, user_id, project["_id"], shown, make_job, extra=extra)


async def apply_candidate(
    db: AsyncDatabase, project_id: ObjectId, doc_id: str, section_id: str, message_id: str,
    draft_revision: int, confirm: bool,
) -> dict:
    """후보를 임시 입력에 넣는다. 저장·검토 완료는 사용자가 따로 한다."""
    doc = await structured_docs.get_doc(db, project_id, doc_id, active=True)
    message = await db.messages.find_one({"_id": parse_object_id(message_id), "project_id": project_id})
    if message is None:
        raise not_found()
    candidate = message.get("candidate")
    if not candidate:
        raise AppError(422, "NOT_A_CANDIDATE", "AI 후보가 있는 답변이 아닙니다.")
    if candidate["doc_id"] != doc["_id"] or candidate["section_id"] != section_id:
        raise AppError(422, "CANDIDATE_MISMATCH", "다른 문서나 항목을 위해 만든 후보입니다.")
    stored = await db.doc_sections.find_one({"doc_id": doc["_id"], "section_id": section_id})
    if stored is None:
        raise not_found()
    outdated = (stored.get("revision"), stored.get("draft_revision")) != (
        candidate["base_revision"], candidate["base_draft_revision"]
    )
    if outdated and not confirm:
        raise AppError(409, "CANDIDATE_OUTDATED", "후보를 만든 뒤 글이 바뀌었습니다. 비교한 뒤 적용할지 정해 주세요.")
    updated = await db.doc_sections.find_one_and_update(
        {"_id": stored["_id"], "draft_revision": draft_revision},
        {"$set": {"draft": candidate["text"], "updated_at": utcnow()}, "$inc": {"draft_revision": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise conflict()
    await structured_docs.touch(db, doc["_id"], section_id)
    meta = templates.get(doc["template_id"], doc["template_version"]).section(section_id)
    return structured_docs.section_view(meta, updated)


async def section_messages(db: AsyncDatabase, project_id: ObjectId, doc_id: str, section_id: str) -> list[dict]:
    doc = await structured_docs.get_doc(db, project_id, doc_id)
    cursor = db.messages.find(
        {"project_id": project_id, "target_doc_id": doc["_id"], "target_section_id": section_id}
    ).sort("ordinal", 1)
    return await tasks.annotate_proposals(db, await cursor.to_list())
