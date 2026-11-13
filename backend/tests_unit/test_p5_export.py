import io
import os
import shutil
import zipfile

import httpx
import pytest
from docx import Document

from app.ai.p5.export import ExportError, export
from app.ai.p5.provider import DemoProvider
from app.ai.p5.server import create_app
from app.ai.p5.store import Store


def available(setting, executable):
    path = os.environ.get(setting)
    return os.path.isfile(path) if path else bool(shutil.which(executable))


def test_missing_pandoc_preserves_nonoffice_exports(monkeypatch):
    monkeypatch.setenv('P5_PANDOC_PATH', 'missing-pandoc')
    assert export('# 한글', 'md')[0] == '# 한글'.encode()
    assert '한글' in export('# 한글', 'txt')[0].decode()
    with pytest.raises(ExportError, match='PANDOC_UNAVAILABLE'):
        export('# 한글', 'docx')


@pytest.mark.asyncio
async def test_unavailable_converter_returns_503_and_keeps_document(monkeypatch):
    monkeypatch.setenv('P5_PANDOC_PATH', 'missing-pandoc')
    db = Store()
    app = create_app(db, DemoProvider())
    sid = db.create_session('demo', 'demo', '대화')['id']
    cid = db.candidate('demo', sid, '제목', '# 한글 본문', [])
    doc = db.save_candidate('demo', sid, cid)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        path = f"/p5/projects/demo/documents/{doc}"
        response = await client.get(path + '/export/docx')
        assert response.status_code == 503
        assert response.json()['detail'] == 'PANDOC_UNAVAILABLE'
        assert (await client.get(path)).json()['body'] == '# 한글 본문'
    db.close()


@pytest.mark.skipif(not available('P5_PANDOC_PATH', 'pandoc') or not available('P5_SOFFICE_PATH', 'soffice'), reason='Pandoc and LibreOffice are required for converter integration')
def test_real_pandoc_and_libreoffice_convert_korean_table_list_and_link():
    body = '# 한글 기획서\n\n**‘첫 번째 메모’, ‘두 번째 메모’**가 저장되었습니다.\n\n- 목록 항목\n\n| 항목 | 내용 |\n| --- | --- |\n| 가격 | 미확인 |\n\n[예시 링크](https://example.com)'
    docx, _ = export(body, 'docx')
    document = Document(io.BytesIO(docx))
    assert document.tables[0].cell(1, 1).text == '미확인'
    assert '한글 기획서' in '\n'.join(p.text for p in document.paragraphs)
    assert any(run.bold and '첫 번째 메모' in run.text for p in document.paragraphs for run in p.runs)
    assert '**' not in '\n'.join(p.text for p in document.paragraphs)
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        assert b'https://example.com' in archive.read('word/_rels/document.xml.rels')
        assert b'<w:numPr>' in archive.read('word/document.xml')
    pdf, _ = export(body, 'pdf')
    assert pdf.startswith(b'%PDF-')
