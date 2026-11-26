"""AI 채팅 대화 목록: 대화마다 이전 대화가 따로이고, 예전 메시지는 "이전 대화"로 묶인다."""
import asyncio
import json

import pytest
from bson import ObjectId

from ai_fakes import FakeProvider, answer
from app.ai.events import RunEvents
from app.ai.runtime import Runtime, set_runtime
from app.services import chat


@pytest.fixture
def use_runtime():
    def install(provider) -> Runtime:
        runtime = Runtime(provider, RunEvents(), {}, "gpt-6-luna")
        set_runtime(runtime)
        return runtime

    yield install
    set_runtime(None)


@pytest.fixture
async def ctx(client, login):
    await login(client)
    project = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return {"client": client, "url": f"/projects/{project['id']}", "project_id": project["id"]}


async def send(ctx, runtime, text, conversation_id=None):
    body = {"text": text, **({"conversation_id": conversation_id} if conversation_id else {})}
    res = await ctx["client"].post(f"{ctx['url']}/runs", json=body)
    assert res.status_code == 202, res.text
    await asyncio.wait_for(runtime.tasks[res.json()["run_id"]], 5)


async def test_each_conversation_keeps_its_own_history(ctx, use_runtime):
    client, url = ctx["client"], ctx["url"]
    provider = FakeProvider([answer("첫 답"), answer("둘째 답"), answer("셋째 답")])
    runtime = use_runtime(provider)
    first = (await client.post(f"{url}/conversations", json={})).json()
    second = (await client.post(f"{url}/conversations", json={})).json()
    assert first["title"] is None and first["status"] == "active"

    await send(ctx, runtime, "남색 유약 팔찌 고객은?", first["id"])
    await send(ctx, runtime, "포장 상자 고르기", second["id"])
    await send(ctx, runtime, "가격은?", first["id"])

    # 세 번째 실행에는 첫 대화의 이전 메시지만 들어가고, 다른 대화의 질문은 들어가지 않는다.
    wire = json.dumps(provider.calls[-1], ensure_ascii=False)
    assert "남색 유약 팔찌 고객은?" in wire and "첫 답" in wire
    assert "포장 상자 고르기" not in wire

    listed = (await client.get(f"{url}/conversations")).json()
    assert [c["id"] for c in listed] == [first["id"], second["id"]]
    assert listed[0]["title"] == "남색 유약 팔찌 고객은?" and listed[0]["message_count"] == 4
    assert listed[0]["preview"] == "셋째 답"
    only_first = (await client.get(f"{url}/messages", params={"conversation_id": first["id"]})).json()
    assert [m["content"] for m in only_first] == ["남색 유약 팔찌 고객은?", "첫 답", "가격은?", "셋째 답"]
    assert {m["conversation_id"] for m in only_first} == {first["id"]}


async def test_run_without_conversation_uses_latest_or_creates_one(ctx, use_runtime):
    client, url = ctx["client"], ctx["url"]
    runtime = use_runtime(FakeProvider([answer("하나"), answer("둘")]))
    await send(ctx, runtime, "처음 질문")
    listed = (await client.get(f"{url}/conversations")).json()
    assert len(listed) == 1 and listed[0]["title"] == "처음 질문"
    await send(ctx, runtime, "이어서")
    listed = (await client.get(f"{url}/conversations")).json()
    assert len(listed) == 1 and listed[0]["message_count"] == 4


@pytest.mark.parametrize("delivery, expected", [("not_dispatched", "failed"), ("unknown", "unknown")])
async def test_reload_keeps_failed_run_without_dispatching_again(ctx, use_runtime, delivery, expected):
    from app.ai.responses import Delivery, ProviderError
    provider = FakeProvider([], error=ProviderError("CONNECTION_FAILED" if delivery == "not_dispatched" else "TRANSPORT_ERROR", Delivery(delivery)))
    runtime = use_runtime(provider)
    await send(ctx, runtime, "재접속 후 결과 확인")
    conversation = (await ctx["client"].get(f"{ctx['url']}/conversations")).json()[0]
    for _ in range(2):
        messages = (await ctx["client"].get(f"{ctx['url']}/messages", params={"conversation_id": conversation["id"]})).json()
        assert len(messages) == 1
        assert messages[0]["run_outcome"]["status"] == expected
        assert messages[0]["run_outcome"]["delivery"] == delivery
    assert len(provider.calls) == 1


