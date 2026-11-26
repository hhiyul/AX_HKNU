"""AI 채팅 4단계: 참고 파일(첨부)을 AI가 읽기 테스트."""
import asyncio
import io
import json
import zipfile

import pytest
from bson import ObjectId

from ai_fakes import COMPLETED, FakeProvider, answer, tool_call
from app.ai.events import RunEvents
from app.ai.runtime import Runtime, set_runtime
from app.services import ai_context

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture(autouse=True)
def read_attachments_on(monkeypatch):
    """이 파일은 첨부 읽기를 켠 경우(AI_READ_ATTACHMENTS=true)를 시험한다. 기본은 꺼져 있다."""
    from app.config import Settings
    from app.services import ai_chat

    monkeypatch.setattr(ai_chat, "get_settings", lambda: Settings(ai_read_attachments=True))


@pytest.fixture
async def setup(client, login, db):
    me = await login(client)
    created = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return {"client": client, "user_id": ObjectId(me["id"]), "project_id": created["id"]}


@pytest.fixture
def use_runtime():
    def install(provider) -> Runtime:
        runtime = Runtime(provider, RunEvents(), {}, "gpt-6-luna")
        set_runtime(runtime)
        return runtime

    yield install
    set_runtime(None)


def make_docx(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><w:document '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


async def upload(setup, data: bytes, name: str, mime: str = "text/plain", asset_type: str = "document") -> str:
    res = await setup["client"].post(
        "/assets",
        data={"project_id": setup["project_id"], "asset_type": asset_type},
        files={"file": (name, data, mime)},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def set_refs(setup, ids):
    url = f"/projects/{setup['project_id']}"
    revision = (await setup["client"].get(url)).json()["revision"]
    res = await setup["client"].patch(url, json={"file_refs": ids, "revision": revision})
    assert res.status_code == 200, res.text


async def start(setup, runtime, text="파일 보고 답해 줘", **body):
    res = await setup["client"].post(f"/projects/{setup['project_id']}/runs", json={"text": text, **body})
    assert res.status_code == 202, res.text
    run_id = res.json()["run_id"]
    await asyncio.wait_for(runtime.tasks[run_id], 5)
    return run_id


def wire_text(wire) -> str:
    return json.dumps(wire["input"], ensure_ascii=False)


async def test_reference_files_are_given_to_ai(setup, use_runtime):
    file_id = await upload(setup, "원가표: 재료 8000원".encode(), "note.md", "text/markdown")
    await set_refs(setup, [file_id])
    provider = FakeProvider([answer("확인했어요")])
    await start(setup, use_runtime(provider))
    prefix = json.loads(provider.calls[0]["input"][1]["content"].split("\n", 1)[1])
    files = [section for section in prefix if section["key"] == f"file:{file_id}"]
    assert files and files[0]["name"] == "note.md" and files[0]["content"] == "원가표: 재료 8000원"
    assert files[0]["source"] == "attachment"


async def test_read_tool_reads_attachments_by_alias(setup, use_runtime):
    file_id = await upload(setup, "포장 단가 500원".encode(), "pack.txt")
    await set_refs(setup, [file_id])
    provider = FakeProvider([[tool_call("read_project_sections", {"keys": ["attachments"]}), COMPLETED], answer("답")])
    await start(setup, use_runtime(provider))
    output = json.loads(provider.calls[1]["input"][-1]["output"])
    assert [section["content"] for section in output] == ["포장 단가 500원"]


async def test_docx_text_is_read(setup, use_runtime):
    file_id = await upload(setup, make_docx("도면 설명", "체인 길이 18cm"), "spec.docx", DOCX_TYPE)
    await set_refs(setup, [file_id])
    provider = FakeProvider([answer("답")])
    await start(setup, use_runtime(provider))
    prefix = json.loads(provider.calls[0]["input"][1]["content"].split("\n", 1)[1])
    content = next(section["content"] for section in prefix if section["key"] == f"file:{file_id}")
    assert content == "도면 설명\n체인 길이 18cm"


async def test_message_asset_ids_are_read_and_saved(setup, use_runtime, db):
    file_id = await upload(setup, "이번에만 보는 메모".encode(), "once.txt")
    provider = FakeProvider([answer("답")])
    await start(setup, use_runtime(provider), asset_ids=[file_id])
    assert "이번에만 보는 메모" in wire_text(provider.calls[0])
    user_message = await db.messages.find_one({"role": "USER"})
    assert user_message["asset_ids"] == [ObjectId(file_id)]


async def test_foreign_asset_ids_are_rejected(setup, use_runtime, login):
    use_runtime(FakeProvider([answer("답")]))
    await login(setup["client"], email="b@test.com")
    other = (await setup["client"].post("/projects", json={"name": "남의 프로젝트"})).json()
    res = await setup["client"].post(
        "/assets", data={"project_id": other["id"], "asset_type": "document"},
        files={"file": ("x.txt", "남의 메모".encode(), "text/plain")},
    )
    foreign = res.json()["id"]
    await setup["client"].post("/auth/login", json={"email": "a@test.com", "password": "password123"})
    res = await setup["client"].post(f"/projects/{setup['project_id']}/runs", json={"text": "x", "asset_ids": [foreign]})
    assert res.status_code == 404


async def test_images_are_not_read(setup, use_runtime):
    image_id = await upload(setup, PNG, "a.png", "image/png", "product_image")
    await set_refs(setup, [image_id])
    provider = FakeProvider([answer("답")])
    await start(setup, use_runtime(provider))
    # 프로젝트 참고 이미지의 목록/미지원 상태를 알리되 원본 이미지는 자동 전송하지 않는다.
    assert f"file:{image_id}" in wire_text(provider.calls[0])
    assert 'unsupported' in wire_text(provider.calls[0])
    assert 'input_image' not in wire_text(provider.calls[0])


async def test_long_file_is_cut(setup, use_runtime):
    file_id = await upload(setup, ("가" * 60_000).encode(), "long.txt")
    await set_refs(setup, [file_id])
    provider = FakeProvider([answer("답")])
    await start(setup, use_runtime(provider))
    prefix = json.loads(provider.calls[0]["input"][1]["content"].split("\n", 1)[1])
    content = next(section["content"] for section in prefix if section["key"] == f"file:{file_id}")
    assert len(content) < 51_000 and content.endswith(ai_context.CUT_MARK)


async def test_cp949_text_upload_is_accepted_and_read(setup, use_runtime):
    file_id = await upload(setup, "한글 메모장 파일".encode("cp949"), "memo.txt")
    await set_refs(setup, [file_id])
    provider = FakeProvider([answer("답")])
    await start(setup, use_runtime(provider))
    assert "한글 메모장 파일" in wire_text(provider.calls[0])


async def test_answers_from_removed_files_leave_history(setup, use_runtime, db):
    file_id = await upload(setup, "비밀 원가 메모".encode(), "secret.txt")
    await set_refs(setup, [file_id])
    runtime = use_runtime(FakeProvider([answer("원가는 비밀 원가 메모 기준")]))
    await start(setup, runtime)
    answer_message = await db.messages.find_one({"role": "ASSISTANT"})
    assert answer_message["source_ids"] == [ObjectId(file_id)]
    await set_refs(setup, [])
    provider = FakeProvider([answer("두 번째")])
    await start(setup, use_runtime(provider), "다시 알려 줘")
    text = wire_text(provider.calls[0])
    assert "원가는 비밀 원가 메모 기준" not in text and "비밀 원가 메모" not in text


async def test_capsule_built_from_removed_file_is_not_reused(setup, db):
    project_id = ObjectId(setup["project_id"])
    from app.services import chat

    a = await chat.add_message(db, setup["user_id"], project_id, "USER", "a")
    b = await chat.add_message(db, setup["user_id"], project_id, "ASSISTANT", "b")
    current = await chat.add_message(db, setup["user_id"], project_id, "USER", "c")
    removed = str(ObjectId())
    capsule = {"text": "파일 내용 요약", "covered_ids": [str(a["_id"]), str(b["_id"])], "source_ids": [removed], "revision": 1}
    await db.projects.update_one({"_id": project_id}, {"$set": {"chat_capsule": capsule}})
    history, _ = await ai_context.load_history(db, project_id, current["_id"], active_file_ids=set())
    assert [item["content"] for item in history] == ["a", "b"]
    history, _ = await ai_context.load_history(db, project_id, current["_id"], active_file_ids={removed})
    assert history[0]["id"] == "capsule"


async def test_attachments_are_not_read_by_default(setup, use_runtime, monkeypatch, db):
    # 기획서 v2: 첨부 원본의 자동 분석은 하지 않는다. 켜야만(AI_READ_ATTACHMENTS=true) 읽는다.
    from app.config import Settings
    from app.services import ai_chat

    monkeypatch.setattr(ai_chat, "get_settings", lambda: Settings(ai_read_attachments=False))
    file_id = await upload(setup, "읽으면 안 되는 메모".encode(), "memo.txt")
    provider = FakeProvider([answer("답")])
    await start(setup, use_runtime(provider), asset_ids=[file_id])
    assert "읽으면 안 되는 메모" not in wire_text(provider.calls[0])
    assert (await db.messages.find_one({"role": "USER"}))["asset_ids"] == [ObjectId(file_id)]
