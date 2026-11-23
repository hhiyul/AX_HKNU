"""v05 항목별 AI 도움: 고른 참고만 보내고, 후보는 본문과 따로, 적용은 임시 입력에만."""
import asyncio
import json

import pytest

from ai_fakes import COMPLETED, FakeProvider, answer, tool_call
from app.ai.events import RunEvents
from app.ai.responses import Delivery, ProviderError
from app.ai.runtime import Runtime, set_runtime


@pytest.fixture
def use_runtime():
    def install(provider) -> Runtime:
        runtime = Runtime(provider, RunEvents(), {}, "gpt-6-luna")
        set_runtime(runtime)
        return runtime

    yield install
    set_runtime(None)


@pytest.fixture
async def doc(client, login):
    await login(client)
    project = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    url = f"/projects/{project['id']}"
    created = (await client.post(f"{url}/docs", json={"template_id": "funding"})).json()
    base = f"{url}/docs/{created['id']}"
    await client.put(f"{base}/sections/F05", json={"text": "1구성 35,000원", "revision": 0})
    return {"client": client, "url": url, "base": base, "id": created["id"], "project_id": project["id"]}


async def assist(doc, runtime, section="F05", **body):
    payload = {"action": "refine", "request": "", "references": [], **body}
    res = await doc["client"].post(f"{doc['base']}/sections/{section}/assist", json=payload)
    assert res.status_code == 202, res.text
    run_id = res.json()["run_id"]
    await asyncio.wait_for(runtime.tasks[run_id], 5)
    return run_id


async def section_of(doc, section_id):
    full = (await doc["client"].get(doc["base"])).json()
    return next(s for s in full["sections"] if s["section_id"] == section_id)


def sent(provider) -> str:
    return json.dumps(provider.calls[0]["input"], ensure_ascii=False)


async def test_assist_makes_candidate_without_touching_section(doc, use_runtime, db):
    runtime = use_runtime(FakeProvider([answer("1구성 35,000원 · 선물 포장 포함(확인 필요)")]))
    run_id = await assist(doc, runtime, request="선물용 느낌으로")
    messages = (await doc["client"].get(f"{doc['url']}/messages")).json()
    question, reply = messages[-2], messages[-1]
    assert (question["purpose"], question["target_section_id"], question["request_id"]) == ("document_assist", "F05", run_id)
    candidate = reply["candidate"]
    assert (candidate["doc_id"], candidate["section_id"], candidate["base_revision"]) == (doc["id"], "F05", 1)
    assert candidate["text"] == "1구성 35,000원 · 선물 포장 포함(확인 필요)"
    section = await section_of(doc, "F05")
    assert (section["text"], section["state"], section["has_draft"]) == ("1구성 35,000원", "WRITING", False)
    kinds = [e["kind"] async for e in runtime.events.subscribe(run_id)]
    assert "assist_candidate" in kinds and kinds[-1] == "completed"


async def test_only_selected_references_are_sent(doc, use_runtime):
    client, url = doc["client"], doc["url"]
    await client.put(f"{url}/items/plan/ck_01", json={"content": "비밀 기획 메모", "revision": 1})
    await client.put(f"{url}/items/plan/ck_05", json={"content": "확정가 35,000원", "amount": 35000, "revision": 1})
    await client.put(f"{doc['base']}/sections/F01", json={"text": "칠보 팔찌 소개글", "revision": 0})
    await client.post(f"{url}/messages", json={"content": "예전 자유 대화"})
    provider = FakeProvider([answer("후보")])
    references = [
        {"type": "plan", "id": "ck_05"},
        {"type": "section", "id": "F01"},
        {"type": "note", "text": "견적서 발췌: 포장 500원"},
    ]
    await assist(doc, use_runtime(provider), references=references)
    text = sent(provider)
    assert "리워드·가격·수량" in text and "1구성 35,000원" in text
    assert "확정가 35,000원" in text and "칠보 팔찌 소개글" in text and "견적서 발췌: 포장 500원" in text
    assert "비밀 기획 메모" not in text and "예전 자유 대화" not in text
    assert "tools" not in provider.calls[0]
    assert "확인 필요" in text


@pytest.mark.parametrize("reference,status", [
    ({"type": "plan", "id": "ck_09"}, 422),
    ({"type": "section", "id": "Z01"}, 404),
    ({"type": "record", "id": "nope"}, 404),
    ({"type": "message", "id": "0" * 24}, 404),
    ({"type": "note"}, 422),
    ({"type": "brand"}, 422),
    ({"type": "email", "id": "x"}, 422),
])
async def test_reference_validation(doc, use_runtime, reference, status):
    use_runtime(FakeProvider([answer("x")]))
    res = await doc["client"].post(f"{doc['base']}/sections/F05/assist", json={"action": "draft", "references": [reference]})
    assert res.status_code == status, res.text
    project = (await doc["client"].get(doc["url"])).json()
    assert project.get("active_run") is None