async def test_legacy_messages_move_into_one_conversation(ctx, db):
    client, url = ctx["client"], ctx["url"]
    project_id = ObjectId(ctx["project_id"])
    project = await db.projects.find_one({"_id": project_id})
    await chat.add_message(db, project["user_id"], project_id, "USER", "예전 질문")
    await chat.add_message(db, project["user_id"], project_id, "ASSISTANT", "예전 답")
    # 항목별 AI 메시지는 대화에 들어가지 않는다.
    await chat.add_message(db, project["user_id"], project_id, "USER", "[항목] 초안", extra={"purpose": "document_assist"})
    capsule = {"text": "예전 요약", "covered_ids": [], "source_ids": [], "revision": 2}
    await db.projects.update_one({"_id": project_id}, {"$set": {"chat_capsule": capsule}})

    listed = (await client.get(f"{url}/conversations")).json()
    assert [(c["title"], c["message_count"], c["preview"]) for c in listed] == [("이전 대화", 2, "예전 답")]
    messages = (await client.get(f"{url}/messages", params={"conversation_id": listed[0]["id"]})).json()
    assert [m["content"] for m in messages] == ["예전 질문", "예전 답"]
    moved = await db.projects.find_one({"_id": project_id})
    assert moved["chat_capsules"][listed[0]["id"]]["text"] == "예전 요약"
    # 두 번 불러도 다시 만들지 않는다.
    assert len((await client.get(f"{url}/conversations")).json()) == 1


async def test_rename_trash_and_restore(ctx, use_runtime):
    client, url = ctx["client"], ctx["url"]
    runtime = use_runtime(FakeProvider([answer("답")]))
    conversation = (await client.post(f"{url}/conversations", json={"title": "포장 고민"})).json()
    renamed = await client.patch(f"{url}/conversations/{conversation['id']}", json={"title": "포장 상자", "revision": conversation["revision"]})
    assert renamed.status_code == 200 and renamed.json()["title"] == "포장 상자"
    stale = await client.patch(f"{url}/conversations/{conversation['id']}", json={"title": "x", "revision": conversation["revision"]})
    assert stale.status_code == 409
    assert (await client.patch(f"{url}/conversations/{conversation['id']}", json={"title": " ", "revision": 2})).status_code == 422

    assert (await client.delete(f"{url}/conversations/{conversation['id']}")).status_code == 204
    assert (await client.get(f"{url}/conversations")).json() == []
    assert [c["id"] for c in (await client.get(f"{url}/conversations", params={"status": "trash"})).json()] == [conversation["id"]]
    blocked = await client.post(f"{url}/runs", json={"text": "질문", "conversation_id": conversation["id"]})
    assert blocked.status_code == 409 and blocked.json()["code"] == "CONVERSATION_TRASHED"

    restored = await client.post(f"{url}/conversations/{conversation['id']}/restore")
    assert restored.status_code == 200 and restored.json()["status"] == "active"
    await send(ctx, runtime, "질문", conversation["id"])
    # 이름을 정한 대화는 첫 질문으로 바뀌지 않는다.
    assert (await client.get(f"{url}/conversations")).json()[0]["title"] == "포장 상자"


async def test_conversations_are_scoped_to_owner_and_project(ctx, client, login):
    url = ctx["url"]
    conversation = (await client.post(f"{url}/conversations", json={})).json()
    other = (await client.post("/projects", json={"name": "다른 프로젝트"})).json()
    assert (await client.get(f"/projects/{other['id']}/messages", params={"conversation_id": conversation["id"]})).status_code == 404
    assert (await client.post(f"/projects/{other['id']}/runs", json={"text": "x", "conversation_id": conversation["id"]})).status_code == 404
    assert (await client.delete(f"{url}/conversations/{ObjectId()}")).status_code == 404
    await client.post("/auth/logout")
    await login(client, email="other@example.com")
    assert (await client.get(f"{url}/conversations")).status_code == 404
