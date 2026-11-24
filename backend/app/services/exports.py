"""산출물과 저장된 상품 기획을 파일(md, txt, docx, pdf)로 내보낸다. 저장된 내용만 쓰고 임시 저장(draft)은 넣지 않는다."""
import asyncio
from urllib.parse import quote

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.ai.export import ExportError, export
from app.errors import AppError
from app.services import items

STATE_LABELS = {"WRITTEN": "작성됨", "SKIPPED": "미작성"}
EMPTY = "(내용 없음)"


async def _file(body: str, fmt: str, name: str) -> dict:
    """fastapi Response(**값)에 넘길 content, media_type, headers. 변환은 외부 프로그램이라 스레드에서 돌린다."""
    try:
        data, media_type = await asyncio.to_thread(export, body, fmt)
    except ValueError:
        raise AppError(422, "UNSUPPORTED_FORMAT", "md, txt, docx, pdf로만 내보낼 수 있습니다.") from None
    except ExportError as exc:
        # 변환 도구가 없거나 실패해도 원본은 그대로다.
        raise AppError(503, str(exc), "파일 변환을 하지 못했습니다. md나 txt로 내려받아 주세요.") from None
    filename = quote(f"{name}.{fmt}", safe="")
    disposition = f"attachment; filename=\"export.{fmt}\"; filename*=UTF-8''{filename}"
    return {"content": data, "media_type": media_type, "headers": {"Content-Disposition": disposition}}


async def markdown_export(body: str, fmt: str, name: str) -> dict:
    """Markdown 글을 형식에 맞는 파일 응답으로 바꾼다(항목형 문서의 합친 글 등)."""
    return await _file(body, fmt, name)


async def document_export(db: AsyncDatabase, project_id: ObjectId, key: str, fmt: str) -> dict:
    document = await items.get_item(db, project_id, "document", key)
    return await _file(document["content"] or "", fmt, document["title"] or "산출물")


def _plan_section(item: dict) -> str:
    body = item["content"] or EMPTY
    if item["amount"] is not None:
        body += f"\n\n금액: {item['amount']:,}원"
    number = int(item["key"].removeprefix("ck_"))
    return f"## {number}. {item['title']} · {STATE_LABELS[item['state']]}\n\n{body}"


async def plan_export(db: AsyncDatabase, project: dict, fmt: str) -> dict:
    """6개 기획 항목(미작성 포함)과 AI와 함께 정리한 내용을 한 파일로 모은다."""
    name = project.get("name") or "프로젝트"
    chunks = [f"# 저장된 상품 기획 · {name}"]
    chunks += [_plan_section(item) for item in await items.list_items(db, project["_id"], "plan")]
    for record in await items.list_items(db, project["_id"], "record"):
        chunks.append(f"## AI와 함께 정리한 내용: {record['title']}\n\n{record['content'] or EMPTY}")
    return await _file("\n\n".join(chunks), fmt, f"{name} 상품 기획")
