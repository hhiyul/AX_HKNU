"""정리 내용을 AI 자료에 넣기, 기존 산출물 참고, 문서 출처 기록과 변경 확인 테스트."""
import asyncio
import json

import pytest
from bson import ObjectId

from ai_fakes import COMPLETED, FakeProvider, answer, tool_call
from app.ai.events import RunEvents
from app.ai.runtime import Runtime, set_runtime


@pytest.fixture
async def setup(client, login, db):
    me = await login(client)
    created = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return {"client": client, "user_id": ObjectId(me["id"]), "url": f"/projects/{created['id']}"}


@pytest.fixture
def use_runtime():
    def install(provider) -> Runtime:
        runtime = Runtime(provider, RunEvents(), {}, "gpt-6-luna")
        set_runtime(runtime)
        return runtime

    yield install
    set_runtime(None)


async def start(setup, runtime, **body):
    res = await setup["client"].post(f"{setup['url']}/runs", json={"text": "문서로 정리해 줘", **body})
    assert res.status_code == 202, res.text
    run_id = res.json()["run_id"]
    await asyncio.wait_for(runtime.tasks[run_id], 5)
    return run_id


def prefix_of(wire) -> dict[str, dict]:
    sections = json.loads(wire["input"][1]["content"].split("\n", 1)[1])
    return {section["key"]: section for section in sections}


async def make_record(setup, title="고객 정리", content="30대 직장인"):
    res = await setup["client"].post(f"{setup['url']}/items/record", json={"title": title, "content": content})
    return res.json()


async def make_document(setup, title="기존 기획서", content="# 기존 기획서\n가격 3만원"):
    res = await setup["client"].post(f"{setup['url']}/items/document", json={"title": title, "content": content})
    return res.json()


async def test_records_are_given_to_ai(setup, use_runtime):
    record = await make_record(setup)
    provider = FakeProvider([answer("답")])
    await start(setup, use_runtime(provider), mode="chat")
    section = prefix_of(provider.calls[0])[f"record:{record['key']}"]
    assert (section["title"], section["content"], section["source"]) == ("고객 정리", "30대 직장인", "this_project")


async def test_reference_document_is_given_in_document_mode(setup, use_runtime):
    document = await make_document(setup)
    provider = FakeProvider([answer("# 새 기획서\n본문")])
    await start(setup, use_runtime(provider), mode="document", reference_document=document["key"])
    section = prefix_of(provider.calls[0])["reference_document"]
    assert (section["content"], section["source"]) == ("# 기존 기획서\n가격 3만원", "saved")


async def test_unsaved_editor_body_can_be_referenced(setup, use_runtime):
    document = await make_document(setup)
    provider = FakeProvider([answer("# 새 기획서\n본문")])
    await start(setup, use_runtime(provider), mode="document", reference_document=document["key"], reference_body="편집 중인 글")
    section = prefix_of(provider.calls[0])["reference_document"]
    assert (section["content"], section["source"]) == ("편집 중인 글", "editor")


async def test_reference_rules(setup, use_runtime):
    use_runtime(FakeProvider([answer("답")]))
    document = await make_document(setup)
    url = f"{setup['url']}/runs"
    res = await setup["client"].post(url, json={"text": "x", "reference_document": document["key"]})
    assert res.status_code == 422 and res.json()["code"] == "REFERENCE_REQUIRES_DOCUMENT_MODE"
    res = await setup["client"].post(url, json={"text": "x", "mode": "document", "reference_body": "글"})
    assert res.status_code == 422 and res.json()["code"] == "REFERENCE_REQUIRED"
    res = await setup["client"].post(url, json={"text": "x", "mode": "document", "reference_document": "nope"})
    assert res.status_code == 404
    project = (await setup["client"].get(setup["url"])).json()
    assert project.get("active_run") is None


async def test_document_sources_and_changes(setup, use_runtime, db):
    client, url = setup["client"], setup["url"]
    await client.put(f"{url}/items/plan/ck_01", json={"content": "칠보 팔찌", "revision": 1})
    record = await make_record(setup)
    reference = await make_document(setup)
    file_res = await client.post(
        "/assets", data={"project_id": url.rsplit("/", 1)[-1], "asset_type": "document"},
        files={"file": ("note.txt", "원가 메모".encode(), "text/plain")},
    )
    file_id = file_res.json()["id"]
    revision = (await client.get(url)).json()["revision"]
    res = await client.patch(url, json={"file_refs": [file_id], "search_enabled": True, "revision": revision})
    assert res.status_code == 200, res.text
    search = tool_call("web_search_generalized", {"concepts": ["craft"]})
    provider = FakeProvider(
        [[search, COMPLETED], answer("# 새 기획서\n본문")],
        search_results=[{"text": "결과", "urls": ["https://example.com/a"]}],
    )
    await start(setup, use_runtime(provider), mode="document", reference_document=reference["key"])
    message = await db.messages.find_one({"role": "ASSISTANT"})
    sources = {(s["type"], s["id"]): s for s in message["doc"]["sources"]}
    assert sources[("plan", "ck_01")]["revision"] == 2
    assert sources[("record", record["key"])]["revision"] == 1
    assert sources[("reference_document", reference["key"])]["source"] == "saved"
    assert ("attachment", file_id) in sources and ("web", "https://example.com/a") in sources

    saved = (await client.post(f"{url}/items/document", json={"message_id": str(message["_id"])})).json()
    changes_url = f"{url}/items/document/{saved['key']}/changes"
    status = {(c["type"], c["id"]): c["status"] for c in (await client.get(changes_url)).json()["changes"]}
    assert status[("plan", "ck_01")] == "unchanged" and status[("record", record["key"])] == "unchanged"
    assert status[("attachment", file_id)] == "unchanged" and status[("web", "https://example.com/a")] == "unavailable"

    await client.put(f"{url}/items/plan/ck_01", json={"content": "칠보 팔찌 2", "revision": 2})
    await client.delete(f"{url}/items/record/{record['key']}", params={"revision": 1})
    await client.delete(f"/assets/{file_id}")
    status = {(c["type"], c["id"]): c["status"] for c in (await client.get(changes_url)).json()["changes"]}
    assert status[("plan", "ck_01")] == "changed"
    assert status[("record", record["key"])] == "unavailable"
    assert status[("attachment", file_id)] == "unavailable"


async def test_document_without_sources_has_no_changes(setup):
    document = await make_document(setup)
    res = await setup["client"].get(f"{setup['url']}/items/document/{document['key']}/changes")
    assert res.status_code == 200 and res.json() == {"sources": [], "changes": []}
