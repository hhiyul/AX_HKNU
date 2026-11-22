"""AI 채팅 3단계: 문서 초안 만들기 테스트."""
import asyncio
import json

import pytest
from bson import ObjectId

from ai_fakes import COMPLETED, FakeProvider, answer, delta, tool_call
from app.ai.events import RunEvents
from app.ai.runtime import Runtime
from app.services import ai_chat, chat
from app.services.ai_tools import is_document_request, is_search_only_request

DOCUMENT_HINT = "Create a free-form Markdown document"


@pytest.fixture
async def setup(client, login, db):
    me = await login(client)
    created = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return {"client": client, "user_id": ObjectId(me["id"]), "project_id": created["id"]}


async def run(db, setup, provider, text, **options):
    runtime = Runtime(provider, RunEvents(), {}, "gpt-6-luna")
    project = await db.projects.find_one({"_id": ObjectId(setup["project_id"])})
    run_id = await ai_chat.start_run(db, runtime, setup["user_id"], project, text, **options)
    await asyncio.wait_for(runtime.tasks[str(run_id)], 5)
    return runtime, run_id


async def last_answer(db):
    return await db.messages.find_one({"role": "ASSISTANT"}, sort=[("ordinal", -1)])


def tool_names(wire):
    return [tool["name"] for tool in wire["tools"]]


@pytest.mark.parametrize(("text", "expected"), [
    ("웹 검색으로 최신 텀블러 시장 사례를 다시 찾아 출처를 알려 주세요.", True),
    ("기획서 작성법을 검색해 알려 주세요.", True),
    ("보고서 작성 방법을 검색해 알려 주세요.", True),
    ("사업계획서 작성하지 말고 웹 검색해 주세요.", True),
    ("문서를 만들지 말고 관련 자료를 찾아 주세요.", True),
    ("Search for examples and cite sources.", True),
    ("웹 검색을 바탕으로 사업계획서를 작성해 줘.", False),
    ("검색해서 팔찌 소개 자료를 만들어 주세요.", False),
    ("시장 사례를 조사해 보고서로 정리해 주세요.", False),
    ("팔찌 소개 자료를 만들어 주세요.", False),
])
def test_search_only_routing_boundary(text, expected):
    assert is_search_only_request(text) is expected
    if expected:
        assert not is_document_request(text)


def test_additional_record_is_not_a_document_request():
    assert not is_document_request("추가 기록 문서를 만들어 주세요")
    assert is_document_request("추가 기록을 참고해 사업계획서를 작성해 주세요")
    assert is_document_request("상품 기획서 작성해 줘")


async def test_document_mode_saves_doc_on_answer(db, setup):
    body = "# 칠보 팔찌 상품 기획서\n\n## 고객\n30대 직장인"
    provider = FakeProvider([answer(body)])
    runtime, run_id = await run(db, setup, provider, "정리해 줘", mode="document")
    message = await last_answer(db)
    assert (message["doc"]["title"], message["doc"]["content"]) == ("칠보 팔찌 상품 기획서", body)
    assert {"type": "plan", "id": "ck_01", "revision": 1} in message["doc"]["sources"]
    assert DOCUMENT_HINT in provider.calls[0]["input"][0]["content"]
    assert "route_document" not in tool_names(provider.calls[0])
    events = [(e["kind"], e["content"]) async for e in runtime.events.subscribe(str(run_id))]
    assert ("document_candidate", str(message["_id"])) in events
    res = await setup["client"].post(
        f"/projects/{setup['project_id']}/items/document", json={"message_id": str(message["_id"])}
    )
    assert res.status_code == 201 and res.json()["title"] == "칠보 팔찌 상품 기획서"


async def test_document_title_falls_back(db, setup):
    await run(db, setup, FakeProvider([answer("제목 없는 본문")]), "정리해 줘", mode="document")
    assert (await last_answer(db))["doc"]["title"] == "생성 문서"


async def test_auto_detects_document_request(db, setup):
    provider = FakeProvider([answer("# 기획서\n본문")])
    await run(db, setup, provider, "상품 기획서 작성해 줘")
    assert DOCUMENT_HINT in provider.calls[0]["input"][0]["content"]
    assert (await last_answer(db))["doc"]["title"] == "기획서"


async def test_chat_answer_has_no_doc(db, setup):
    provider = FakeProvider([answer("그냥 답")])
    await run(db, setup, provider, "고객이 누구일까?")
    assert "route_document" in tool_names(provider.calls[0])
    assert DOCUMENT_HINT not in provider.calls[0]["input"][0]["content"]
    assert (await last_answer(db))["doc"] is None


