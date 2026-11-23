"""계정 홈(기획서 v2 C절 "작성 상태와 대시보드 기준").

내 진행 중 프로젝트 전체(또는 고른 브랜드)의 최근 문서·오늘 할 일·기한 지난 일·진행 중 문서와
근거가 있는 추천을 모은다. AI를 부르지 않고, 홈을 연다고 아무것도 바뀌지 않는다.
문서 진행도(검토 완료)와 업무 완료는 서로 다른 정보로 따로 센다.
"""
import datetime as dt

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.services import structured_docs
from app.services.store import get_owned_brand

KST = dt.timezone(dt.timedelta(hours=9))
RECENT_DOCS = 5
MAX_RECOMMENDATIONS = 8
STALLED_DAYS = 7
NO_BRAND = "none"


def korean_today() -> dt.date:
    return dt.datetime.now(KST).date()


async def active_projects(db: AsyncDatabase, user_id: ObjectId, brand_id: str | None) -> dict[ObjectId, dict]:
    query: dict = {"user_id": user_id, "deleted_at": None, "archived_at": None}
    if brand_id == NO_BRAND:
        query["brand_id"] = None
    elif brand_id:
        query["brand_id"] = (await get_owned_brand(db, user_id, brand_id))["_id"]
    return {project["_id"]: project async for project in db.projects.find(query, {"name": 1, "brand_id": 1})}


def _doc_summary(view: dict, project: dict) -> dict:
    return {
        "doc_id": view["_id"], "project_id": view["project_id"], "project_name": project.get("name"),
        "title": view["title"], "template_id": view["template_id"], "last_section_id": view.get("last_section_id"),
        "last_section_title": view.get("last_section_title"), "progress": view["progress"],
        "updated_at": view["updated_at"],
    }


def _task_summary(task: dict, project: dict) -> dict:
    return {
        "task_id": task["_id"], "project_id": task["project_id"], "project_name": project.get("name"),
        "title": task["title"], "date": task["date"], "source_doc_id": task.get("source_doc_id"),
        "source_section_id": task.get("source_section_id"),
    }


def _first_open_required(view: dict) -> dict | None:
    return next((s for s in view["sections"] if s["required"] and s["state"] != structured_docs.REVIEWED), None)


def _recommendations(overdue: list[dict], docs: list[tuple[dict, dict]], today: dt.date) -> list[dict]:
    """근거가 있는 추천만: 기한 지난 일, 진행 중 문서의 확인 안 된 필수 항목, 오래 멈춘 문서."""
    items = [
        {"kind": "overdue_task", "title": f"기한 지난 일: {t['title']}", "project_id": t["project_id"],
         "task_id": t["task_id"], "doc_id": t["source_doc_id"], "section_id": t["source_section_id"]}
        for t in overdue
    ]
    stalled_before = today - dt.timedelta(days=STALLED_DAYS)
    for view, project in docs:
        open_section = _first_open_required(view)
        if open_section is None:
            continue
        items.append({
            "kind": "required_section", "title": f"{view['title']} · {open_section['title']} 작성·확인",
            "project_id": view["project_id"], "doc_id": view["_id"], "section_id": open_section["section_id"],
        })
        if view["updated_at"].astimezone(KST).date() < stalled_before:
            items.append({
                "kind": "stalled_doc", "title": f"{STALLED_DAYS}일 넘게 멈춘 문서: {view['title']}",
                "project_id": view["project_id"], "doc_id": view["_id"], "section_id": view.get("last_section_id"),
            })
    return items[:MAX_RECOMMENDATIONS]


async def get_home(db: AsyncDatabase, user_id: ObjectId, brand_id: str | None, today: dt.date) -> dict:
    projects = await active_projects(db, user_id, brand_id)
    ids = list(projects)
    cursor = db.structured_docs.find({"project_id": {"$in": ids}, "deleted_at": None}).sort("updated_at", -1)
    docs = [(await structured_docs.doc_view(db, doc), projects[doc["project_id"]]) async for doc in cursor]
    in_progress = [(view, project) for view, project in docs
                   if view["progress"]["required_reviewed"] < view["progress"]["required_total"]]
    task_query = {"project_id": {"$in": ids}, "deleted_at": None, "done": False, "date": {"$lte": today.isoformat()}}
    open_tasks = [_task_summary(task, projects[task["project_id"]])
                  async for task in db.tasks.find(task_query).sort([("date", 1), ("created_at", 1)])]
    overdue = [task for task in open_tasks if task["date"] < today.isoformat()]
    return {
        "today": today.isoformat(),
        "brand_id": brand_id,
        "recent_docs": [_doc_summary(view, project) for view, project in docs[:RECENT_DOCS]],
        "today_tasks": [task for task in open_tasks if task["date"] == today.isoformat()],
        "overdue_tasks": overdue,
        "docs_in_progress": [_doc_summary(view, project) for view, project in in_progress],
        "recommendations": _recommendations(overdue, in_progress, today),
    }
