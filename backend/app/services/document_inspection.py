"""저장된 버전만 검사한다. 모델 호출·본문 변경·검토 완료 처리는 하지 않는다."""
from app.services import structured_docs, catalog
from app.services.store import utcnow


async def inspect(db, project, user_id, doc_id):
    doc = await structured_docs.get_doc(db, project["_id"], doc_id, active=True)
    view = await structured_docs.doc_view(db, doc)
    issues = []
    for section in view["sections"]:
        base = {"section_id": section["section_id"], "revision": section["revision"]}
        if section["required"] and not section["text"].strip():
            issues.append({**base, "code": "REQUIRED_EMPTY", "message": "필수 항목이 비어 있습니다."})
        if "확인 필요" in section["text"]:
            issues.append({**base, "code": "UNCONFIRMED", "message": "확인 필요 표시가 남아 있습니다."})
        if section["has_draft"]:
            issues.append({**base, "code": "UNSAVED_DRAFT", "message": "임시 입력은 검사와 다운로드에 포함되지 않습니다."})
        if section["text"].strip() and section["state"] != "REVIEWED":
            issues.append({**base, "code": "UNREVIEWED", "message": "현재 저장 버전을 아직 검토하지 않았습니다."})
    for saved in doc.get("product_snapshots", []):
        current = await db.products.find_one({**catalog.query(project, user_id, "products"), "_id": saved["_id"]})
        if not current:
            issues.append({"code": "SOURCE_UNAVAILABLE", "source_key": f"product:{saved['_id']}", "message": "선택 제품 원본을 현재 확인할 수 없습니다. 문서 내용은 유지합니다."})
        elif current["revision"] != saved["revision"]:
            fields = [k for k in ("name", "intent", "material", "process", "cost", "sale_price", "price_estimated", "options") if current.get(k) != saved.get(k)]
            if fields:
                issues.append({"code": "SOURCE_CHANGED", "source_key": f"product:{saved['_id']}", "fields": fields,
                               "saved_revision": saved["revision"], "current_revision": current["revision"],
                               "message": "원 제품 정보가 변경되었습니다. 문서의 계획값을 자동으로 바꾸지 않고 비교가 필요합니다."})
    return {"doc_id": str(doc["_id"]), "revision": doc["revision"], "checked_at": utcnow().isoformat(),
            "section_revisions": {s["section_id"]: s["revision"] for s in view["sections"]},
            "model_called": False, "issues": issues,
            "policy_pending": ["DOC-POL-01"],
            "limits": "자유 서술의 금액 합계나 사실성은 자동 확정하지 않습니다. 견적 증빙 유무는 작성·저장·다운로드를 막지 않습니다."}
