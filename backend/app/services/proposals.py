"""AI 제안. 제안은 저장된 기획과 따로 보관하고, 사용자가 반영을 눌러야 기획에 들어간다."""
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.services import items
from app.services.store import get_owned, not_found, parse_object_id, utcnow

OPEN_STATES = ["PENDING", "HELD"]


def closed() -> AppError:
    return AppError(409, "PROPOSAL_CLOSED", "이미 반영했거나 거절한 제안입니다.")


async def _revisions(db: AsyncDatabase, project_id: ObjectId) -> dict[str, int]:
    """제안 대상별 현재 revision. 추가 기록은 늘 새로 만들어지므로 0이다."""
    current = {item["key"]: item["revision"] for item in await items.list_items(db, project_id, "plan")}
    current["record"] = 0
    return current


def view(proposal: dict, revisions: dict[str, int]) -> dict:
    current = revisions[proposal["target"]]
    return {
        **proposal,
        "current_revision": current,
        # 열려 있는 제안만 의미가 있다. 제안을 만든 뒤 대상이 바뀌었는지 알려 준다.
        "target_changed": proposal["state"] in OPEN_STATES and current != proposal["base_revision"],
    }


async def create_proposal(
    db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, target: str, content: str, message_id: str | None
) -> dict:
    message = None
    if message_id is not None:
        message = await db.messages.find_one({"_id": parse_object_id(message_id), "project_id": project_id})
        if message is None:
            raise not_found()
    revisions = await _revisions(db, project_id)
    now = utcnow()
    proposal = {
        "user_id": user_id,
        "project_id": project_id,
        "target": target,
        "content": content,
        "message_id": message["_id"] if message else None,
        "state": "PENDING",
        "base_revision": revisions[target],
        "applied_content": None,
        "decided_at": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.proposals.insert_one(proposal)
    return view(proposal, revisions)


async def list_proposals(db: AsyncDatabase, project_id: ObjectId, state: str | None, page) -> list[dict]:
    query = {"project_id": project_id}
    if state is not None:
        query["state"] = state
    cursor = db.proposals.find(query).sort("created_at", -1)
    revisions = await _revisions(db, project_id)
    return [view(doc, revisions) for doc in await cursor.skip(page.skip).limit(page.limit).to_list()]


async def get_proposal(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, proposal_id: str) -> dict:
    proposal = await get_owned(db, "proposals", user_id, proposal_id, project_id=project_id)
    return view(proposal, await _revisions(db, project_id))


async def _close(db: AsyncDatabase, proposal: dict, changes: dict) -> dict:
    """열려 있는 제안의 상태만 바꾼다. 그 사이에 닫혔으면 409."""
    updated = await db.proposals.find_one_and_update(
        {"_id": proposal["_id"], "state": {"$in": OPEN_STATES}},
        {"$set": {**changes, "updated_at": utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise closed()
    return view(updated, await _revisions(db, proposal["project_id"]))


async def set_state(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, proposal_id: str, state: str) -> dict:
    """보류(HELD)하거나 거절(REJECTED)한다. 저장된 기획은 바뀌지 않는다."""
    proposal = await get_owned(db, "proposals", user_id, proposal_id, project_id=project_id)
    changes = {"state": state}
    if state == "REJECTED":
        changes["decided_at"] = utcnow()
    return await _close(db, proposal, changes)


async def apply_proposal(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, proposal_id: str, body) -> dict:
    """사용자가 고른 내용을 대상에 저장한다. 기획 항목이 그 사이 바뀌었으면 409로 돌려보낸다."""
    proposal = await get_owned(db, "proposals", user_id, proposal_id, project_id=project_id)
    if proposal["state"] not in OPEN_STATES:
        raise closed()
    origin = {"source": "AI", "source_ref": proposal["_id"]}
    if proposal["target"] == "record":
        if body.title is None:
            raise AppError(422, "TITLE_REQUIRED", "추가 기록으로 반영하려면 제목을 입력해 주세요.")
        if body.amount is not None:
            raise AppError(422, "AMOUNT_NOT_ALLOWED", "금액은 가격·원가 항목에만 넣을 수 있습니다.")
        await items.create_item(db, user_id, project_id, "record", body, **origin)
    else:
        await items.save_item(
            db, user_id, project_id, "plan", proposal["target"], body.state, body.content, body.revision,
            amount=body.amount, **origin,
        )
    return await _close(db, proposal, {"state": "APPLIED", "applied_content": body.content, "decided_at": utcnow()})
