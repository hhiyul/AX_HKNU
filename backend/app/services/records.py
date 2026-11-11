"""추가 기록. 6개 기획 항목에 들어가지 않는 내용을 프로젝트당 한 문서에 모은다."""
import re

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.services.ownership import not_found, parse_object_id
from app.services.projects import touch_project
from app.services.revisions import upsert_revision, utcnow

MAX_ENTRIES = 200
SIMILAR_THRESHOLD = 0.5


def view(project_id: ObjectId, document: dict | None) -> dict:
    document = document or {}
    return {
        "project_id": project_id,
        "entries": document.get("entries", []),
        "revision": document.get("revision", 0),
        "updated_at": document.get("updated_at"),
    }


async def _find(db: AsyncDatabase, project_id: ObjectId) -> dict | None:
    return await db.records.find_one({"project_id": project_id})


async def get_record(db: AsyncDatabase, project_id: ObjectId) -> dict:
    return view(project_id, await _find(db, project_id))


async def _save(
    db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, existing: dict | None, entries: list, revision: int
) -> dict:
    ident = {"user_id": user_id, "project_id": project_id}
    document = await upsert_revision(db.records, ident, existing, {"entries": entries}, revision)
    await touch_project(db, project_id)
    return view(project_id, document)


async def add_entry(
    db: AsyncDatabase,
    user_id: ObjectId,
    project_id: ObjectId,
    title: str,
    content: str,
    state: str,
    revision: int,
    source: str = "USER",
    source_ref: ObjectId | None = None,
) -> dict:
    existing = await _find(db, project_id)
    entries = list((existing or {}).get("entries", []))
    if len(entries) >= MAX_ENTRIES:
        raise AppError(422, "RECORD_FULL", f"추가 기록은 {MAX_ENTRIES}개까지 저장할 수 있습니다.")
    now = utcnow()
    entries.append(
        {
            "entry_id": ObjectId(),
            "title": title,
            "content": content,
            "state": state,
            "source": source,
            "source_ref": source_ref,
            "created_at": now,
            "updated_at": now,
        }
    )
    return await _save(db, user_id, project_id, existing, entries, revision)


def _locate(existing: dict | None, entry_id: str) -> tuple[list, int]:
    target = parse_object_id(entry_id)
    entries = list((existing or {}).get("entries", []))
    for index, entry in enumerate(entries):
        if entry["entry_id"] == target:
            return entries, index
    raise not_found()


async def update_entry(
    db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, entry_id: str, changes: dict, revision: int
) -> dict:
    existing = await _find(db, project_id)
    entries, index = _locate(existing, entry_id)
    entries[index] = {**entries[index], **changes, "updated_at": utcnow()}
    return await _save(db, user_id, project_id, existing, entries, revision)


async def delete_entry(
    db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, entry_id: str, revision: int
) -> dict:
    existing = await _find(db, project_id)
    entries, index = _locate(existing, entry_id)
    del entries[index]
    return await _save(db, user_id, project_id, existing, entries, revision)


def _grams(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", text.lower())
    return {compact[i : i + 2] for i in range(len(compact) - 1)} or {compact}


async def find_similar(db: AsyncDatabase, project_id: ObjectId, title: str, content: str) -> list[dict]:
    """비슷한 기존 기록을 찾아 준다. 알려주기만 하고 저장을 막지는 않는다."""
    wanted = _grams(f"{title} {content}")
    wanted_title = title.strip().lower()
    found = []
    for entry in (await get_record(db, project_id))["entries"]:
        grams = _grams(f"{entry['title']} {entry['content']}")
        score = len(wanted & grams) / len(wanted | grams)
        if wanted_title and wanted_title == entry["title"].strip().lower():
            score = 1.0
        if score >= SIMILAR_THRESHOLD:
            found.append({"score": round(score, 2), "entry": entry})
    return sorted(found, key=lambda item: item["score"], reverse=True)[:5]