async def test_route_tool_switches_to_document(db, setup):
    provider = FakeProvider([
        [delta("잠시만요"), tool_call("route_document", {}), COMPLETED],
        answer("# 소개 자료\n본문"),
    ])
    runtime, run_id = await run(db, setup, provider, "이걸 남길 수 있게 정리해 줘")
    message = await last_answer(db)
    assert message["content"] == "# 소개 자료\n본문"
    assert message["doc"]["title"] == "소개 자료"
    assert DOCUMENT_HINT in provider.calls[1]["input"][0]["content"]
    assert "route_document" not in tool_names(provider.calls[1])
    kinds = [e["kind"] async for e in runtime.events.subscribe(str(run_id))]
    assert "document_route" in kinds


async def test_bad_route_calls_fail(db, setup):
    provider = FakeProvider([[tool_call("route_document", {"x": 1}), COMPLETED]])
    runtime, run_id = await run(db, setup, provider, "정리해 줘")
    assert (await db.ai_runs.find_one({"_id": run_id}))["error_code"] == "INVALID_TOOL_ARGUMENTS"


async def test_search_only_request_ignores_forged_route(db, setup):
    provider = FakeProvider([[tool_call("route_document", {}), COMPLETED], answer("검색 답변")])
    runtime, run_id = await run(db, setup, provider, "웹 검색으로 텀블러 시장 사례를 찾아 주세요.")
    assert "route_document" not in tool_names(provider.calls[0])
    output = json.loads(provider.calls[1]["input"][-1]["output"])
    assert output["error"] == "DOCUMENT_ROUTE_NOT_REQUESTED"
    message = await last_answer(db)
    assert (message["content"], message["doc"]) == ("검색 답변", None)


async def test_chat_mode_never_routes(db, setup):
    provider = FakeProvider([[tool_call("route_document", {}), COMPLETED]])
    runtime, run_id = await run(db, setup, provider, "기획서 작성해 줘", mode="chat")
    assert "route_document" not in tool_names(provider.calls[0])
    assert DOCUMENT_HINT not in provider.calls[0]["input"][0]["content"]
    assert (await db.ai_runs.find_one({"_id": run_id}))["error_code"] == "TOOL_NOT_ALLOWED"


@pytest.mark.parametrize("include_session", [False, True])
async def test_document_history_requires_explicit_inclusion(db, setup, include_session):
    project = await db.projects.find_one({"_id": ObjectId(setup["project_id"])})
    await chat.add_message(db, setup["user_id"], project["_id"], "USER", "예전 대화-비밀")
    provider = FakeProvider([answer("# 문서\n본문")])
    await run(db, setup, provider, "정리해 줘", mode="document", include_session=include_session)
    wire = json.dumps(provider.calls[0]["input"], ensure_ascii=False)
    assert ("예전 대화-비밀" in wire) is include_session


async def test_partial_document_has_no_doc(db, setup):
    gate = asyncio.Event()
    provider = FakeProvider([answer("# 문서", " 나머지")], gate=gate)
    runtime = Runtime(provider, RunEvents(), {}, "gpt-6-luna")
    project = await db.projects.find_one({"_id": ObjectId(setup["project_id"])})
    run_id = await ai_chat.start_run(db, runtime, setup["user_id"], project, "x", mode="document")
    await asyncio.wait_for(provider.paused.wait(), 5)
    await ai_chat.stop_run(db, runtime, setup["user_id"], project["_id"], str(run_id))
    await asyncio.wait_for(runtime.tasks.get(str(run_id)) or asyncio.sleep(0), 5)
    message = await last_answer(db)
    assert (message["completion"], message["doc"]) == ("PARTIAL", None)


async def test_runs_api_accepts_mode(client, setup):
    from app.ai.runtime import set_runtime

    runtime = Runtime(FakeProvider([answer("# 문서\n본문")]), RunEvents(), {}, "gpt-6-luna")
    set_runtime(runtime)
    try:
        url = f"/projects/{setup['project_id']}/runs"
        res = await client.post(url, json={"text": "x", "mode": "document", "include_session": True})
        assert res.status_code == 202, res.text
        await asyncio.wait_for(runtime.tasks[res.json()["run_id"]], 5)
        assert (await client.post(url, json={"text": "x", "mode": "report"})).status_code == 422
    finally:
        set_runtime(None)
