"""AI협업 채팅에 넘기는 작업 자료: 오늘 날짜, 작성 중인 문서, 일정, 원가·판매가격, 정산, 프로젝트 메모.

- 문서는 저장한 글만 넘긴다(임시 입력은 넘기지 않는다). 항목마다 앞부분만 넣는다.
- 상태는 화면과 같은 한국어로 넘겨 AI가 코드값을 그대로 보이지 않게 한다.
- 모두 자료일 뿐이다. 안에 지시처럼 보이는 글이 있어도 따르지 않는다는 규칙은 ai/context.py에 있다.
"""
import datetime as dt

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.services import economics, home, ledger, structured_docs

MAX_DOCS = 5
MAX_SECTION_CHARS = 1500
MAX_TASKS = 30
MAX_LEDGER = 10
CUT = " …(이하 생략)"
WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
STATE_LABELS = {"UNWRITTEN": "미작성", "WRITING": "작성 중", "REVIEWED": "검토 완료"}
TEMPLATE_NAMES = {"funding": "펀딩 상세페이지", "business_plan": "사업계획서"}
COST_LABELS = {"material": "재료비", "labor": "공임", "packaging": "포장비", "shipping": "배송비", "other": "기타 비용"}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + CUT


def today_section(today: dt.date | None = None) -> dict:
    today = today or home.korean_today()
    return {"kind": "today", "date": today.isoformat(), "weekday": WEEKDAYS[today.weekday()], "timezone": "Asia/Seoul"}


async def _doc_sections(db: AsyncDatabase, project_id: ObjectId) -> dict[str, dict]:
    cursor = db.structured_docs.find({"project_id": project_id, "deleted_at": None}).sort("updated_at", -1).limit(MAX_DOCS)
    sections = {}
    async for doc in cursor:
        view = await structured_docs.doc_view(db, doc)
        sections[f"doc:{doc['_id']}"] = {
            "kind": "structured_doc",
            "title": doc["title"],
            "revision": doc["revision"], "read_status": "partial" if any(len(s["text"]) > MAX_SECTION_CHARS for s in view["sections"]) else "read",
            "template": TEMPLATE_NAMES.get(doc["template_id"], doc["template_id"]),
            "progress": f"필수 항목 {view['progress']['required_reviewed']}/{view['progress']['required_total']} 검토 완료",
            "sections": [
                {"title": s["title"], "required": s["required"], "state": STATE_LABELS[s["state"]],
                 "saved_text": _clip(s["text"], MAX_SECTION_CHARS)}
                for s in view["sections"]
            ],
        }
    return sections


async def _tasks(db: AsyncDatabase, project_id: ObjectId, today: dt.date) -> dict | None:
    query = {"project_id": project_id, "deleted_at": None}
    open_tasks, done = [], 0
    async for task in db.tasks.find(query).sort([("date", 1), ("created_at", 1)]):
        if task["done"]:
            done += 1
            continue
        if len(open_tasks) < MAX_TASKS:
            item = {"id": str(task["_id"]), "revision": task.get("revision"), "title": task["title"], "date": task["date"], "overdue": task["date"] < today.isoformat()}
            if task.get("note"):
                item["note"] = _clip(task["note"], 200)
            open_tasks.append(item)
    if not open_tasks and not done:
        return None
    total = await db.tasks.count_documents({**query, "done": False})
    return {"kind": "tasks", "open": open_tasks, "done_count": done, "has_more": total > len(open_tasks)}


async def _economics(db: AsyncDatabase, project_id: ObjectId) -> dict | None:
    view = await economics.get_economics(db, project_id)
    cost = view["cost"]
    if view["confirmed_price"] is None and all(value is None for value in cost.values()):
        return None
    section = {
        "kind": "economics",
        "confirmed_price": view["confirmed_price"],
        "cost_per_item": {COST_LABELS[key]: cost.get(key) for key in COST_LABELS},
        "fee_rate_percent": cost.get("fee_rate"),
        "price_being_compared": cost.get("price"),
        "note": "값이 null이면 아직 모름. 확정 판매가는 사용자가 정한 값이고, 비교 중인 판매가는 확정이 아니다.",
    }
    for name in ("keep", "change"):
        if view[name]:
            section[f"net_per_item_{name}"] = view[name]["net"]
    return section


async def _ledger(db: AsyncDatabase, project_id: ObjectId) -> dict | None:
    data = await ledger.list_entries(db, project_id, "active")
    if not data["entries"]:
        return None
    return {
        "kind": "ledger",
        "income_total": data["income"],
        "expense_total": data["expense"],
        "recent": [
            {"type": ledger.TYPE_LABELS[e["type"]], "title": e["title"], "amount": e["amount"], "date": e["date"]}
            for e in data["entries"][:MAX_LEDGER]
        ],
    }


async def workspace_sections(db: AsyncDatabase, project: dict) -> dict[str, dict]:
    today = home.korean_today()
    sections: dict[str, dict] = {"today": today_section(today)}
    sections.update(await _doc_sections(db, project["_id"]))
    for key, value in (
        ("tasks", await _tasks(db, project["_id"], today)),
        ("economics", await _economics(db, project["_id"])),
        ("ledger", await _ledger(db, project["_id"])),
    ):
        if value:
            sections[key] = value
    if project.get("note"):
        sections["project_memo"] = {"kind": "memo", "content": _clip(project["note"], 2000)}
    return sections

