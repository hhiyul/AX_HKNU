"""정산 수입·지출 내역(ledger_entries). 실제로 들어오고 나간 돈을 프로젝트별로 적는다.

원가·판매가격 비교(cost_sheets)와는 따로다. 내역을 적어도 판매가나 비용 입력은 바뀌지 않는다.
지우면 휴지통으로 가고 복원할 수 있다. 합계는 휴지통에 없는 내역만 더한다.
"""
from bson import Int64, ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.services.store import conflict, not_found, parse_object_id, recent_push, utcnow

TYPE_LABELS = {"income": "수입", "expense": "지출"}


def view(entry: dict) -> dict:
    return {
        **{key: value for key, value in entry.items() if key != "user_id"},
        "amount": int(entry["amount"]),
        "status": "trash" if entry.get("deleted_at") else "active",
    }


async def _log(db: AsyncDatabase, project_id: ObjectId, text: str) -> None:
    await db.projects.update_one({"_id": project_id}, {"$push": recent_push(text), "$set": {"updated_at": utcnow()}})


async def list_entries(db: AsyncDatabase, project_id: ObjectId, status: str) -> dict:
    query = {"project_id": project_id, "deleted_at": {"$ne": None} if status == "trash" else None}
    cursor = db.ledger_entries.find(query).sort([("date", -1), ("created_at", -1)])
    entries = [view(entry) async for entry in cursor]
    totals = {"income": 0, "expense": 0}
    async for row in await db.ledger_entries.aggregate([
        {"$match": {"project_id": project_id, "deleted_at": None}},
        {"$group": {"_id": "$type", "total": {"$sum": "$amount"}}},
    ]):
        totals[row["_id"]] = int(row["total"])
    return {**totals, "entries": entries}


async def create_entry(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, body) -> dict:
    now = utcnow()
    entry = {
        "user_id": user_id, "project_id": project_id, "type": body.type, "title": body.title,
        "amount": Int64(body.amount), "date": body.date.isoformat(), "note": body.note,
        "deleted_at": None, "revision": 1, "created_at": now, "updated_at": now,
    }
    entry["_id"] = (await db.ledger_entries.insert_one(entry)).inserted_id
    await _log(db, project_id, f"정산 내역 추가 · {TYPE_LABELS[body.type]} {body.amount:,}원")
    return view(entry)


async def _get(db: AsyncDatabase, project_id: ObjectId, entry_id: str) -> dict:
    entry = await db.ledger_entries.find_one({"_id": parse_object_id(entry_id), "project_id": project_id})
    if entry is None:
        raise not_found()
    return entry


async def update_entry(db: AsyncDatabase, project_id: ObjectId, entry_id: str, changes: dict, revision: int) -> dict:
    entry = await _get(db, project_id, entry_id)
    if "date" in changes:
        changes["date"] = changes["date"].isoformat()
    if "amount" in changes:
        changes["amount"] = Int64(changes["amount"])
    updated = await db.ledger_entries.find_one_and_update(
        {"_id": entry["_id"], "revision": revision},
        {"$set": {**changes, "updated_at": utcnow()}, "$inc": {"revision": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise conflict()
    return view(updated)


async def set_trashed(db: AsyncDatabase, project_id: ObjectId, entry_id: str, trashed: bool) -> dict:
    entry = await _get(db, project_id, entry_id)
    now = utcnow()
    updated = await db.ledger_entries.find_one_and_update(
        {"_id": entry["_id"]},
        {"$set": {"deleted_at": now if trashed else None, "updated_at": now}, "$inc": {"revision": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if trashed:
        await _log(db, project_id, f"정산 내역 삭제 · {entry['title']}")
    return view(updated)
