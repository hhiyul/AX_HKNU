"""프로젝트에 저장하는 글. 종류(kind)는 세 가지다.

  - plan      상품기획 6개 항목(ck_01~ck_06). 저장한 적이 없어도 미작성 상태로 조회된다.
  - record    AI와 함께 정리한 내용. AI 답변을 검수해서 저장한다.
  - document  산출물(제목 + 본문). 편집 중인 내용은 임시 저장(draft)으로 따로 둔다.

초안·확정 구분과 이력은 없다. 저장하면 바로 반영된다.
"""
from bson import Int64, ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import AppError
from app.services.store import (
    conflict,
    not_found,
    parse_object_id,
    save_revision,
    touch_project,
    upsert_revision,
    utcnow,
)

PLAN_LABELS = {
    "ck_01": "상품선정",
    "ck_02": "고객선정",
    "ck_03": "구매가치결정",
    "ck_04": "상품구성결정",
    "ck_05": "가격결정",
    "ck_06": "제작가능성",
}
PRICE_KEY = "ck_05"
RULES = {
    "plan": {"max_length": 5000},
    "record": {"max_length": 5000, "max_count": 200},
    "document": {"max_length": 50000, "max_count": 200},
}
# AI 답변(메시지)에 저장한 글의 key를 적어 두는 필드
MESSAGE_LINKS = {"record": "record_key", "document": "doc_key"}


def invalid(message: str) -> AppError:
    return AppError(422, "VALIDATION_ERROR", f"입력값을 확인해 주세요: {message}")


def check_plan_answer(key: str, content: str, amount: int | None) -> None:
    if amount is not None and key != PRICE_KEY:
        raise AppError(422, "AMOUNT_NOT_ALLOWED", "금액은 가격결정 항목에만 넣을 수 있습니다.")
    if len(content) > RULES["plan"]["max_length"]:
        raise invalid("content")


def view(kind: str, key: str, document: dict | None) -> dict:
    document = document or {}
    content = document.get("content") or None
    if kind != "plan" or content:
        state = "WRITTEN"
    else:
        state = "SKIPPED" if document.get("skipped") else "UNSET"
    return {
        "kind": kind,
        "key": key,
        "title": PLAN_LABELS[key] if kind == "plan" else document.get("title"),
        # 기획 항목만 미작성(UNSET)·건너뜀(SKIPPED)이 있다.
        "state": state,
        "content": content,
        "amount": document.get("amount"),
        "message_id": document.get("message_id"),
        "draft": document.get("draft") if kind == "document" else None,
        # 아직 한 번도 저장하지 않은 기획 항목은 0
        "revision": document.get("revision", 0),
        "updated_at": document.get("updated_at"),
    }


async def _find(db: AsyncDatabase, project_id: ObjectId, kind: str, key: str) -> dict | None:
    if kind == "plan" and key not in PLAN_LABELS:
        raise invalid("key")
    return await db.items.find_one({"project_id": project_id, "kind": kind, "key": key})


async def _plan_filled(db: AsyncDatabase, project_id: ObjectId) -> int:
    return await db.items.count_documents({"project_id": project_id, "kind": "plan", "content": {"$nin": ["", None]}})


