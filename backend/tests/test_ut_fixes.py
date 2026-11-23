"""UT 전 최종 리뷰에서 나온 문제의 회귀 테스트."""
import asyncio
import io
import json
import threading
import time
import zipfile

import httpx
import pytest
from bson import ObjectId

from ai_fakes import COMPLETED, FakeProvider, answer, tool_call
from app.ai import export
from app.ai.events import RunEvents
from app.ai.provider import OpenAIProvider
from app.ai.runtime import Runtime, set_runtime

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
async def setup(client, login, db):
    me = await login(client)
    created = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return {"client": client, "user_id": ObjectId(me["id"]), "url": f"/projects/{created['id']}", "id": created["id"]}


@pytest.fixture
def use_runtime():
    def install(provider) -> Runtime:
        runtime = Runtime(provider, RunEvents(), {}, "gpt-6-luna")
        set_runtime(runtime)
        return runtime

    yield install
    set_runtime(None)


async def upload(setup, data, name, mime="text/plain", asset_type="document"):
    res = await setup["client"].post(
        "/assets", data={"project_id": setup["id"], "asset_type": asset_type}, files={"file": (name, data, mime)}
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def start(setup, runtime, text="질문", **body):
    res = await setup["client"].post(f"{setup['url']}/runs", json={"text": text, **body})
    assert res.status_code == 202, res.text
    await asyncio.wait_for(runtime.tasks[res.json()["run_id"]], 5)


async def docx_media(setup, content):
    res = await setup["client"].post(f"{setup['url']}/items/document", json={"title": "문서", "content": content})
    key = res.json()["key"]
    exported = await setup["client"].get(f"{setup['url']}/items/document/{key}/export/docx")
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        return [name for name in archive.namelist() if name.startswith("word/media/")]


# ---------- 1. DOCX 내보내기로 서버 파일·내부 주소를 읽지 못하게 ----------


@pytest.mark.parametrize("content", [
    "![x]\n\n[x]: /app/main.py",
    "![a [b] c](/app/main.py)",
    "![x](main.py)",
    "![x](data:image/png;base64,iVBORw0KGgo=)",
])
async def test_docx_export_embeds_no_images(setup, content):
    assert await docx_media(setup, content) == []


async def test_docx_export_cannot_read_uploaded_images(setup):
    image_id = await upload(setup, PNG, "a.png", "image/png", "product_image")
    assert await docx_media(setup, f"![p](/tmp/uploads/{image_id})\n\n![q]\n\n[q]: /tmp/uploads/{image_id}") == []


# ---------- 2. 한 번 붙인 파일에 대한 답변이 다음 질문에서 사라지지 않게 ----------


async def test_answer_about_attached_file_stays_in_history(setup, use_runtime, db):
    file_id = await upload(setup, "견적 메모".encode(), "quote.txt")
    await start(setup, use_runtime(FakeProvider([answer("요약: 견적 메모")])), "요약해 줘", asset_ids=[file_id])
    provider = FakeProvider([answer("자세히")])
    await start(setup, use_runtime(provider), "2번 더 자세히")
    assert "요약: 견적 메모" in json.dumps(provider.calls[0]["input"], ensure_ascii=False)
    await setup["client"].delete(f"/assets/{file_id}")
    provider = FakeProvider([answer("다시")])
    await start(setup, use_runtime(provider), "또")
    assert "요약: 견적 메모" not in json.dumps(provider.calls[0]["input"], ensure_ascii=False)


# ---------- 3. 사진이 많아도 방금 붙인 글 파일을 읽게 ----------


async def test_attached_text_file_is_read_even_with_many_photos(setup, use_runtime):
    photos = [await upload(setup, PNG, f"p{n}.png", "image/png", "product_image") for n in range(11)]
    revision = (await setup["client"].get(setup["url"])).json()["revision"]
    await setup["client"].patch(setup["url"], json={"file_refs": photos, "revision": revision})
    file_id = await upload(setup, "방금 붙인 메모".encode(), "now.txt")
    provider = FakeProvider([answer("답")])
    await start(setup, use_runtime(provider), asset_ids=[file_id])
    assert "방금 붙인 메모" in json.dumps(provider.calls[0]["input"], ensure_ascii=False)


# ---------- 4. 문서 만들기 버튼도 앞 대화를 기본으로 넣는다 ----------


async def test_document_mode_includes_conversation_by_default(setup, use_runtime):
    await start(setup, use_runtime(FakeProvider([answer("고객은 30대로 정했어요")])), "고객은 30대로 하자")
    provider = FakeProvider([answer("# 기획서\n본문")])
    await start(setup, use_runtime(provider), "정리", mode="document")
    assert "고객은 30대로 정했어요" in json.dumps(provider.calls[0]["input"], ensure_ascii=False)
    provider = FakeProvider([answer("# 기획서\n본문")])
    await start(setup, use_runtime(provider), "정리", mode="document", include_session=False)
    assert "고객은 30대로 정했어요" not in json.dumps(provider.calls[0]["input"], ensure_ascii=False)


# ---------- 5. 문서 답변에 기록 검토안이 붙지 않게 ----------


async def test_route_clears_record_draft(setup, use_runtime, db):
    provider = FakeProvider([
        [tool_call("propose_record", {"title": "기록", "body": "내용"}), COMPLETED],
        [tool_call("route_document", {}, call_id="c2"), COMPLETED],
        answer("# 문서\n본문"),
    ])
    await start(setup, use_runtime(provider), "기록하고 문서로 남겨 줘")
    message = await db.messages.find_one({"role": "ASSISTANT"})
    assert message["doc"]["title"] == "문서" and message["record_draft"] is None


# ---------- 6. 모델 이름은 날짜 붙은 스냅숏만 같은 모델로 ----------


@pytest.mark.parametrize("reported", ["gpt-6-luna-mini", "gpt-6-luna-2026-10"])
async def test_other_models_with_same_prefix_are_rejected(reported):
    async def handler(request):
        data = 'data: {"type":"response.completed","response":{"status":"completed","model":"%s"}}\n\n' % reported
        return httpx.Response(200, text=data, headers={"content-type": "text/event-stream"})

    client = OpenAIProvider("sk-fake-test", "gpt-6-luna", httpx.MockTransport(handler))
    with pytest.raises(Exception):
        [event async for event in client.stream({"model": "gpt-6-luna", "input": [], "tools": []})]


# ---------- 7. 동시에 여러 명이 PDF를 내보내도 변환은 2개씩만 ----------


def test_office_conversions_run_at_most_two_at_once(monkeypatch):
    running, peak, lock = [0], [0], threading.Lock()

    def slow_run(args, input_bytes=None, cwd=None):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.2)
        with lock:
            running[0] -= 1
        raise export.ExportError("CONVERSION_FAILED")

    monkeypatch.setattr(export, "_run", slow_run)
    monkeypatch.setattr(export, "_converter", lambda setting, name: name)
    threads = [threading.Thread(target=lambda: pytest.raises(export.ExportError, export.export, "x", "docx")) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak[0] == 2
