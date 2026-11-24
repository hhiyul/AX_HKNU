"""AI에게 넘길 자료: 이 프로젝트와 참고 프로젝트의 섹션, 이전 대화, 섹션 읽기 도구.

자료는 실행을 시작할 때 한 번 모은다. 읽기 도구는 이미 모은 자료에서만 고르고 DB를 다시 읽지 않는다.
참고 파일(프로젝트의 file_refs와 이번 메시지에 붙인 파일) 중 TXT·MD·DOCX는 글로 읽어 넘긴다. 이미지는 읽지 않는다.
"""
import asyncio
import io
import zipfile
from xml.etree import ElementTree

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.ai.context import history_summary_text
from app.schemas.projects import MAX_CHIP_REFS
from app.services import ai_runs, ai_workspace, overview
from app.services.assets import DOCX_TYPE, decode_text
from app.storage import file_path

MAX_FILES = 10
# AI와 함께 정리한 내용은 최근 것 20개까지 넘긴다.
MAX_RECORDS = 20
MAX_FILE_CHARS = 50_000
MAX_DOCX_XML_BYTES = 10 * 1024 * 1024
CUT_MARK = "\n(이하 생략: 파일이 길어 앞부분만 넣었습니다)"
TEXT_TYPES = {"text/plain", "text/markdown"}
FILE_ALIASES = {"attachments", "attachment", "files", "file", "reference_files"}
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

