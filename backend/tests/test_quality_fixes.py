"""UT 전 보강: 채팅이 작업 자료를 본다, 긴 대화의 최근 메시지, 오늘 날짜, 로그인 제한, 업로드 개수, 용도 범위."""
import asyncio
import datetime as dt
import json

import pytest
from bson import ObjectId

from ai_fakes import FakeProvider, answer, tool_call
from app.ai.context import SCOPE_POLICY, build_wire
from app.ai.events import RunEvents
from app.ai.runtime import Runtime, set_runtime
from app.services import chat, home, rate_limit
from app.services.task_proposals import parse_proposals


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
    url = f"/projects/{project['id']}"
    doc = (await client.post(f"{url}/docs", json={"template_id": "funding"})).json()
    return {"client": client, "url": url, "project": project, "doc_id": doc["id"]}


def context_of(provider: FakeProvider, call: int = 0) -> dict:
    """모델에 보낸 프로젝트 자료(두 번째 입력)를 key별로 돌려준다."""
    raw = provider.calls[call]["input"][1]["content"].split("\n", 1)[1]
    return {item["key"]: item for item in json.loads(raw)}


async def run_chat(ctx, runtime, text="상태 알려 줘", **extra):
    res = await ctx["client"].post(f"{ctx['url']}/runs", json={"text": text, "mode": "chat", **extra})
    assert res.status_code == 202, res.text
    await asyncio.wait_for(runtime.tasks[res.json()["run_id"]], 5)


async def test_chat_sees_documents_tasks_costs_ledger_and_memo(ctx, use_runtime):
    client, url, doc_url = ctx["client"], ctx["url"], f"{ctx['url']}/docs/{ctx['doc_id']}"
    section = next(s for s in (await client.get(doc_url)).json()["sections"] if s["section_id"] == "F01")
    saved = (await client.put(f"{doc_url}/sections/F01", json={"text": "남색 칠보 팔찌 소개", "revision": section["revision"]})).json()
    await client.post(f"{doc_url}/sections/F01/review", json={"revision": saved["revision"]})
    f02 = next(s for s in (await client.get(doc_url)).json()["sections"] if s["section_id"] == "F02")
    await client.put(f"{doc_url}/sections/F02/draft", json={"text": "아직 임시 글", "draft_revision": f02["draft_revision"]})
    await client.post(f"{url}/tasks", json={"title": "제작처 견적 받기", "date": "2026-12-01"})
    await client.post(f"{url}/tasks", json={"title": "지난 일", "date": "2020-01-01"})
    await client.put(f"{url}/economics", json={"cost": {"material": 9200, "labor": 13500, "packaging": 1500, "shipping": 0,
                                                         "other": 800, "fee_rate": 8, "price": 49000}, "revision": 0})
    await client.post(f"{url}/ledger", json={"type": "income", "title": "팔찌 판매", "amount": 49000, "date": "2026-11-20"})
    project = (await client.get(url)).json()
    await client.patch(url, json={"note": "체인 단가 확인 필요", "revision": project["revision"]})

    provider = FakeProvider([answer("답")])
    await run_chat(ctx, use_runtime(provider))
    sent = context_of(provider)

    assert sent["today"]["date"] == home.korean_today().isoformat() and sent["today"]["timezone"] == "Asia/Seoul"
    doc = sent[f"doc:{ctx['doc_id']}"]
    assert doc["template"] == "펀딩 상세페이지" and doc["progress"] == "필수 항목 1/6 검토 완료"
    first, second = doc["sections"][0], doc["sections"][1]
    assert (first["state"], first["saved_text"]) == ("검토 완료", "남색 칠보 팔찌 소개")
    # 임시 입력은 넘기지 않는다. 상태 코드도 한국어로만 넘긴다.
    assert second["saved_text"] == "" and "아직 임시 글" not in json.dumps(sent, ensure_ascii=False)
    assert "REVIEWED" not in json.dumps(doc) and "UNWRITTEN" not in json.dumps(doc)
    assert [(t["title"], t["overdue"]) for t in sent["tasks"]["open"]] == [("지난 일", True), ("제작처 견적 받기", False)]
    assert sent["economics"]["cost_per_item"]["재료비"] == 9200 and sent["economics"]["price_being_compared"] == 49000
    assert sent["ledger"]["income_total"] == 49000 and sent["ledger"]["recent"][0]["type"] == "수입"
    assert sent["project_memo"]["content"] == "체인 단가 확인 필요"
    assert all(value["source"] == "this_project" for value in sent.values() if "source" in value)


