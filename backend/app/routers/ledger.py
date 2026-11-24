"""정산 수입·지출 내역 API. 원가·판매가격 비교는 /projects/{id}/economics에 따로 있다."""
from fastapi import APIRouter, Depends, Query

from app.deps import Workspace
from app.schemas import ledger as schema
from app.schemas.common import to_api
from app.services import ledger

router = APIRouter(prefix="/projects/{project_id}/ledger", tags=["ledger"])


@router.get("", response_model=schema.LedgerOut)
async def list_entries(status: str = Query("active", pattern="^(active|trash)$"), ws: Workspace = Depends()):
    """내역은 날짜가 늦은 것부터. income·expense는 휴지통에 없는 내역의 합계다."""
    return to_api(await ledger.list_entries(ws.db, ws.project_id, status))


@router.post("", response_model=schema.EntryOut, status_code=201)
async def create_entry(body: schema.EntryCreate, ws: Workspace = Depends()):
    return to_api(await ledger.create_entry(ws.db, ws.user_id, ws.project_id, body))


@router.patch("/{entry_id}", response_model=schema.EntryOut)
async def update_entry(entry_id: str, body: schema.EntryUpdate, ws: Workspace = Depends()):
    # null은 "바꾸지 않음"으로 본다.
    changes = {key: value for key, value in body.model_dump(exclude_unset=True, exclude={"revision"}).items() if value is not None}
    return to_api(await ledger.update_entry(ws.db, ws.project_id, entry_id, changes, body.revision))


@router.delete("/{entry_id}", status_code=204)
async def trash_entry(entry_id: str, ws: Workspace = Depends()):
    await ledger.set_trashed(ws.db, ws.project_id, entry_id, True)


@router.post("/{entry_id}/restore", response_model=schema.EntryOut)
async def restore_entry(entry_id: str, ws: Workspace = Depends()):
    return to_api(await ledger.set_trashed(ws.db, ws.project_id, entry_id, False))
