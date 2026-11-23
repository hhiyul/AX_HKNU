"""AI가 만든 문서가 어떤 자료로 만들어졌는지(출처) 기록하고, 그 자료가 그 뒤 바뀌었는지 확인한다.

출처 종류: plan(기획 항목), record(AI와 함께 정리한 내용), reference_document(참고한 산출물),
attachment(참고 파일), web(검색 출처 주소). 상태는 unchanged, changed, unavailable(지워졌거나 확인할 수 없음).
"""
from bson import ObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.database import AsyncDatabase

from app.services.store import not_found


def collect(sections: dict[str, dict], web_urls: list[str]) -> list[dict]:
    """AI에게 넘긴 자료에서 출처 목록을 만든다. 참고 프로젝트의 자료는 넣지 않는다."""
    sources: list[dict] = []
    for key, section in sections.items():
        if key.startswith("ck_"):
            sources.append({"type": "plan", "id": key, "revision": section.get("revision")})
        elif key.startswith("record:"):
            sources.append({"type": "record", "id": key.split(":", 1)[1], "revision": section.get("revision")})
        elif key.startswith("file:"):
            sources.append({"type": "attachment", "id": key.split(":", 1)[1], "revision": None})
        elif key == "reference_document":
            sources.append({
                "type": "reference_document", "id": section["id"], "revision": section.get("revision"),
                "source": section.get("source"),
            })
    sources += [{"type": "web", "id": url, "revision": None} for url in dict.fromkeys(web_urls)]
    return sources


def _compare(current: int | None, saved: int | None) -> str:
    if current is None or saved is None:
        return "unavailable"
    return "unchanged" if current == saved else "changed"


async def _item_revision(db: AsyncDatabase, project_id: ObjectId, kind: str, key: str) -> int | None:
    item = await db.items.find_one({"project_id": project_id, "kind": kind, "key": key}, {"revision": 1})
    if item is None:
        # 6개를 함께 만들기 전의 프로젝트는 기획 항목 문서가 없을 수 있다. 그때 revision은 0이다.
        return 0 if kind == "plan" else None
    return item.get("revision")


async def _status(db: AsyncDatabase, project_id: ObjectId, source: dict) -> str:
    kind = source.get("type")
    if kind == "plan":
        return _compare(await _item_revision(db, project_id, "plan", source["id"]), source.get("revision"))
    if kind == "record":
        return _compare(await _item_revision(db, project_id, "record", source["id"]), source.get("revision"))
    if kind == "reference_document":
        if source.get("source") == "editor":
            # 저장하지 않은 편집 중인 글을 참고했으면 지금 내용과 비교할 수 없다.
            return "unavailable"
        return _compare(await _item_revision(db, project_id, "document", source["id"]), source.get("revision"))
    if kind == "attachment":
        try:
            exists = await db.assets.find_one({"_id": ObjectId(source["id"])}, {"_id": 1})
        except (InvalidId, TypeError):
            exists = None
        return "unchanged" if exists else "unavailable"
    return "unavailable"


async def document_changes(db: AsyncDatabase, project_id: ObjectId, key: str) -> dict:
    document = await db.items.find_one({"project_id": project_id, "kind": "document", "key": key})
    if document is None:
        raise not_found()
    sources = list(document.get("sources") or [])
    changes = [
        {"type": source.get("type"), "id": source.get("id"), "status": await _status(db, project_id, source)}
        for source in sources
    ]
    return {"sources": sources, "changes": changes}