PARTIAL_MARK = "(중간에 끊긴 답변) "
READ_TOOL = {
    "type": "function",
    "name": "read_project_sections",
    "description": (
        "Read sections of this project or its reference projects already supplied in context. "
        "Keys: project_basic, linked_brand, ck_01..ck_06, today, doc:<id> (saved document sections), tasks, "
        "economics, ledger, project_memo, or ref:<project_id>:<section> for a reference project."
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


async def _record_sections(db: AsyncDatabase, project_id: ObjectId) -> dict[str, dict]:
    cursor = db.items.find({"project_id": project_id, "kind": "record"}).sort("updated_at", -1).limit(MAX_RECORDS)
    return {
        f"record:{record['key']}": {
            "source": "this_project", "kind": "record", "title": record.get("title"),
            "content": record.get("content"), "revision": record.get("revision"),
        }
        async for record in cursor
    }


async def build_sections(db: AsyncDatabase, project: dict, user_id: ObjectId) -> dict[str, dict]:
    sections = {key: {**value, "source": "this_project"} for key, value in (await _sections_of(db, project)).items()}
    sections.update(await _record_sections(db, project["_id"]))
    # 오늘 날짜·문서·일정·원가·정산·메모(이 프로젝트만. 참고 프로젝트는 기획 항목만 넘긴다)
    sections.update({key: {**value, "source": "this_project"} for key, value in (await ai_workspace.workspace_sections(db, project)).items()})
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
    """키를 요청 순서대로 고른다. attachments·files 같은 이름은 참고 파일 전부, 파일 이름도 키로 쓸 수 있다."""
    wanted: list[str] = []
    for key in keys:
        if key.casefold() in FILE_ALIASES:
            wanted += [name for name in sections if name.startswith("file:")]
        elif key in sections:
            wanted.append(key)
        else:
            wanted += [name for name, value in sections.items() if name.startswith("file:") and value.get("name") == key]
    return [{"key": key, **sections[key]} for key in dict.fromkeys(wanted)]


def _docx_text(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                return None
            root = ElementTree.fromstring(archive.read(info))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return None
    paragraphs = ["".join(node.text or "" for node in p.iter(f"{WORD}t")) for p in root.iter(f"{WORD}p")]
    return "\n".join(paragraphs)


def _read_file(asset: dict) -> str | None:
    try:
        data = file_path(asset["_id"]).read_bytes()
    except OSError:
        return None
    if asset.get("mime_type") == DOCX_TYPE:
        return _docx_text(data)
    if asset.get("mime_type") in TEXT_TYPES:
        return decode_text(data)
    return None


async def file_sections(
    db: AsyncDatabase, project: dict, user_id: ObjectId, extra_ids: list[ObjectId] | None = None
) -> dict[str, dict]:
    """이번 메시지에 붙인 파일을 먼저, 그다음 프로젝트의 참고 파일. 내 것이고 글로 읽을 수 있는 것만 10개까지."""
    ids = list(dict.fromkeys([*(extra_ids or []), *(project.get("file_refs") or [])]))
    if not ids:
        return {}
    query = {"_id": {"$in": ids}, "user_id": user_id, "mime_type": {"$in": [*TEXT_TYPES, DOCX_TYPE]}}
    found = {asset["_id"]: asset async for asset in db.assets.find(query)}
    sections: dict[str, dict] = {}
    for ident in [ident for ident in ids if ident in found][:MAX_FILES]:
        asset = found[ident]
        text = await asyncio.to_thread(_read_file, asset)
        if text is None:
            continue
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + CUT_MARK
        sections[f"file:{ident}"] = {
            "source": "attachment", "name": asset.get("file_name"), "mime_type": asset.get("mime_type"), "content": text,
        }
    return sections


async def active_file_ids(db: AsyncDatabase, project: dict, user_id: ObjectId, current: list[ObjectId]) -> set[str]:
    """아직 쓸 수 있는 파일: 프로젝트의 참고 파일, 이번에 읽은 파일, 대화에서 붙였고 아직 지우지 않은 파일.

    여기에 없는 파일을 읽고 만든 답변·요약은 이후 대화에 넣지 않는다(참고에서 뺐거나 지운 파일).
    """
    attached: set[ObjectId] = set()
    cursor = db.messages.find({"project_id": project["_id"], "asset_ids.0": {"$exists": True}}, {"asset_ids": 1})
    async for message in cursor:
        attached.update(message["asset_ids"])
    existing = {asset["_id"] async for asset in db.assets.find({"_id": {"$in": list(attached)}, "user_id": user_id}, {"_id": 1})}
    return {str(ident) for ident in [*(project.get("file_refs") or []), *current, *existing]}


def _history_item(message: dict) -> dict:
    role = "user" if message["role"] == "USER" else "assistant"
    content = message["content"]
    if role == "assistant" and message.get("completion", "COMPLETE") != "COMPLETE":
        content = PARTIAL_MARK + content
    sources = [str(ident) for ident in message.get("source_ids") or []]
    return {"id": str(message["_id"]), "role": role, "content": content, "source_ids": sources}


async def load_history(
    db: AsyncDatabase, project_id: ObjectId, exclude_message_id: ObjectId, active_file_ids: set[str] = frozenset(),
    conversation_id: ObjectId | None = None,
) -> tuple[list[dict], int]:
    """이전 대화와 지금 압축 요약의 번호.

    참고에서 뺀 파일을 읽고 만든 답변은 넣지 않는다. 요약은 덮는 메시지가 모두 남아 있고, 요약이 읽은 파일이 모두
    아직 참고 중일 때만 그 메시지들 대신 쓴다.
    """
    query: dict = {"project_id": project_id, "_id": {"$ne": exclude_message_id}}
    # 대화를 주면 그 대화의 메시지만 넣는다. 다른 대화의 내용은 AI에게 넘기지 않는다.
    if conversation_id is not None:
        query["conversation_id"] = conversation_id
    cursor = db.messages.find(query).sort("ordinal", 1)
    history = [
        item async for item in (_history_item(message) async for message in cursor)
        if set(item["source_ids"]) <= set(active_file_ids)
    ]
    capsule = await ai_runs.load_capsule(db, project_id, conversation_id)
    if not capsule:
        return history, 0
    covered = set(capsule["covered_ids"])
    capsule_sources = list(capsule.get("source_ids") or [])
    if covered and covered <= {item["id"] for item in history} and set(capsule_sources) <= set(active_file_ids):
        summary = {
            "id": "capsule",
            "role": "user",
            "content": history_summary_text(capsule["text"]),
            "covered_ids": list(capsule["covered_ids"]),
            "source_ids": capsule_sources,
        }
        history = [summary] + [item for item in history if item["id"] not in covered]
    return history, capsule["revision"]