async def start_plan(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, answers: dict) -> int:
    """새 프로젝트 마법사의 답변으로 6개 항목을 만든다. 답하지 않은 항목은 건너뜀으로 남는다. 작성한 개수를 돌려준다."""
    now = utcnow()
    documents = []
    for key in PLAN_LABELS:
        answer = answers.get(key)
        content = answer.content if answer and answer.content.strip() else ""
        amount = answer.amount if answer and content else None
        documents.append(
            {
                "user_id": user_id,
                "project_id": project_id,
                "kind": "plan",
                "key": key,
                "content": content,
                "amount": Int64(amount) if amount is not None else None,
                "skipped": not content,
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
        )
    await db.items.insert_many(documents)
    filled = sum(1 for document in documents if document["content"])
    await touch_project(db, project_id, plan_filled=filled)
    return filled


async def get_item(db: AsyncDatabase, project_id: ObjectId, kind: str, key: str) -> dict:
    document = await _find(db, project_id, kind, key)
    if document is None and kind != "plan":
        raise not_found()
    return view(kind, key, document)


async def list_items(db: AsyncDatabase, project_id: ObjectId, kind: str, page=None) -> list[dict]:
    cursor = db.items.find({"project_id": project_id, "kind": kind})
    if kind == "plan":
        documents = {doc["key"]: doc async for doc in cursor}
        return [view(kind, key, documents.get(key)) for key in PLAN_LABELS]
    cursor = cursor.sort("updated_at", -1)
    if page is not None:
        cursor = cursor.skip(page.skip).limit(page.limit)
    return [view(kind, doc["key"], doc) for doc in await cursor.to_list()]


def _check_text(kind: str, content: str) -> None:
    if not content.strip() or len(content) > RULES[kind]["max_length"]:
        raise invalid("content")


async def save_item(
    db: AsyncDatabase,
    user_id: ObjectId,
    project_id: ObjectId,
    kind: str,
    key: str,
    revision: int,
    content: str | None = None,
    title: str | None = None,
    amount: int | None = None,
    skipped: bool = False,
    as_draft: bool = False,
    log: str | None = None,
) -> dict:
    """글을 저장한다. content와 title은 보내지 않으면 그대로 둔다.

    기획 항목은 내용을 비워 저장하면 미작성으로 돌아가고(skipped면 건너뜀), 금액은 보내지 않으면 유지한다.
    as_draft는 산출물 편집기의 임시 저장이다. 저장된 문서는 그대로 두고 draft만 바꾼다.
    """
    existing = await _find(db, project_id, kind, key)
    if existing is None and kind != "plan":
        raise not_found()
    if content is None:
        if existing is None:
            raise invalid("content")
        content = existing.get("content") or ""

    if as_draft:
        if kind != "document":
            raise invalid("as_draft")
        draft = {"title": title or existing.get("title"), "content": content, "updated_at": utcnow()}
        return view(kind, key, await save_revision(db.items, existing, {"draft": draft}, revision))

    if kind == "plan":
        check_plan_answer(key, content, amount)
        if not content.strip():
            if amount is not None:
                raise invalid("amount")
            changes = {"content": "", "amount": None, "skipped": skipped}
        else:
            if amount is None:
                amount = (existing or {}).get("amount")
            changes = {"content": content, "amount": Int64(amount) if amount is not None else None, "skipped": False}
        log = log or f"{PLAN_LABELS[key]} 수정"
    else:
        if amount is not None:
            raise AppError(422, "AMOUNT_NOT_ALLOWED", "금액은 가격결정 항목에만 넣을 수 있습니다.")
        _check_text(kind, content)
        changes = {"content": content}
        if title is not None:
            changes["title"] = title
        if kind == "document":
            changes["draft"] = None
            log = log or f"산출물 수정 · {title or existing.get('title')}"
        else:
            log = log or "AI 정리 내용 수정"

    ident = {"user_id": user_id, "project_id": project_id, "kind": kind, "key": key}
    document = await upsert_revision(db.items, ident, existing, changes, revision)
    fields = {"plan_filled": await _plan_filled(db, project_id)} if kind == "plan" else {}
    await touch_project(db, project_id, log, **fields)
    return view(kind, key, document)


async def create_item(db: AsyncDatabase, user_id: ObjectId, project_id: ObjectId, kind: str, body) -> dict:
    """정리 내용이나 산출물을 새로 만든다. message_id를 보내면 그 AI 답변에서 저장한 글로 연결한다.

    같은 답변을 다시 검수하면 정리 내용을 새로 만들지 않고 고친다. 이미 산출물로 저장한 답변이면 그 문서를 돌려준다.
    """
    if kind == "plan":
        raise invalid("kind")
    title, content, message = body.title, body.content, None
    if body.message_id is not None:
        message = await db.messages.find_one(
            {"_id": parse_object_id(body.message_id), "project_id": project_id, "role": "ASSISTANT"}
        )
        if message is None:
            raise not_found()
        link = MESSAGE_LINKS[kind]
        linked = await _find(db, project_id, kind, message[link]) if message.get(link) else None
        if linked and kind == "document":
            return view(kind, linked["key"], linked)
        if linked:
            return await save_item(
                db, user_id, project_id, kind, linked["key"], linked["revision"], content, title,
                log="AI 답변 검수·확정 → 상품기획에 저장",
            )
        if kind == "document" and message.get("doc"):
            title = title or message["doc"]["title"]
            content = content or message["doc"]["content"]
    if not title:
        raise invalid("title")
    _check_text(kind, content or "")
    limit = RULES[kind]["max_count"]
    if await db.items.count_documents({"project_id": project_id, "kind": kind}) >= limit:
        raise AppError(422, "ITEM_LIMIT", f"{limit}개까지 저장할 수 있습니다.")

    # 답변에서 저장한 글은 key를 답변에 묶어, 같은 답변을 동시에 두 번 저장해도 하나만 생기게 한다(늦은 쪽은 409).
    key = f"m{message['_id']}" if message else str(ObjectId())
    ident = {"user_id": user_id, "project_id": project_id, "kind": kind, "key": key}
    changes = {"title": title, "content": content, "message_id": message["_id"] if message else None}
    if kind == "document":
        changes["draft"] = None
    document = await upsert_revision(db.items, ident, None, changes, 0)
    if message:
        await db.messages.update_one({"_id": message["_id"]}, {"$set": {MESSAGE_LINKS[kind]: key}})
    if kind == "document":
        log = f"산출물 저장 · {title}"
    else:
        log = "AI 답변 검수·확정 → 상품기획에 저장" if message else "정리 내용 추가"
    await touch_project(db, project_id, log)
    return view(kind, key, document)


async def delete_item(db: AsyncDatabase, project_id: ObjectId, kind: str, key: str, revision: int) -> None:
    """정리 내용이나 산출물을 지운다. 원래 AI 대화는 남는다. 기획 항목은 비워서 저장하면 된다."""
    if kind == "plan":
        raise invalid("kind")
    existing = await _find(db, project_id, kind, key)
    if existing is None:
        raise not_found()
    deleted = await db.items.delete_one({"_id": existing["_id"], "revision": revision})
    if not deleted.deleted_count:
        raise conflict()
    link = MESSAGE_LINKS[kind]
    await db.messages.update_many({"project_id": project_id, link: key}, {"$set": {link: None}})
    await touch_project(db, project_id, "산출물 삭제" if kind == "document" else "AI 정리 내용 삭제")


async def discard_draft(db: AsyncDatabase, project_id: ObjectId, key: str, revision: int) -> dict:
    """산출물의 임시 저장을 버리고 저장된 문서로 돌아간다."""
    existing = await _find(db, project_id, "document", key)
    if existing is None:
        raise not_found()
    if not existing.get("draft"):
        raise AppError(422, "NO_DRAFT", "버릴 임시 저장이 없습니다.")
    return view("document", key, await save_revision(db.items, existing, {"draft": None}, revision))
