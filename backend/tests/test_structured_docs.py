"""v05 항목형 전용 문서(펀딩 상세페이지·사업계획서) 테스트."""
import io
import zipfile

import pytest

FUNDING = ["F01", "F02", "F03", "F04", "F05", "F06", "F07", "F08"]
BUSINESS = [f"B0{n}" for n in range(1, 9)]


@pytest.fixture
async def url(client, login):
    await login(client)
    project = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return f"/projects/{project['id']}"


async def make_doc(client, url, template_id="funding", **body):
    res = await client.post(f"{url}/docs", json={"template_id": template_id, **body})
    assert res.status_code == 201, res.text
    return res.json()


def section(doc, section_id):
    return next(s for s in doc["sections"] if s["section_id"] == section_id)


async def test_templates_list_two_forms(client, login):
    await login(client)
    templates = (await client.get("/templates")).json()
    funding = next(t for t in templates if t["template_id"] == "funding")
    business = next(t for t in templates if t["template_id"] == "business_plan")
    assert [s["section_id"] for s in funding["sections"]] == FUNDING
    assert sum(s["required"] for s in funding["sections"]) == 6
    assert [s["section_id"] for s in business["sections"]] == BUSINESS
    assert sum(s["required"] for s in business["sections"]) == 8
    assert funding["version"] == 1 and funding["sections"][0]["question"]


async def test_create_funding_doc_with_all_sections_unwritten(client, url):
    doc = await make_doc(client, url)
    assert doc["template_id"] == "funding" and doc["template_version"] == 1
    assert doc["title"] == "펀딩 상세페이지"
    assert [s["section_id"] for s in doc["sections"]] == FUNDING
    assert {s["state"] for s in doc["sections"]} == {"UNWRITTEN"}
    assert section(doc, "F05")["title"] == "리워드·가격·수량" and section(doc, "F05")["required"] is True
    assert doc["progress"] == {"required_total": 6, "required_reviewed": 0, "optional_total": 2, "optional_reviewed": 0}


async def test_project_can_hold_several_docs_and_duplicate_click_creates_one(client, url):
    headers = {"Idempotency-Key": "new-doc-1"}
    first = await client.post(f"{url}/docs", json={"template_id": "funding"}, headers=headers)
    again = await client.post(f"{url}/docs", json={"template_id": "funding"}, headers=headers)
    assert first.json()["id"] == again.json()["id"]
    await make_doc(client, url, "business_plan", title="2026 사업계획서")
    await make_doc(client, url, "funding", title="두 번째 펀딩")
    docs = (await client.get(f"{url}/docs")).json()
    assert len(docs) == 3 and {d["template_id"] for d in docs} == {"funding", "business_plan"}
    assert "sections" not in docs[0] and docs[0]["progress"]["required_total"] in (6, 8)
    assert (await client.post(f"{url}/docs", json={"template_id": "sns"})).status_code == 422


async def test_draft_save_and_review_flow(client, url):
    doc = await make_doc(client, url)
    base = f"{url}/docs/{doc['id']}/sections/F05"
    drafted = (await client.put(f"{base}/draft", json={"text": "1구성 35,000원", "draft_revision": 0})).json()
    assert (drafted["state"], drafted["has_draft"], drafted["draft"], drafted["draft_revision"]) == ("UNWRITTEN", True, "1구성 35,000원", 1)
    stale = await client.put(f"{base}/draft", json={"text": "옛 탭", "draft_revision": 0})
    assert stale.status_code == 409
    saved = (await client.put(base, json={"text": "1구성 35,000원", "revision": 0})).json()
    assert (saved["state"], saved["revision"], saved["has_draft"], saved["draft"]) == ("WRITING", 1, False, None)
    reviewed = (await client.post(f"{base}/review", json={"revision": 1})).json()
    assert reviewed["state"] == "REVIEWED"
    doc_now = (await client.get(f"{url}/docs/{doc['id']}")).json()
    assert doc_now["progress"]["required_reviewed"] == 1 and doc_now["last_section_id"] == "F05"
    edited = (await client.put(base, json={"text": "1구성 36,000원", "revision": 1})).json()
    assert (edited["state"], edited["revision"]) == ("WRITING", 2)
    assert (await client.post(f"{base}/review", json={"revision": 1})).status_code == 409


async def test_empty_section_cannot_be_reviewed_and_optional_counts_separately(client, url):
    doc = await make_doc(client, url)
    res = await client.post(f"{url}/docs/{doc['id']}/sections/F01/review", json={"revision": 0})
    assert res.status_code == 422 and res.json()["code"] == "SECTION_EMPTY"
    base = f"{url}/docs/{doc['id']}/sections/F03"
    await client.put(base, json={"text": "어머니께 배운 칠보", "revision": 0})
    await client.post(f"{base}/review", json={"revision": 1})
    progress = (await client.get(f"{url}/docs/{doc['id']}")).json()["progress"]
    assert (progress["required_reviewed"], progress["optional_reviewed"]) == (0, 1)


