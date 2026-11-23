"""AI 채팅 대화 목록. 프로젝트 안에 대화를 여러 개 두고, 메시지는 conversation_id로 대화에 속한다.

- 항목별 AI·할 일 제안 메시지(purpose가 있는 것)는 어느 대화에도 속하지 않는다(conversation_id 없음).
- 대화가 생기기 전의 예전 채팅 메시지는 처음 필요할 때 "이전 대화" 하나로 옮긴다. 압축 요약도 함께 옮긴다.
- 이전 대화와 압축 요약은 대화마다 따로다. 다른 대화의 내용은 AI에게 넘기지 않는다.
- 휴지통의 대화는 목록에서 빠지고 메시지를 보낼 수 없다. 복원하면 그대로 이어서 쓴다.
"""
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.services.store import conflict, not_found, parse_object_id, utcnow

LEGACY_TITLE = "이전 대화"
TITLE_LENGTH = 40
PREVIEW_LENGTH = 80
# 예전 채팅 메시지: 대화 id가 없고 항목별 AI·할 일 제안도 아닌 것
LEGACY_FILTER = {"conversation_id": None, "purpose": None}


def _clip(text: str, length: int) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= length else line[: length - 1] + "…"


def view(conversation: dict) -> dict:
    return {
        **{key: value for key, value in conversation.items() if key not in ("user_id",)},
        "status": "trash" if conversation.get("deleted_at") else "active",
    }


async def _insert(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, title: str | None) -> dict:
    now = utcnow()
    conversation = {
        "user_id": user_id, "project_id": project_id, "title": title, "preview": "", "message_count": 0,
        "last_message_at": None, "deleted_at": None, "revision": 1, "created_at": now, "updated_at": now,
    }
    conversation["_id"] = (await db.conversations.insert_one(conversation)).inserted_id
    return conversation


async def _adopt_legacy(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId) -> dict | None:
    """대화가 하나도 없는 프로젝트의 예전 채팅 메시지를 "이전 대화"로 옮긴다. 옮길 것이 없으면 None."""
    if await db.conversations.count_documents({"project_id": project_id}, limit=1):
        return None
    legacy = {"project_id": project_id, **LEGACY_FILTER}
    last = await db.messages.find_one(legacy, sort=[("ordinal", -1)])
    if last is None:
        return None
    conversation = await _insert(db, user_id, project_id, LEGACY_TITLE)
    moved = await db.messages.update_many(legacy, {"$set": {"conversation_id": conversation["_id"]}})
    changes = {
        "message_count": moved.modified_count, "last_message_at": last["created_at"],
        "preview": _clip(last["content"], PREVIEW_LENGTH),
    }
    await db.conversations.update_one({"_id": conversation["_id"]}, {"$set": changes})
    # 프로젝트에 하나만 있던 압축 요약은 이 대화의 것이다.
    project = await db.projects.find_one({"_id": project_id}, {"chat_capsule": 1})
    if project and project.get("chat_capsule"):
        await db.projects.update_one(
            {"_id": project_id}, {"$set": {f"chat_capsules.{conversation['_id']}": project["chat_capsule"]}}
        )
    return {**conversation, **changes}


async def list_conversations(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, status: str) -> list[dict]:
    await _adopt_legacy(db, user_id, project_id)
    query = {"project_id": project_id, "deleted_at": {"$ne": None} if status == "trash" else None}
    cursor = db.conversations.find(query).sort([("last_message_at", -1), ("created_at", -1)])
    return [view(conversation) async for conversation in cursor]


async def create(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, title: str | None) -> dict:
    await _adopt_legacy(db, user_id, project_id)
    return view(await _insert(db, user_id, project_id, title or None))


async def _get(db: AsyncDatabase, project_id: ObjectId, conversation_id: str) -> dict:
    conversation = await db.conversations.find_one({"_id": parse_object_id(conversation_id), "project_id": project_id})
    if conversation is None:
        raise not_found()
    return conversation


async def get(db: AsyncDatabase, project_id: ObjectId, conversation_id: str) -> dict:
    return view(await _get(db, project_id, conversation_id))


async def rename(db: AsyncDatabase, project_id: ObjectId, conversation_id: str, title: str, revision: int) -> dict:
    conversation = await _get(db, project_id, conversation_id)
    updated = await db.conversations.find_one_and_update(
        {"_id": conversation["_id"], "revision": revision},
        {"$set": {"title": title, "updated_at": utcnow()}, "$inc": {"revision": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise conflict()
    return view(updated)


async def set_trashed(db: AsyncDatabase, project_id: ObjectId, conversation_id: str, trashed: bool) -> dict:
    conversation = await _get(db, project_id, conversation_id)
    now = utcnow()
    updated = await db.conversations.find_one_and_update(
        {"_id": conversation["_id"]},
        {"$set": {"deleted_at": now if trashed else None, "updated_at": now}, "$inc": {"revision": 1}},
        return_document=ReturnDocument.AFTER,
    )
    return view(updated)


async def resolve(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, conversation_id: str | None) -> ObjectId:
    """메시지를 보낼 대화. 비우면 가장 최근 대화, 대화가 없으면 새로 만든다(예전 화면과 호환)."""
    if conversation_id:
        conversation = await _get(db, project_id, conversation_id)
        if conversation.get("deleted_at"):
            raise AppError(409, "CONVERSATION_TRASHED", "휴지통에 있는 대화입니다. 복원한 뒤 이어서 쓰세요.")
        return conversation["_id"]
    await _adopt_legacy(db, user_id, project_id)
    latest = await db.conversations.find_one(
        {"project_id": project_id, "deleted_at": None}, sort=[("last_message_at", -1), ("created_at", -1)]
    )
    if latest is not None:
        return latest["_id"]
    return (await _insert(db, user_id, project_id, None))["_id"]


async def touch(db: AsyncDatabase, conversation_id: ObjectId, role: str, content: str, at) -> None:
    """메시지가 쌓일 때 목록용 정보(마지막 시각·미리보기·개수)를 고친다. 제목이 없으면 첫 질문으로 정한다."""
    await db.conversations.update_one(
        {"_id": conversation_id},
        {"$set": {"last_message_at": at, "updated_at": at, "preview": _clip(content, PREVIEW_LENGTH)},
         "$inc": {"message_count": 1}},
    )
    if role == "USER":
        await db.conversations.update_one(
            {"_id": conversation_id, "title": None}, {"$set": {"title": _clip(content, TITLE_LENGTH)}}
        )
