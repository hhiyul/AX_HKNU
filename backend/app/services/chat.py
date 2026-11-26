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
    """conversation_id를 주면 그 대화의 최근 메시지 limit개를 시간순으로 준다(skip은 최근 쪽부터 건너뛸 개수).
    긴 대화에서도 방금 주고받은 메시지가 빠지지 않게 하려는 것이다.
    비우면 프로젝트의 모든 메시지를 처음부터 준다(예전 화면과 호환)."""
    if conversation_id is None:
        cursor = db.messages.find({"project_id": project_id}).sort("ordinal", 1)
        messages = await cursor.skip(page.skip).limit(page.limit).to_list()
    else:
        cursor = db.messages.find({"project_id": project_id, "conversation_id": conversation_id}).sort("ordinal", -1)
        newest_first = await cursor.skip(page.skip).limit(page.limit).to_list()
        messages = newest_first[::-1]
    # Restore terminal failures even when no assistant text was saved. Read only;
    # reloading a conversation must never dispatch another paid request.
    request_ids = [m["request_id"] for m in messages if m["role"] == "USER" and m.get("request_id")]
    outcomes = {r["_id"]: r async for r in db.ai_runs.find({
        "project_id": project_id, "_id": {"$in": request_ids},
        "status": {"$in": ["failed", "unknown", "stopped"]},
    }, {"status": 1, "delivery": 1, "error_code": 1, "user_message_id": 1})} if request_ids else {}
    for message in messages:
        outcome = outcomes.get(message.get("request_id"))
        if message["role"] == "USER" and outcome and outcome.get("user_message_id") == message["_id"]:
            message["run_outcome"] = {key: outcome.get(key) for key in ("status", "delivery", "error_code")}
    return messages


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
