"""항목형 전용 문서 API: 작성 틀, 문서, 항목의 임시 입력·저장·검토 완료, 미리보기·내보내기."""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.deps import Once, Workspace, current_user
from app.schemas import docs as schema
from app.schemas.common import to_api
from app.ai.runtime import get_runtime
from app.schemas.workspace import MessageOut
from app.services import doc_assist, exports, structured_docs, task_proposals, templates
from app.services import item_context
from app.services import document_inspection

template_router = APIRouter(prefix="/templates", tags=["docs"])
router = APIRouter(prefix="/projects/{project_id}/docs", tags=["docs"])
SECTION = "/{doc_id}/sections/{section_id}"


@router.get("/{doc_id}/inspection")
async def inspect_document(doc_id: str, ws: Workspace = Depends()):
    return await document_inspection.inspect(ws.db, ws.project, ws.user_id, doc_id)


@router.get(SECTION + "/assist-context")
async def get_assist_context(doc_id: str, section_id: str, ws: Workspace = Depends()):
    return await item_context.get(ws.db, ws.project_id, doc_id, section_id)


@router.put(SECTION + "/assist-context")
async def save_assist_context(doc_id: str, section_id: str, body: schema.AssistContextSave, ws: Workspace = Depends()):
    return await item_context.save(ws.db, ws.project_id, doc_id, section_id, body)


@template_router.get("")
async def list_templates(_: dict = Depends(current_user)):
    """펀딩 상세페이지·사업계획서 작성 틀(최신 버전)."""
    return [templates.to_dict(template) for template in templates.LATEST.values()]


@router.get("", response_model=list[schema.DocSummaryOut])
async def list_docs(status: str = Query("active", pattern="^(active|trash)$"), ws: Workspace = Depends()):
    return to_api(await structured_docs.list_docs(ws.db, ws.project_id, status))


@router.post("", response_model=schema.DocOut, status_code=201)
async def create_doc(body: schema.DocCreate, once: Once = Depends(), ws: Workspace = Depends()):
    """새 문서는 한 번만 만든다. 화면은 Idempotency-Key를 붙여 중복 클릭을 하나로 처리한다."""
    return to_api(await once.run(
        lambda: structured_docs.create_doc(ws.db, ws.user_id, ws.project_id, body.template_id, body.title, body.origin_message_id, body.product_ids)
    ))


@router.get("/{doc_id}", response_model=schema.DocOut)
async def get_doc(doc_id: str, ws: Workspace = Depends()):
    return to_api(await structured_docs.doc_view(ws.db, await structured_docs.get_doc(ws.db, ws.project_id, doc_id)))


@router.patch("/{doc_id}", response_model=schema.DocOut)
async def rename_doc(doc_id: str, body: schema.DocRename, ws: Workspace = Depends()):
    return to_api(await structured_docs.rename_doc(ws.db, ws.project_id, doc_id, body.title, body.revision))


@router.delete("/{doc_id}", status_code=204)
async def trash_doc(doc_id: str, ws: Workspace = Depends()):
    """휴지통으로 옮긴다. 연결된 일정은 그대로 둔다."""
    await structured_docs.set_trashed(ws.db, ws.project_id, doc_id, True)


@router.post("/{doc_id}/restore", response_model=schema.DocOut)
async def restore_doc(doc_id: str, ws: Workspace = Depends()):
    return to_api(await structured_docs.set_trashed(ws.db, ws.project_id, doc_id, False))


@router.put(SECTION + "/draft", response_model=schema.SectionOut)
async def save_draft(doc_id: str, section_id: str, body: schema.DraftSave, ws: Workspace = Depends()):
    """임시 입력. 저장 본문과 검토 상태는 그대로다."""
    return to_api(await structured_docs.save_draft(
        ws.db, ws.project_id, doc_id, section_id, body.text, body.draft_revision
    ))


@router.delete(SECTION + "/draft", response_model=schema.SectionOut)
async def discard_draft(doc_id: str, section_id: str, draft_revision: int = Query(ge=0), ws: Workspace = Depends()):
    return to_api(await structured_docs.discard_draft(ws.db, ws.project_id, doc_id, section_id, draft_revision))


@router.put(SECTION, response_model=schema.SectionOut)
async def save_section(doc_id: str, section_id: str, body: schema.SectionSave, ws: Workspace = Depends()):
    """본문 저장. 저장하면 다시 확인해야 검토 완료가 된다."""
    return to_api(await structured_docs.save_section(
        ws.db, ws.user_id, ws.project_id, doc_id, section_id, body.text, body.material_ids, body.revision
    ))


@router.post(SECTION + "/review", response_model=schema.SectionOut)
async def review_section(doc_id: str, section_id: str, body: schema.SectionReview, ws: Workspace = Depends()):
    """지금 저장된 본문(revision)을 사용자가 확인했다고 기록한다."""
    return to_api(await structured_docs.review_section(ws.db, ws.project_id, doc_id, section_id, body.revision))


@router.get("/{doc_id}/preview", response_model=schema.PreviewOut)
async def preview(doc_id: str, ws: Workspace = Depends()):
    return to_api(await structured_docs.compose(ws.db, ws.project_id, doc_id))


@router.get("/{doc_id}/export/{fmt}")
async def export_doc(doc_id: str, fmt: str, ws: Workspace = Depends()):
    """저장된 본문을 합친 글을 md·txt·docx·pdf로 내려받는다. 임시 입력은 넣지 않는다."""
    composed = await structured_docs.compose(ws.db, ws.project_id, doc_id)
    return Response(**await exports.markdown_export(composed["text"], fmt, composed["title"]))


@router.post(SECTION + "/assist", response_model=schema.RunStarted, status_code=202)
async def assist(doc_id: str, section_id: str, body: schema.AssistRequest, ws: Workspace = Depends()):
    """항목 하나의 AI 후보 만들기. 고른 참고만 보낸다. 진행 상황은 /runs/{run_id}/stream, 후보는 답변 메시지의 candidate."""
    run_id = await doc_assist.start_assist(ws.db, get_runtime(), ws.user_id, ws.project, doc_id, section_id, body)
    return {"run_id": str(run_id)}


@router.post(SECTION + "/apply", response_model=schema.SectionOut)
async def apply_candidate(doc_id: str, section_id: str, body: schema.ApplyCandidate, ws: Workspace = Depends()):
    """후보를 임시 입력에 넣는다. 후보 뒤에 글이 바뀌었으면 409 CANDIDATE_OUTDATED(confirm이면 적용)."""
    return to_api(await doc_assist.apply_candidate(
        ws.db, ws.project_id, doc_id, section_id, body.message_id, body.draft_revision, body.confirm
    ))


@router.get(SECTION + "/messages", response_model=list[MessageOut])
async def section_messages(doc_id: str, section_id: str, ws: Workspace = Depends()):
    """이 항목의 AI 요청·답변 기록. 메시지의 target_doc_id·target_section_id로 항목에 돌아온다."""
    return to_api(await doc_assist.section_messages(ws.db, ws.project_id, doc_id, section_id))


@router.post(SECTION + "/task-proposals", response_model=schema.RunStarted, status_code=202)
async def propose_tasks(doc_id: str, section_id: str, body: schema.ProposalRequest, ws: Workspace = Depends()):
    """이 항목에서 할 일 후보를 받는다. 후보는 답변 메시지의 proposals이고 아직 일정이 아니다."""
    run_id = await task_proposals.start_proposals(ws.db, get_runtime(), ws.user_id, ws.project, doc_id, section_id, body)
    return {"run_id": str(run_id)}
