"""AI 기록 검토안(propose_record) 테스트. AI는 검토안만 만들고 저장은 사용자가 한다."""
import asyncio
import json

import pytest
from bson import ObjectId

from ai_fakes import COMPLETED, FakeProvider, answer, tool_call
from app.ai.events import RunEvents
from app.ai.runtime import Runtime
from app.services import ai_chat

PROPOSAL = {"title": "고객 정리", "body": "30대 직장인, 선물용 수요"}


@pytest.fixture
async def setup(client, login, db):
    me = await login(client)
    created = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return {"client": client, "user_id": ObjectId(me["id"]), "project_id": created["id"]}


async def run(db, setup, provider, text="지금까지 얘기 기록해 줘", **options):
    runtime = Runtime(provider, RunEvents(), {}, "gpt-6-luna")
    project = await db.projects.find_one({"_id": ObjectId(setup["project_id"])})
    run_id = await ai_chat.start_run(db, runtime, setup["user_id"], project, text, **options)
    await asyncio.wait_for(runtime.tasks[str(run_id)], 5)
    return runtime, run_id


async def test_record_proposal_is_shown_not_saved(db, setup):
    provider = FakeProvider([[tool_call("propose_record", PROPOSAL), COMPLETED], answer("검토안을 만들었어요")])
    runtime, run_id = await run(db, setup, provider)
    assert "propose_record" in [tool["name"] for tool in provider.calls[0]["tools"]]
    output = json.loads(provider.calls[1]["input"][-1]["output"])
    assert output["status"] == "review_required"
    assert await db.items.count_documents({"kind": "record"}) == 0
    events = [(e["kind"], e["content"]) async for e in runtime.events.subscribe(str(run_id))]
    candidate = next(json.loads(content) for kind, content in events if kind == "record_candidate")
    assert candidate == PROPOSAL
    message = await db.messages.find_one({"role": "ASSISTANT"})
    assert message["record_draft"] == {"title": "고객 정리", "content": "30대 직장인, 선물용 수요"}
    listed = (await setup["client"].get(f"/projects/{setup['project_id']}/messages")).json()
    assert listed[-1]["record_draft"] == {"title": "고객 정리", "content": "30대 직장인, 선물용 수요"}


async def test_saving_reviewed_record_uses_draft_unless_edited(db, setup):
    provider = FakeProvider([[tool_call("propose_record", PROPOSAL), COMPLETED], answer("검토안")])
    await run(db, setup, provider)
    message_id = str((await db.messages.find_one({"role": "ASSISTANT"}))["_id"])
    url = f"/projects/{setup['project_id']}/items/record"
    saved = (await setup["client"].post(url, json={"message_id": message_id})).json()
    assert (saved["title"], saved["content"]) == ("고객 정리", "30대 직장인, 선물용 수요")
    edited = (await setup["client"].post(url, json={"message_id": message_id, "title": "고친 제목"})).json()
    assert edited["title"] == "고친 제목" and edited["key"] == saved["key"]


async def test_record_tool_not_offered_in_document_mode(db, setup):
    provider = FakeProvider([[tool_call("propose_record", PROPOSAL), COMPLETED]])
    runtime, run_id = await run(db, setup, provider, mode="document")
    assert "propose_record" not in [tool["name"] for tool in provider.calls[0]["tools"]]
    assert (await db.ai_runs.find_one({"_id": run_id}))["error_code"] == "TOOL_NOT_ALLOWED"


@pytest.mark.parametrize("arguments", [{"title": "", "body": "x"}, {"title": "x"}, {"title": "x", "body": "y", "z": 1}])
async def test_invalid_record_arguments_fail(db, setup, arguments):
    provider = FakeProvider([[tool_call("propose_record", arguments), COMPLETED]])
    runtime, run_id = await run(db, setup, provider)
    assert (await db.ai_runs.find_one({"_id": run_id}))["error_code"] == "INVALID_TOOL_ARGUMENTS"
