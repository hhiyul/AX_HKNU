"""산출물과 저장된 상품 기획 내보내기(TXT/MD) 테스트."""
from urllib.parse import quote

import pytest


@pytest.fixture
async def url(client, login):
    await login(client)
    project = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return f"/projects/{project['id']}"


async def make_document(client, url, title="상품 기획서", content="# 상품 기획서\n\n- 고객: [30대](https://example.com/a)"):
    res = await client.post(f"{url}/items/document", json={"title": title, "content": content})
    assert res.status_code == 201, res.text
    return res.json()


async def test_document_markdown_export(client, url):
    document = await make_document(client, url)
    res = await client.get(f"{url}/items/document/{document['key']}/export/md")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    assert res.text == document["content"]
    assert quote("상품 기획서.md") in res.headers["content-disposition"]


async def test_document_text_export_keeps_link_targets(client, url):
    document = await make_document(client, url)
    res = await client.get(f"{url}/items/document/{document['key']}/export/txt")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/plain")
    assert "#" not in res.text
    assert "상품 기획서" in res.text and "30대 (https://example.com/a)" in res.text


async def test_document_export_uses_saved_content_not_draft(client, url):
    document = await make_document(client, url)
    key = document["key"]
    await client.put(f"{url}/items/document/{key}", json={"content": "임시", "as_draft": True, "revision": 1})
    assert (await client.get(f"{url}/items/document/{key}/export/md")).text == document["content"]


async def test_document_export_rejects_other_formats_and_owners(client, url, login):
    document = await make_document(client, url)
    base = f"{url}/items/document/{document['key']}/export"
    assert (await client.get(f"{base}/html")).status_code == 422
    assert (await client.get(f"{url}/items/document/nope/export/md")).status_code == 404
    await login(client, email="b@test.com")
    assert (await client.get(f"{base}/md")).status_code == 404


async def test_plan_export_lists_six_items_and_records(client, url, db):
    await client.put(f"{url}/items/plan/ck_01", json={"content": "칠보 팔찌", "revision": 1})
    await client.put(f"{url}/items/plan/ck_05", json={"content": "35,000원", "amount": 35000, "revision": 1})
    await client.post(f"{url}/items/record", json={"title": "고객 정리", "content": "30대 직장인"})
    res = await client.get(f"{url}/plan-export/md")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/markdown")
    text = res.text
    assert text.startswith("# 저장된 상품 기획 · 칠보 팔찌")
    assert "## 1. 상품선정 · 작성됨\n\n칠보 팔찌" in text
    assert "## 2. 고객선정 · 미작성\n\n(내용 없음)" in text
    assert "ck_0" not in text
    assert "금액: 35,000원" in text
    assert "## AI와 함께 정리한 내용: 고객 정리\n\n30대 직장인" in text
    plain = (await client.get(f"{url}/plan-export/txt")).text
    assert "#" not in plain and "1. 상품선정 · 작성됨" in plain
    assert (await client.get(f"{url}/plan-export/html")).status_code == 422


async def test_document_docx_export(client, url):
    import io
    import zipfile

    document = await make_document(client, url, content="# 상품 기획서\n\n| 항목 | 값 |\n|---|---|\n| 가격 | 35,000원 |")
    res = await client.get(f"{url}/items/document/{document['key']}/export/docx")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument")
    with zipfile.ZipFile(io.BytesIO(res.content)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    assert "상품 기획서" in xml and "35,000원" in xml


async def test_document_pdf_export(client, url):
    document = await make_document(client, url)
    res = await client.get(f"{url}/items/document/{document['key']}/export/pdf")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf" and res.content.startswith(b"%PDF")


async def test_plan_export_as_docx(client, url):
    res = await client.get(f"{url}/plan-export/docx")
    assert res.status_code == 200 and res.content.startswith(b"PK")


async def test_missing_converter_is_reported(client, url, monkeypatch):
    from app.ai import export

    monkeypatch.setattr(export.shutil, "which", lambda name: None)
    monkeypatch.delenv("PANDOC_PATH", raising=False)
    document = await make_document(client, url)
    res = await client.get(f"{url}/items/document/{document['key']}/export/docx")
    assert res.status_code == 503 and res.json()["code"] == "PANDOC_UNAVAILABLE"


async def test_docx_export_never_embeds_local_or_remote_files(client, url):
    import io
    import zipfile

    content = "# 문서\n\n![비밀](/etc/passwd)\n\n![원격][r]\n\n<img src=\"/etc/hostname\">\n\n[r]: http://169.254.169.254/x"
    document = await make_document(client, url, content=content)
    res = await client.get(f"{url}/items/document/{document['key']}/export/docx")
    assert res.status_code == 200, res.text
    with zipfile.ZipFile(io.BytesIO(res.content)) as archive:
        assert not [name for name in archive.namelist() if name.startswith("word/media/")]
        assert "비밀" in archive.read("word/document.xml").decode("utf-8")
