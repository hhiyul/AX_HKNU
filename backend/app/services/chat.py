"""AI 채팅. 프로젝트마다 채팅이 하나 있고, 메시지는 순번대로 쌓인다.

AI 답변을 만드는 일은 파이프라인의 몫이다. 여기서는 채팅 설정과 메시지 저장만 한다.
파이프라인은 add_message(role="ASSISTANT")로 답변을 남기면 된다.
"""
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.services.ownership import not_found, parse_object_id
from app.services.projects import touch_project
from app.services.revisions import conflict, save_revision, utcnow


async def get_chat(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId) -> dict:
    """프로젝트의 채팅을 꺼낸다. 처음이면 만든다."""
    chat = await db.chats.find_one({"project_id": project_id})
    if chat is not None:
        return chat
    now = utcnow()
    chat = {
        "user_id": user_id,
        "project_id": project_id,
        "search_enabled": False,
        "file_refs": [],
        "chip_refs": [],
        "message_count": 0,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await db.chats.insert_one(chat)
    except DuplicateKeyError:
        # 동시에 들어온 다른 요청이 먼저 만든 경우
        return await db.chats.find_one({"project_id": project_id})
    return chat


async def _owned_ids(db: AsyncDatabase, collection: str, user_id: ObjectId, ids: list[str]) -> list[ObjectId]:
    """내 자료의 id만 통과시킨다. 하나라도 없거나 남의 것이면 404."""
    wanted = list(dict.fromkeys(parse_object_id(value) for value in ids))
    if wanted:
        count = await db[collection].count_documents({"_id": {"$in": wanted}, "user_id": user_id})
        if count != len(wanted):
            raise not_found()
    return wanted


async def update_chat(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, changes: dict, revision: int) -> dict:
    chat = await get_chat(db, user_id, project_id)
    if "file_refs" in changes:
        changes["file_refs"] = await _owned_ids(db, "assets", user_id, changes["file_refs"])
    if "chip_refs" in changes:
        refs = await _owned_ids(db, "projects", user_id, changes["chip_refs"])
        # 자기 프로젝트는 참고 자료로 걸 수 없다.
        if project_id in refs:
            raise not_found()
        changes["chip_refs"] = refs
    if not changes:
        if revision != chat["revision"]:
            raise conflict()
        return chat
    return await save_revision(db.chats, chat, changes, revision)


async def list_messages(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, page) -> list[dict]:
    cursor = db.messages.find({"project_id": project_id}).sort("ordinal", 1)
    return await cursor.skip(page.skip).limit(page.limit).to_list()


async def add_message(
    db: AsyncDatabase,
    user_id: ObjectId,
    project_id: ObjectId,
    role: str,
    content: str,
    asset_ids: list[str] | None = None,
    completion: str = "COMPLETE",
) -> dict:
    chat = await get_chat(db, user_id, project_id)
    assets = await _owned_ids(db, "assets", user_id, asset_ids or [])
    now = utcnow()
    # 순번은 채팅 문서에서 원자적으로 받아 온다. 설정 revision은 건드리지 않는다.
    chat = await db.chats.find_one_and_update(
        {"_id": chat["_id"]},
        {"$inc": {"message_count": 1}, "$set": {"updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    message = {
        "user_id": user_id,
        "project_id": project_id,
        "chat_id": chat["_id"],
        "ordinal": chat["message_count"],
        "role": role,
        "content": content,
        "asset_ids": assets,
        "completion": completion,
        "created_at": now,
    }
    await db.messages.insert_one(message)
    await touch_project(db, project_id)
    return message
