"""항목별 AI 도움(기획서 v2의 D절). 기존 AI 실행 구조(잠금·SSE·중지·실패 처리)를 그대로 쓴다.

- AI에는 항목의 질문·가이드, 지금 본문과 임시 입력, 사용자 요청, 사용자가 고른 참고만 보낸다.
  이전 대화·다른 프로젝트·파일 원본은 보내지 않고 도구도 주지 않는다.
- 결과는 답변 메시지의 candidate로만 남는다. 본문·임시 입력·검토 상태는 바뀌지 않는다.
- 적용하면 후보를 임시 입력에 넣는다. 후보를 만든 뒤 본문이나 임시 입력이 바뀌었으면 확인 없이 덮지 않는다.
"""
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.ai.context import Budget, ContextError, prepare
from app.ai.runtime import Runtime
from app.errors import AppError
from app.services import ai_chat, ai_tools, ai_workspace, items, structured_docs, tasks, templates
from app.services.store import conflict, not_found, parse_object_id, utcnow

PURPOSE = "document_assist"
ACTIONS = {"draft": "초안 만들기", "refine": "현재 글 다듬기"}
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
    """항목 하나의 후보를 만드는 실행. 도구·이전 대화·자동 맥락 없이 고른 자료만 보낸다."""

    def __init__(self, db, runtime, user_id, project_id, run_id, user_message_id, text, *, target: dict):
        super().__init__(db, runtime, user_id, project_id, run_id, user_message_id, text)
        self.target = target

    def _prefix(self) -> list[dict]:
        t = self.target
        section = {
            "key": "target_section", "doc_title": t["doc_title"], "section_id": t["section_id"], "title": t["title"],
            "question": t["question"], "saved_text": t["text"], "draft": t["draft"],
        }
        # 오늘 날짜(한국)는 "다음 주 금요일" 같은 말을 날짜로 바꿀 때 쓴다.
        today = {"key": "today", **ai_workspace.today_section()}
        return [section, *({"key": "reference", **reference} for reference in t["references"]), today]

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
            self.provider, self._prefix(), [], self._instruction(), [], self.emit, self.runtime.model,
            self.budget, None, False, None, self._observe_count,
        )
        await self.emit("generating", "후보를 만들고 있습니다.")
        if await self._stream_once(prepared.wire):
            raise ContextError("TOOL_NOT_ALLOWED")

    def _message_extra(self, text: str, completion: str) -> dict:
        t = self.target
        extra = {
            "purpose": PURPOSE, "target_doc_id": t["doc_id"], "target_section_id": t["section_id"],
            "request_id": self.run_id,
        }
        if completion == "COMPLETE":
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
    return {
        "doc_id": doc["_id"], "doc_title": doc["title"], "doc_name": template.name, "section_id": section_id,
        "title": meta.title, "question": meta.question, "guide": meta.guide, "text": stored.get("text") or "",
        "draft": stored.get("draft"), "revision": stored.get("revision", 0),
        "draft_revision": stored.get("draft_revision", 0), "action": action, "request": request,
        "references": await resolve_references(db, project, doc, references),
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
