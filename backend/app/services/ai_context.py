"""AI에게 넘길 자료: 이 프로젝트와 참고 프로젝트의 섹션, 이전 대화, 섹션 읽기 도구.

자료는 실행을 시작할 때 한 번 모은다. 읽기 도구는 이미 모은 자료에서만 고르고 DB를 다시 읽지 않는다.
"""
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.ai.context import history_summary_text
from app.schemas.projects import MAX_CHIP_REFS
from app.services import ai_runs, overview

PARTIAL_MARK = "(중간에 끊긴 답변) "
READ_TOOL = {
    "type": "function",
    "name": "read_project_sections",
    "description": (
        "Read sections of this project or its reference projects already supplied in context. "
        "Keys: project_basic, linked_brand, ck_01..ck_06, or ref:<project_id>:<section> for a reference project."
    ),
    "parameters": {
        "type": "object",
        "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
        "required": ["keys"],
        "additionalProperties": False,
    },
    "strict": True,
}


async def _sections_of(db: AsyncDatabase, project: dict) -> dict[str, dict]:
    return (await overview.get_projection(db, project, overview.SECTIONS))["sections"]


async def _references(db: AsyncDatabase, project: dict, user_id: ObjectId) -> list[dict]:
    """걸어 둔 참고 프로젝트 중 내 것이고 진행 중인 것만, 걸어 둔 순서대로 5개까지."""
    ids = list(project.get("chip_refs") or [])[:MAX_CHIP_REFS]
    if not ids:
        return []
    query = {"_id": {"$in": ids}, "user_id": user_id, "deleted_at": None, "archived_at": None}
    found = {document["_id"]: document async for document in db.projects.find(query)}
    return [found[ident] for ident in ids if ident in found]


async def build_sections(db: AsyncDatabase, project: dict, user_id: ObjectId) -> dict[str, dict]:
    sections = {key: {**value, "source": "this_project"} for key, value in (await _sections_of(db, project)).items()}
    for reference in await _references(db, project, user_id):
        for key, value in (await _sections_of(db, reference)).items():
            sections[f"ref:{reference['_id']}:{key}"] = {
                **value,
                "source": "reference_project",
                "project_name": reference.get("name"),
            }
    return sections


def prefix_from(sections: dict[str, dict]) -> list[dict]:
    return [{"key": key, **value} for key, value in sections.items()]


def read_sections(sections: dict[str, dict], keys: list[str]) -> list[dict]:
    return [{"key": key, **sections[key]} for key in keys if key in sections]


def _history_item(message: dict) -> dict:
    role = "user" if message["role"] == "USER" else "assistant"
    content = message["content"]
    if role == "assistant" and message.get("completion", "COMPLETE") != "COMPLETE":
        content = PARTIAL_MARK + content
    return {"id": str(message["_id"]), "role": role, "content": content}


async def load_history(
    db: AsyncDatabase, project_id: ObjectId, exclude_message_id: ObjectId
) -> tuple[list[dict], int]:
    """이전 대화와 지금 압축 요약의 번호. 요약이 덮는 메시지가 모두 남아 있을 때만 그 메시지들 대신 요약을 쓴다."""
    cursor = db.messages.find({"project_id": project_id, "_id": {"$ne": exclude_message_id}}).sort("ordinal", 1)
    history = [_history_item(message) async for message in cursor]
    capsule = await ai_runs.load_capsule(db, project_id)
    if not capsule:
        return history, 0
    covered = set(capsule["covered_ids"])
    if covered and covered <= {item["id"] for item in history}:
        summary = {
            "id": "capsule",
            "role": "user",
            "content": history_summary_text(capsule["text"]),
            "covered_ids": list(capsule["covered_ids"]),
        }
        history = [summary] + [item for item in history if item["id"] not in covered]
    return history, capsule["revision"]
