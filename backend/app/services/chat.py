"""AI 채팅 메시지. 메시지는 프로젝트 안에서 순번대로 쌓이고, 채팅 메시지는 conversation_id로 대화에 속한다.

채팅 설정(검색 사용, 참고 파일·프로젝트)은 프로젝트 문서에 있다.
AI 답변을 만드는 일은 파이프라인의 몫이다. 파이프라인은 add_message(role="ASSISTANT")로 답변을 남기면 된다.
답변이 문서 초안을 담고 있으면 doc={"title": ..., "content": ...}로 함께 넘긴다.
"""
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.services import conversations
from app.services.store import not_found, owned_ids, recent_push, utcnow


async def list_messages(db: AsyncDatabase, project_id: ObjectId, page, conversation_id: ObjectId | None = None) -> list[dict]:
    """conversation_id를 주면 그 대화의 메시지만, 비우면 프로젝트의 모든 메시지(예전 화면과 호환)."""
    query: dict = {"project_id": project_id}
    if conversation_id is not None:
        query["conversation_id"] = conversation_id
    cursor = db.messages.find(query).sort("ordinal", 1)
    return await cursor.skip(page.skip).limit(page.limit).to_list()


async def add_message(
    db: AsyncDatabase,
    user_id: ObjectId,
    project_id: ObjectId,
    role: str,
    content: str,
    asset_ids: list[str] | None = None,
    completion: str = "COMPLETE",
    doc: dict | None = None,
    source_ids: list[ObjectId] | None = None,
    record_draft: dict | None = None,
    extra: dict | None = None,
    conversation_id: ObjectId | None = None,
) -> dict:
    assets = await owned_ids(db, "assets", user_id, asset_ids or [])
    now = utcnow()
    # 순번은 프로젝트 문서에서 원자적으로 받아 온다. 프로젝트의 revision은 건드리지 않는다.
    update = {"$inc": {"message_count": 1}, "$set": {"updated_at": now}}
    if role == "USER":
        update["$push"] = recent_push("AI 채팅 · 대화 추가")
    project = await db.projects.find_one_and_update(
        {"_id": project_id}, update, return_document=ReturnDocument.AFTER
    )
    if project is None:
        raise not_found()
    message = {
        "user_id": user_id,
        "project_id": project_id,
        "ordinal": project["message_count"],
        # 채팅 대화. 항목별 AI·할 일 제안 메시지는 None이다.
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "asset_ids": assets,
        "completion": completion,
        # AI가 만든 문서 초안. 사용자가 "산출물로 저장"을 눌러야 산출물이 된다.
        "doc": doc,
        # AI 답변이 읽은 참고 파일. 나중에 그 파일을 참고에서 빼면 이 답변은 이후 대화에 쓰지 않는다.
        "source_ids": list(source_ids or []),
        # AI가 만든 기록 검토안 {title, content}. "검수하고 저장하기"의 기본값으로 쓴다.
        "record_draft": record_draft,
        # 항목별 AI·할 일 제안의 연결 정보(purpose, target_doc_id, target_section_id, request_id, candidate, proposals)
        **(extra or {}),
        # 이 답변에서 저장한 정리 내용과 산출물의 key. 저장하지 않았으면 None.
        "record_key": None,
        "doc_key": None,
        "created_at": now,
    }
    await db.messages.insert_one(message)
    if conversation_id is not None:
        await conversations.touch(db, conversation_id, role, content, now)
    return message