async def test_empty_workspace_sends_only_today(ctx, use_runtime):
    await ctx["client"].delete(f"{ctx['url']}/docs/{ctx['doc_id']}")
    provider = FakeProvider([answer("답")])
    await run_chat(ctx, use_runtime(provider))
    sent = context_of(provider)
    assert "today" in sent
    assert not any(key.startswith("doc:") or key in ("tasks", "economics", "ledger", "project_memo") for key in sent)


async def test_long_section_text_is_clipped(ctx, use_runtime):
    client, doc_url = ctx["client"], f"{ctx['url']}/docs/{ctx['doc_id']}"
    section = next(s for s in (await client.get(doc_url)).json()["sections"] if s["section_id"] == "F01")
    await client.put(f"{doc_url}/sections/F01", json={"text": "가" * 3000, "revision": section["revision"]})
    provider = FakeProvider([answer("답")])
    await run_chat(ctx, use_runtime(provider))
    text = context_of(provider)[f"doc:{ctx['doc_id']}"]["sections"][0]["saved_text"]
    assert text.startswith("가" * 1500) and text.endswith("(이하 생략)") and len(text) < 1600


async def test_conversation_messages_are_the_latest_in_order(ctx, db):
    client, url = ctx["client"], ctx["url"]
    conversation = (await client.post(f"{url}/conversations", json={})).json()
    project_id, cid = ObjectId(ctx["project"]["id"]), ObjectId(conversation["id"])
    owner = (await db.projects.find_one({"_id": project_id}))["user_id"]
    for index in range(6):
        await chat.add_message(db, owner, project_id, "USER", f"질문 {index}", conversation_id=cid)
    latest = (await client.get(f"{url}/messages", params={"conversation_id": conversation["id"], "limit": 4})).json()
    assert [m["content"] for m in latest] == ["질문 2", "질문 3", "질문 4", "질문 5"]
    older = (await client.get(f"{url}/messages", params={"conversation_id": conversation["id"], "limit": 4, "skip": 4})).json()
    assert [m["content"] for m in older] == ["질문 0", "질문 1"]
    # 대화를 지정하지 않으면 예전처럼 처음부터
    first = (await client.get(f"{url}/messages", params={"limit": 2})).json()
    assert [m["content"] for m in first] == ["질문 0", "질문 1"]


async def test_section_ai_gets_today(ctx, use_runtime):
    provider = FakeProvider([answer("후보")])
    runtime = use_runtime(provider)
    res = await ctx["client"].post(f"{ctx['url']}/docs/{ctx['doc_id']}/sections/F01/assist", json={"action": "draft", "references": []})
    await asyncio.wait_for(runtime.tasks[res.json()["run_id"]], 5)
    sent = context_of(provider)
    assert sent["today"]["date"] == home.korean_today().isoformat()


def test_past_proposal_dates_are_dropped():
    args = json.dumps({"tasks": [
        {"title": "지난 일", "date": "2026-11-01", "note": ""},
        {"title": "오늘 일", "date": "2026-11-25", "note": ""},
        {"title": "다음 일", "date": "2026-12-01", "note": ""},
    ]})
    dates = [p["suggested_date"] for p in parse_proposals(args, dt.date(2026, 11, 25))]
    assert dates == [None, "2026-11-25", "2026-12-01"]
    # 기준일을 주지 않으면 그대로(예전 호출과 호환)
    assert parse_proposals(args)[0]["suggested_date"] == "2026-11-01"