async def test_sections_can_be_written_in_any_order_and_unknown_section_is_404(client, url):
    doc = await make_doc(client, url, "business_plan")
    assert (await client.put(f"{url}/docs/{doc['id']}/sections/B07", json={"text": "혼자 제작", "revision": 0})).status_code == 200
    assert (await client.put(f"{url}/docs/{doc['id']}/sections/F01", json={"text": "x", "revision": 0})).status_code == 404


async def test_material_links_change_revision_and_must_be_mine(client, url):
    doc = await make_doc(client, url)
    project_id = url.rsplit("/", 1)[-1]
    asset = (await client.post("/assets", data={"project_id": project_id, "asset_type": "document"},
                               files={"file": ("견적.txt", "견적 3만원".encode(), "text/plain")})).json()
    base = f"{url}/docs/{doc['id']}/sections/F06"
    await client.put(base, json={"text": "제작 2주", "revision": 0})
    await client.post(f"{base}/review", json={"revision": 1})
    linked = (await client.put(base, json={"text": "제작 2주", "material_ids": [asset["id"]], "revision": 1})).json()
    assert (linked["material_ids"], linked["state"]) == ([asset["id"]], "WRITING")
    res = await client.put(base, json={"text": "x", "material_ids": ["0" * 24], "revision": 2})
    assert res.status_code == 404


async def test_discard_draft(client, url):
    doc = await make_doc(client, url)
    base = f"{url}/docs/{doc['id']}/sections/F01"
    await client.put(f"{base}/draft", json={"text": "임시", "draft_revision": 0})
    res = await client.delete(f"{base}/draft", params={"draft_revision": 1})
    assert res.status_code == 200 and res.json()["has_draft"] is False


async def test_preview_composes_saved_sections_in_order(client, url):
    doc = await make_doc(client, url, title="팔찌 펀딩")
    sections = f"{url}/docs/{doc['id']}/sections"
    await client.put(f"{sections}/F05", json={"text": "35,000원", "revision": 0})
    await client.put(f"{sections}/F01", json={"text": "칠보 팔찌", "revision": 0})
    await client.put(f"{sections}/F02/draft", json={"text": "저장 안 한 글", "draft_revision": 0})
    preview = (await client.get(f"{url}/docs/{doc['id']}/preview")).json()
    text = preview["text"]
    assert text.startswith("# 팔찌 펀딩")
    assert text.index("## 작품·프로젝트 소개") < text.index("## 리워드·가격·수량")
    assert "저장 안 한 글" not in text and "## 고객과 구매 가치" not in text
    assert preview["has_unsaved_drafts"] is True
    exported = await client.get(f"{url}/docs/{doc['id']}/export/docx")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert "35,000원" in archive.read("word/document.xml").decode("utf-8")
    md = await client.get(f"{url}/docs/{doc['id']}/export/md")
    assert md.text == text


async def test_rename_trash_and_restore(client, url):
    doc = await make_doc(client, url)
    renamed = (await client.patch(f"{url}/docs/{doc['id']}", json={"title": "새 이름", "revision": doc["revision"]})).json()
    assert renamed["title"] == "새 이름"
    assert (await client.delete(f"{url}/docs/{doc['id']}")).status_code == 204
    assert (await client.get(f"{url}/docs")).json() == []
    trashed = (await client.get(f"{url}/docs", params={"status": "trash"})).json()
    assert [d["id"] for d in trashed] == [doc["id"]]
    assert (await client.put(f"{url}/docs/{doc['id']}/sections/F01", json={"text": "x", "revision": 0})).status_code == 409
    assert (await client.post(f"{url}/docs/{doc['id']}/restore")).status_code == 200
    assert len((await client.get(f"{url}/docs")).json()) == 1


async def test_other_users_cannot_see_docs(client, url, login):
    doc = await make_doc(client, url)
    await login(client, email="b@test.com")
    assert (await client.get(f"{url}/docs/{doc['id']}")).status_code == 404
    assert (await client.get(f"{url}/docs")).status_code == 404


async def test_permanent_project_delete_removes_docs(client, url, db):
    await make_doc(client, url)
    await client.delete(url)
    assert (await client.delete(url, params={"permanent": "true"})).status_code == 204
    assert await db.structured_docs.count_documents({}) == 0
    assert await db.doc_sections.count_documents({}) == 0
