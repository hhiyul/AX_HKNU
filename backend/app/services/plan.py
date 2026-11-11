"""상품기획 6개 항목. 항목마다 초안과 확정본을 따로 갖고, 확정할 때마다 이력을 남긴다."""
from bson import Int64, ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.services.projects import touch_project
from app.services.revisions import conflict, save_revision, upsert_revision, utcnow

ITEM_LABELS = {
    "ck_01": "상품 선정",
    "ck_02": "고객 선정",
    "ck_03": "구매 가치",
    "ck_04": "상품 구성",
    "ck_05": "가격·원가",
    "ck_06": "제작 가능성",
}
STATE_LABELS = {"UNSET": "미작성", "DRAFT": "초안", "DECIDED": "확정"}


def view(item_key: str, document: dict | None) -> dict:
    document = document or {}
    draft, decided = document.get("draft"), document.get("decided")
    current = draft or decided or {}
    return {
        "item_key": item_key,
        "label": ITEM_LABELS[item_key],
        "state": "DRAFT" if draft else "DECIDED" if decided else "UNSET",
        "content": current.get("content"),
        "amount": current.get("amount"),
        "draft": draft,
        "decided": decided,
        # 아직 한 번도 저장하지 않은 항목은 0
        "revision": document.get("revision", 0),
        "updated_at": document.get("updated_at"),
    }


async def _find(db: AsyncDatabase, project_id: ObjectId, item_key: str) -> dict | None:
    return await db.plan_items.find_one({"project_id": project_id, "item_key": item_key})


async def get_item(db: AsyncDatabase, project_id: ObjectId, item_key: str) -> dict:
    return view(item_key, await _find(db, project_id, item_key))


async def list_items(db: AsyncDatabase, project_id: ObjectId) -> list[dict]:
    documents = {doc["item_key"]: doc async for doc in db.plan_items.find({"project_id": project_id})}
    return [view(key, documents.get(key)) for key in ITEM_LABELS]


async def decided_versions(db: AsyncDatabase, project_id: ObjectId) -> dict[str, int]:
    """항목별 현재 확정본의 버전. 확정본이 없으면 0."""
    items = await list_items(db, project_id)
    return {item["item_key"]: (item["decided"] or {}).get("version", 0) for item in items}


async def save_item(
    db: AsyncDatabase,
    user_id: ObjectId,
    project_id: ObjectId,
    item_key: str,
    kind: str,
    content: str,
    amount: int | None,
    revision: int,
    source: str = "USER",
    source_ref: ObjectId | None = None,
) -> dict:
    if amount is not None and item_key != "ck_05":
        raise AppError(422, "AMOUNT_NOT_ALLOWED", "금액은 가격·원가 항목에만 넣을 수 있습니다.")
    existing = await _find(db, project_id, item_key)
    now = utcnow()
    part = {
        "content": content,
        "amount": Int64(amount) if amount is not None else None,
        "source": source,
        "source_ref": source_ref,
    }
    ident = {"user_id": user_id, "project_id": project_id, "item_key": item_key}
    if kind == "DRAFT":
        # 확정본은 그대로 두고 초안만 바꾼다. 확정하기 전까지 기준은 기존 확정본이다.
        changes = {"draft": {**part, "updated_at": now}}
        if existing is None:
            changes.update({"decided": None, "version": 0})
    else:
        version = (existing or {}).get("version", 0) + 1
        changes = {"draft": None, "decided": {**part, "version": version, "decided_at": now}, "version": version}
    document = await upsert_revision(db.plan_items, ident, existing, changes, revision)
    if kind == "DECIDED":
        await db.plan_versions.insert_one({**ident, **part, "version": version, "decided_at": now})
    await touch_project(db, project_id)
    return view(item_key, document)


async def save_batch(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, items: list) -> list[dict]:
    """여러 항목을 함께 저장한다. 통과한 항목만 반영하고 결과를 항목별로 알려준다."""
    results = []
    for item in items:
        try:
            saved = await save_item(
                db, user_id, project_id, item.item_key, item.kind, item.content, item.amount, item.revision
            )
            results.append({"item_key": item.item_key, "status": "APPLIED", "item": saved})
        except AppError as exc:
            results.append(
                {"item_key": item.item_key, "status": "REJECTED", "code": exc.code, "message": exc.message}
            )
    return results


async def clear_item(db: AsyncDatabase, project_id: ObjectId, item_key: str, revision: int) -> dict:
    """항목을 통째로 비워 UNSET으로 되돌린다. 확정 이력은 남는다."""
    existing = await _find(db, project_id, item_key)
    if existing is None:
        if revision != 0:
            raise conflict()
        return view(item_key, None)
    document = await save_revision(db.plan_items, existing, {"draft": None, "decided": None}, revision)
    await touch_project(db, project_id)
    return view(item_key, document)


async def discard_draft(db: AsyncDatabase, project_id: ObjectId, item_key: str, revision: int) -> dict:
    """초안만 버린다. 확정본이 있으면 그 상태로 돌아간다."""
    existing = await _find(db, project_id, item_key)
    if existing is None or not existing.get("draft"):
        raise AppError(422, "NO_DRAFT", "버릴 초안이 없습니다.")
    document = await save_revision(db.plan_items, existing, {"draft": None}, revision)
    await touch_project(db, project_id)
    return view(item_key, document)


async def list_versions(db: AsyncDatabase, project_id: ObjectId, item_key: str, page) -> list[dict]:
    cursor = db.plan_versions.find({"project_id": project_id, "item_key": item_key}).sort("version", -1)
    return await cursor.skip(page.skip).limit(page.limit).to_list()


def export_text(project: dict, items: list[dict]) -> str:
    """지금 저장된 6개 항목의 내용과 상태를 그대로 글로 옮긴다. MD와 TXT가 같은 내용이다."""
    lines = [f"# {project.get('name') or '이름 없는 프로젝트'} 상품기획", ""]
    for item in items:
        lines.append(f"## {item['label']} ({STATE_LABELS[item['state']]})")
        lines.append("")
        if item["amount"] is not None:
            lines.append(f"판매가격: {item['amount']:,}원")
            lines.append("")
        lines.append(item["content"] or "(내용 없음)")
        lines.append("")
    return "\n".join(lines)