async def test_proposals_from_ai_drop_past_dates(ctx, use_runtime):
    proposal = {"tasks": [{"title": "지난 견적", "date": "2000-01-01", "note": ""}]}
    provider = FakeProvider([[tool_call("suggest_tasks", proposal), {"type": "completed"}]])
    runtime = use_runtime(provider)
    res = await ctx["client"].post(f"{ctx['url']}/docs/{ctx['doc_id']}/sections/F06/task-proposals", json={})
    await asyncio.wait_for(runtime.tasks[res.json()["run_id"]], 5)
    messages = (await ctx["client"].get(f"{ctx['url']}/docs/{ctx['doc_id']}/sections/F06/messages")).json()
    assert [p["suggested_date"] for m in messages for p in (m.get("proposals") or [])] == [None]


async def test_login_is_limited_after_repeated_failures(client, login):
    await login(client, email="limit@test.com")
    for _ in range(rate_limit.EMAIL_FAILURES):
        res = await client.post("/auth/login", json={"email": "limit@test.com", "password": "wrong-password"})
        assert res.status_code == 401
    blocked = await client.post("/auth/login", json={"email": "limit@test.com", "password": "password123"})
    assert blocked.status_code == 429 and blocked.json()["code"] == "LOGIN_RATE_LIMITED"
    # 다른 이메일은 막히지 않는다.
    await login(client, email="other@test.com")
    assert (await client.post("/auth/login", json={"email": "other@test.com", "password": "password123"})).status_code == 200


async def test_successful_login_resets_email_failures(client, login):
    await login(client, email="reset@test.com")
    for _ in range(rate_limit.EMAIL_FAILURES - 1):
        await client.post("/auth/login", json={"email": "reset@test.com", "password": "wrong-password"})
    assert (await client.post("/auth/login", json={"email": "reset@test.com", "password": "password123"})).status_code == 200
    for _ in range(rate_limit.EMAIL_FAILURES - 1):
        await client.post("/auth/login", json={"email": "reset@test.com", "password": "wrong-password"})
    assert (await client.post("/auth/login", json={"email": "reset@test.com", "password": "password123"})).status_code == 200


async def test_address_limit_uses_nginx_forwarded_address(client):
    headers = {"X-Forwarded-For": "1.2.3.4, 10.0.0.9"}
    for index in range(rate_limit.IP_FAILURES):
        await client.post("/auth/login", json={"email": f"u{index}@test.com", "password": "wrong-password"}, headers=headers)
    blocked = await client.post("/auth/login", json={"email": "new@test.com", "password": "wrong-password"}, headers=headers)
    assert blocked.status_code == 429
    other = await client.post("/auth/login", json={"email": "new@test.com", "password": "wrong-password"},
                              headers={"X-Forwarded-For": "1.2.3.4, 10.0.0.10"})
    assert other.status_code == 401


def test_failure_window_expires():
    now = [0.0]
    window = rate_limit.FailureWindow(2, seconds=60, clock=lambda: now[0])
    window.fail("a"); window.fail("a")
    assert window.retry_after("a") == 60
    now[0] = 61
    assert window.retry_after("a") == 0 and "a" not in window.hits


async def test_project_upload_limit(ctx, monkeypatch):
    from app.services import assets
    monkeypatch.setattr(assets, "MAX_PROJECT_ASSETS", 2)
    client, project_id = ctx["client"], ctx["project"]["id"]

    async def upload(name):
        return await client.post("/assets", data={"project_id": project_id, "asset_type": "document"},
                                 files={"file": (name, "견적 메모".encode(), "text/plain")})

    assert (await upload("a.txt")).status_code == 201
    second = await upload("b.txt")
    assert second.status_code == 201
    third = await upload("c.txt")
    assert third.status_code == 409 and third.json()["code"] == "ASSET_LIMIT"
    await client.delete(f"/assets/{second.json()['id']}")
    assert (await upload("d.txt")).status_code == 201


def test_scope_policy_is_in_every_request():
    developer = build_wire([], [], "x", [], "m")["input"][0]["content"]
    assert SCOPE_POLICY in developer
    assert "program code" in SCOPE_POLICY and "Never reveal" in SCOPE_POLICY