async def test_apply_candidate_goes_to_draft_only(doc, use_runtime):
    runtime = use_runtime(FakeProvider([answer("다듬은 글")]))
    await assist(doc, runtime)
    reply = (await doc["client"].get(f"{doc['url']}/messages")).json()[-1]
    before = await section_of(doc, "F05")
    res = await doc["client"].post(
        f"{doc['base']}/sections/F05/apply", json={"message_id": reply["id"], "draft_revision": before["draft_revision"]}
    )
    assert res.status_code == 200, res.text
    applied = res.json()
    assert (applied["draft"], applied["has_draft"], applied["text"], applied["revision"], applied["state"]) == (
        "다듬은 글", True, "1구성 35,000원", 1, "WRITING"
    )


async def test_outdated_candidate_needs_confirmation(doc, use_runtime):
    await assist(doc, use_runtime(FakeProvider([answer("후보")])))
    reply = (await doc["client"].get(f"{doc['url']}/messages")).json()[-1]
    await doc["client"].put(f"{doc['base']}/sections/F05", json={"text": "사용자가 바꾼 글", "revision": 1})
    current = await section_of(doc, "F05")
    body = {"message_id": reply["id"], "draft_revision": current["draft_revision"]}
    res = await doc["client"].post(f"{doc['base']}/sections/F05/apply", json=body)
    assert res.status_code == 409 and res.json()["code"] == "CANDIDATE_OUTDATED"
    res = await doc["client"].post(f"{doc['base']}/sections/F05/apply", json={**body, "confirm": True})
    assert res.status_code == 200 and res.json()["draft"] == "후보" and res.json()["text"] == "사용자가 바꾼 글"


async def test_candidate_cannot_be_applied_elsewhere(doc, use_runtime):
    await assist(doc, use_runtime(FakeProvider([answer("후보")])))
    messages = (await doc["client"].get(f"{doc['url']}/messages")).json()
    reply, question = messages[-1], messages[-2]
    res = await doc["client"].post(f"{doc['base']}/sections/F06/apply", json={"message_id": reply["id"], "draft_revision": 0})
    assert res.status_code == 422 and res.json()["code"] == "CANDIDATE_MISMATCH"
    res = await doc["client"].post(f"{doc['base']}/sections/F05/apply", json={"message_id": question["id"], "draft_revision": 0})
    assert res.status_code == 422 and res.json()["code"] == "NOT_A_CANDIDATE"


async def test_failed_assist_leaves_no_candidate(doc, use_runtime):
    runtime = use_runtime(FakeProvider([], error=ProviderError("PROVIDER_ERROR", Delivery.RESPONDED)))
    run_id = await assist(doc, runtime)
    run = (await doc["client"].get(f"{doc['url']}/runs/{run_id}")).json()
    assert run["status"] == "failed"
    assert all(not m.get("candidate") for m in (await doc["client"].get(f"{doc['url']}/messages")).json())
    assert (await section_of(doc, "F05"))["text"] == "1구성 35,000원"


async def test_tool_calls_are_not_allowed_in_assist(doc, use_runtime):
    runtime = use_runtime(FakeProvider([[tool_call("read_project_sections", {"keys": ["ck_01"]}), COMPLETED]]))
    run_id = await assist(doc, runtime)
    assert (await doc["client"].get(f"{doc['url']}/runs/{run_id}")).json()["error_code"] == "TOOL_NOT_ALLOWED"


async def test_section_history_and_message_links(doc, use_runtime):
    await assist(doc, use_runtime(FakeProvider([answer("후보")])))
    history = (await doc["client"].get(f"{doc['base']}/sections/F05/messages")).json()
    assert [m["role"] for m in history] == ["USER", "ASSISTANT"]
    assert all(m["target_doc_id"] == doc["id"] for m in history)
    assert (await doc["client"].get(f"{doc['base']}/sections/F06/messages")).json() == []


async def test_assist_refused_for_trashed_doc(doc, use_runtime):
    use_runtime(FakeProvider([answer("x")]))
    await doc["client"].delete(doc["base"])
    res = await doc["client"].post(f"{doc['base']}/sections/F05/assist", json={"action": "draft"})
    assert res.status_code == 409 and res.json()["code"] == "DOC_IN_TRASH"
