"""AI 채팅 메시지. 프로젝트마다 대화가 하나 있고, 메시지는 순번대로 쌓인다.

채팅 설정(검색 사용, 참고 파일·프로젝트)은 프로젝트 문서에 있다.
AI 답변을 만드는 일은 파이프라인의 몫이다. 파이프라인은 add_message(role="ASSISTANT")로 답변을 남기면 된다.
"""
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.services.store import not_found, owned_ids, utcnow


async def list_messages(db: AsyncDatabase, project_id: ObjectId, page) -> list[dict]:
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
    assets = await owned_ids(db, "assets", user_id, asset_ids or [])
    now = utcnow()
    # 순번은 프로젝트 문서에서 원자적으로 받아 온다. 프로젝트의 revision은 건드리지 않는다.
    project = await db.projects.find_one_and_update(
        {"_id": project_id},
        {"$inc": {"message_count": 1}, "$set": {"updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if project is None:
        raise not_found()
    message = {
        "user_id": user_id,
        "project_id": project_id,
        "ordinal": project["message_count"],
        "role": role,
        "content": content,
        "asset_ids": assets,
        "completion": completion,
        "created_at": now,
    }
    await db.messages.insert_one(message)
    return message
