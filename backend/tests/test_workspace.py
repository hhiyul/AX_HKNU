"""프로젝트 작업실 기능 테스트 (프로토타입 기준)."""
import pytest
from bson import ObjectId

from app.services import chat

COSTS = {"material": 8000, "labor": 12000, "packaging": 2000, "shipping": 0, "other": 0, "fee_rate": 10}


@pytest.fixture
async def url(client, login):
    await login(client)
    res = await client.post("/projects", json={"name": "칠보 팔찌"})
    assert res.status_code == 201, res.text
    return f"/projects/{res.json()['id']}"


async def save(client, url, key, content, revision, kind="plan", **extra):
    return await client.put(f"{url}/items/{kind}/{key}", json={"content": content, "revision": revision, **extra})


async def create(client, url, kind, **body):
    res = await client.post(f"{url}/items/{kind}", json=body)
    assert res.status_code == 201, res.text
    return res.json()


async def assistant_says(db, url, content, doc=None):
    project = await db.projects.find_one({"_id": ObjectId(url.rsplit("/", 1)[-1])})
    message = await chat.add_message(db, project["user_id"], project["_id"], "ASSISTANT", content, doc=doc)
    return str(message["_id"])


async def recent(client, url):
    return [entry["text"] for entry in (await client.get(url)).json()["recent"]]


# ---------- 상품기획 6개 항목 ----------


async def test_plan_starts_with_six_unset_items(client, url):
    items = (await client.get(f"{url}/items/plan")).json()
    assert [item["key"] for item in items] == [f"ck_0{n}" for n in range(1, 7)]
    assert items[0]["title"] == "상품선정"
    assert {item["state"] for item in items} == {"UNSET"}
    assert {item["revision"] for item in items} == {0}
    assert (await client.get(f"{url}/items/plan/ck_07")).status_code == 422
    assert (await client.get(f"{url}/items/memo")).status_code == 422
    # 기획 항목은 6개로 고정이라 새로 만들거나 지울 수 없다.
    assert (await client.post(f"{url}/items/plan", json={"title": "x", "content": "y"})).status_code == 422
    assert (await client.delete(f"{url}/items/plan/ck_01", params={"revision": 0})).status_code == 422


async def test_plan_save_applies_at_once(client, url):
    saved = (await save(client, url, "ck_01", "칠보 팔찌", 0)).json()
    assert (saved["state"], saved["content"], saved["revision"]) == ("WRITTEN", "칠보 팔찌", 1)
    assert (await client.get(url)).json()["plan_filled"] == 1
    assert (await recent(client, url))[0] == "상품선정 수정"
    # 비워서 저장하면 미작성으로 돌아간다. skipped를 보내면 건너뜀으로 남는다.
    cleared = (await save(client, url, "ck_01", "  ", 1)).json()
    assert (cleared["state"], cleared["content"]) == ("UNSET", None)
    skipped = (await save(client, url, "ck_01", "", 2, skipped=True)).json()
    assert skipped["state"] == "SKIPPED"
    assert (await client.get(url)).json()["plan_filled"] == 0


async def test_plan_save_conflicts_and_validation(client, url):
    await save(client, url, "ck_01", "처음", 0)
    stale = await save(client, url, "ck_01", "예전 탭", 0)
    assert stale.status_code == 409 and stale.json()["code"] == "REVISION_CONFLICT"
    assert (await save(client, url, "ck_01", "x" * 5001, 1)).status_code == 422
    assert (await client.put(f"{url}/items/plan/ck_02", json={"revision": 0})).status_code == 422
    res = await save(client, url, "ck_01", "가격", 1, amount=1000)
    assert res.status_code == 422 and res.json()["code"] == "AMOUNT_NOT_ALLOWED"
    assert (await save(client, url, "ck_01", "x", 1, state="DRAFT")).status_code == 422


async def test_price_amount_is_kept_until_cleared(client, url):
    priced = (await save(client, url, "ck_05", "35,000원", 0, amount=35000)).json()
    assert priced["amount"] == 35000
    # 글만 고치면 금액은 그대로다.
    assert (await save(client, url, "ck_05", "35,000원 · 검토 중", 1)).json()["amount"] == 35000
    assert (await save(client, url, "ck_05", "", 2, amount=1000)).status_code == 422
    assert (await save(client, url, "ck_05", "", 2)).json()["amount"] is None


async def test_wizard_answers_create_plan_with_skips(client, login):
    await login(client)
    plan = [
        {"key": "ck_01", "content": "칠보 팔찌"},
        {"key": "ck_03", "content": "   "},
        {"key": "ck_05", "content": "35,000원", "amount": 35000},
    ]
    res = await client.post("/projects", json={"name": "칠보 팔찌 상품화", "plan": plan})
    assert res.status_code == 201, res.text
    project = res.json()
    assert project["plan_filled"] == 2
    items = (await client.get(f"/projects/{project['id']}/items/plan")).json()
    assert [item["state"] for item in items] == ["WRITTEN", "SKIPPED", "SKIPPED", "SKIPPED", "WRITTEN", "SKIPPED"]
    assert items[4]["amount"] == 35000 and items[0]["revision"] == 1
    # 건너뛴 항목은 작업실에서 채울 수 있다.
    filled = await client.put(f"/projects/{project['id']}/items/plan/ck_02", json={"content": "선물 고객", "revision": 1})
    assert filled.json()["state"] == "WRITTEN"

    twice = [{"key": "ck_01", "content": "a"}, {"key": "ck_01", "content": "b"}]
    assert (await client.post("/projects", json={"name": "x", "plan": twice})).status_code == 422
    priced = [{"key": "ck_01", "content": "a", "amount": 1000}]
    assert (await client.post("/projects", json={"name": "x", "plan": priced})).status_code == 422
    assert len((await client.get("/projects")).json()) == 1


async def test_idempotency_key_applies_once(client, url):
    headers = {"Idempotency-Key": "save-1"}
    body = {"content": "칠보 팔찌", "revision": 0}
    first = await client.put(f"{url}/items/plan/ck_01", json=body, headers=headers)
    again = await client.put(f"{url}/items/plan/ck_01", json=body, headers=headers)
    assert first.status_code == again.status_code == 200
    assert again.json()["revision"] == 1
    result = (await client.get("/requests/save-1")).json()
    assert result["state"] == "APPLIED"
    assert (await client.get("/requests/unknown")).status_code == 404


async def test_rejected_request_is_remembered(client, url):
    headers = {"Idempotency-Key": "save-2"}
    body = {"content": "늦은 저장", "revision": 5}
    first = await client.put(f"{url}/items/plan/ck_01", json=body, headers=headers)
    again = await client.put(f"{url}/items/plan/ck_01", json=body, headers=headers)
    assert first.status_code == again.status_code == 409
    assert (await client.get("/requests/save-2")).json()["state"] == "NOT_APPLIED"


# ---------- AI 채팅과 답변 저장 ----------


async def test_messages_are_numbered_per_project(client, url):
    first = await client.post(f"{url}/messages", json={"content": "안녕"})
    assert first.status_code == 201, first.text
    second = (await client.post(f"{url}/messages", json={"content": "가격을 정해 줘"})).json()
    assert (first.json()["ordinal"], second["ordinal"]) == (1, 2)
    assert first.json()["role"] == "USER"
    assert (first.json()["doc"], first.json()["record_key"], first.json()["doc_key"]) == (None, None, None)
    assert [m["content"] for m in (await client.get(f"{url}/messages")).json()] == ["안녕", "가격을 정해 줘"]
    assert (await client.post(f"{url}/messages", json={"content": ""})).status_code == 422
    assert (await client.post(f"{url}/messages", json={"content": "x", "role": "ASSISTANT"})).status_code == 422
    project = (await client.get(url)).json()
    assert project["message_count"] == 2 and project["revision"] == 1
    assert project["recent"][0]["text"] == "AI 채팅 · 대화 추가"


async def test_chat_settings_live_on_the_project(client, url):
    other = (await client.post("/projects", json={"name": "참고 프로젝트"})).json()
    res = await client.patch(url, json={"search_enabled": True, "chip_refs": [other["id"]], "revision": 1})
    assert res.status_code == 200, res.text
    assert res.json()["search_enabled"] is True and res.json()["chip_refs"] == [other["id"]]
    own_id = url.rsplit("/", 1)[-1]
    assert (await client.patch(url, json={"chip_refs": [own_id], "revision": 2})).status_code == 404
    assert (await client.patch(url, json={"file_refs": [str(ObjectId())], "revision": 2})).status_code == 404


async def test_review_answer_saves_a_record(client, url, db):
    message_id = await assistant_says(db, url, "고객은 30대 직장인입니다.")
    record = await create(
        client, url, "record", title="고객 정리", content="고객은 30대 직장인", message_id=message_id
    )
    assert (record["title"], record["content"], record["message_id"]) == ("고객 정리", "고객은 30대 직장인", message_id)
    message = (await client.get(f"{url}/messages")).json()[0]
    assert message["record_key"] == record["key"]
    assert (await recent(client, url))[0] == "AI 답변 검수·확정 → 상품기획에 저장"
    # 같은 답변을 다시 검수하면 새로 만들지 않고 고친다.
    again = await create(client, url, "record", title="고객", content="고친 내용", message_id=message_id)
    assert again["key"] == record["key"] and again["content"] == "고친 내용"
    assert len((await client.get(f"{url}/items/record")).json()) == 1

    edited = await save(client, url, record["key"], "직접 고침", again["revision"], kind="record")
    assert edited.json()["content"] == "직접 고침" and edited.json()["title"] == "고객"
    assert (await save(client, url, record["key"], " ", edited.json()["revision"], kind="record")).status_code == 422
    # 지우면 답변의 저장 표시도 풀린다.
    gone = await client.delete(f"{url}/items/record/{record['key']}", params={"revision": edited.json()["revision"]})
    assert gone.status_code == 204
    assert (await client.get(f"{url}/messages")).json()[0]["record_key"] is None


async def test_record_needs_title_content_and_my_message(client, url, db):
    assert (await client.post(f"{url}/items/record", json={"content": "내용"})).status_code == 422
    assert (await client.post(f"{url}/items/record", json={"title": "제목"})).status_code == 422
    missing = {"title": "제목", "content": "내용", "message_id": str(ObjectId())}
    assert (await client.post(f"{url}/items/record", json=missing)).status_code == 404
    # 사용자 메시지는 검수 대상이 아니다.
    mine = (await client.post(f"{url}/messages", json={"content": "질문"})).json()
    body = {"title": "제목", "content": "내용", "message_id": mine["id"]}
    assert (await client.post(f"{url}/items/record", json=body)).status_code == 404


# ---------- 산출물 ----------


async def test_save_document_from_answer(client, url, db):
    doc = {"title": "칠보 팔찌 상품기획서", "content": "1. 상품선정\n칠보 팔찌"}
    message_id = await assistant_says(db, url, "문서 초안으로 정리했어요.", doc=doc)
    assert (await client.get(f"{url}/messages")).json()[0]["doc"] == doc

    saved = await create(client, url, "document", message_id=message_id)
    assert (saved["title"], saved["content"], saved["draft"]) == (doc["title"], doc["content"], None)
    assert (await client.get(f"{url}/messages")).json()[0]["doc_key"] == saved["key"]
    assert (await recent(client, url))[0] == f"산출물 저장 · {doc['title']}"
    # 이미 저장한 답변이면 같은 문서를 돌려준다.
    assert (await create(client, url, "document", message_id=message_id))["key"] == saved["key"]
    assert len((await client.get(f"{url}/items/document")).json()) == 1

    gone = await client.delete(f"{url}/items/document/{saved['key']}", params={"revision": saved["revision"]})
    assert gone.status_code == 204
    assert (await client.get(f"{url}/messages")).json()[0]["doc_key"] is None
    assert (await client.get(f"{url}/items/document/{saved['key']}")).status_code == 404


async def test_document_editor_draft_and_rename(client, url):
    document = await create(client, url, "document", title="기획서", content="본문")
    path = f"{url}/items/document/{document['key']}"
    # 편집 중인 내용은 임시 저장으로만 남고 저장된 문서는 그대로다.
    drafted = (await client.put(path, json={"title": "새 제목", "content": "고치는 중", "as_draft": True, "revision": 1})).json()
    assert (drafted["title"], drafted["content"]) == ("기획서", "본문")
    assert (drafted["draft"]["title"], drafted["draft"]["content"]) == ("새 제목", "고치는 중")
    reverted = await client.delete(f"{path}/draft", params={"revision": drafted["revision"]})
    assert reverted.status_code == 200 and reverted.json()["draft"] is None
    assert (await client.delete(f"{path}/draft", params={"revision": 3})).json()["code"] == "NO_DRAFT"

    await client.put(path, json={"content": "고치는 중", "as_draft": True, "revision": 3})
    saved = (await client.put(path, json={"title": "새 제목", "content": "완성", "revision": 4})).json()
    assert (saved["title"], saved["content"], saved["draft"]) == ("새 제목", "완성", None)
    # 이름만 바꿀 수도 있다.
    renamed = (await client.put(path, json={"title": "최종본", "revision": 5})).json()
    assert (renamed["title"], renamed["content"]) == ("최종본", "완성")
    assert (await client.put(path, json={"title": " ", "revision": 6})).status_code == 422
    assert (await client.put(path, json={"content": "", "revision": 6})).status_code == 422
    # 임시 저장은 산출물에만 있다.
    res = await client.put(f"{url}/items/plan/ck_01", json={"content": "x", "as_draft": True, "revision": 0})
    assert res.status_code == 422


# ---------- 경제성 ----------


async def test_economics_keeps_unknown_apart_from_zero(client, url):
    empty = (await client.get(f"{url}/economics")).json()
    assert empty["revision"] == 0 and empty["cost"]["material"] is None
    assert (empty["before"], empty["keep"], empty["change"], empty["candidates"]) == (None, None, None, [])

    partial = {**COSTS, "other": None, "price": 35000}
    saved = await client.put(f"{url}/economics", json={"cost": partial, "revision": 0})
    assert saved.status_code == 200, saved.text
    # 모르는 비용이 있으면 0원으로 치지 않고 계산하지 않는다.
    assert saved.json()["change"] is None
    res = await client.post(f"{url}/economics/baseline", json={"revision": 1})
    assert res.status_code == 422 and res.json()["code"] == "COSTS_INCOMPLETE"

    assert (await client.put(f"{url}/economics", json={"cost": {**COSTS, "price": 35000}, "revision": 0})).status_code == 409
    for bad in ({"material": -1}, {"fee_rate": 101}, {"material": 10.5}, {"tax": 1}):
        res = await client.put(f"{url}/economics", json={"cost": {**COSTS, **bad}, "revision": 1})
        assert res.status_code == 422, bad


async def test_economics_compares_before_keep_and_change(client, url):
    await client.put(f"{url}/economics", json={"cost": {**COSTS, "price": 35000}, "revision": 0})
    based = await client.post(f"{url}/economics/baseline", json={"revision": 1})
    assert based.status_code == 200, based.text
    assert based.json()["before"] == {"price": 35000, "cost": 22000, "fee": 3500, "net": 9500, "delta": None}

    # 재료비가 오르고 판매가 후보를 40,000원으로 넣어 본다.
    changed = {**COSTS, "material": 12000, "price": 40000}
    result = (await client.put(f"{url}/economics", json={"cost": changed, "revision": 2})).json()
    assert result["base"]["material"] == 8000
    assert result["before"]["net"] == 9500
    assert result["keep"] == {"price": 35000, "cost": 26000, "fee": 3500, "net": 5500, "delta": -4000}
    assert result["change"] == {"price": 40000, "cost": 26000, "fee": 4000, "net": 10000, "delta": 500}
    assert [row["price"] for row in result["candidates"]] == [35000, 40000, 45000]
    # 후보를 넣어도 확정 가격은 바뀌지 않는다.
    assert result["confirmed_price"] is None


async def test_confirming_price_updates_the_plan(client, url):
    await client.put(f"{url}/economics", json={"cost": {**COSTS, "price": 35000}, "revision": 0})
    await client.post(f"{url}/economics/baseline", json={"revision": 1})
    await client.put(f"{url}/economics", json={"cost": {**COSTS, "material": 12000, "price": 40000}, "revision": 2})

    assert (await client.post(f"{url}/economics/confirm", json={"mode": "change", "revision": 1})).status_code == 409
    confirmed = await client.post(f"{url}/economics/confirm", json={"mode": "change", "revision": 3})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed_price"] == 40000
    price_item = (await client.get(f"{url}/items/plan/ck_05")).json()
    assert price_item["amount"] == 40000
    assert price_item["content"] == "40,000원 · 경제성 검토 후 직접 결정"
    assert (await recent(client, url))[0] == "판매가 40,000원 변경"

    kept = (await client.post(f"{url}/economics/confirm", json={"mode": "keep", "revision": 4})).json()
    assert kept["confirmed_price"] == 35000 and kept["cost"]["price"] == 35000
    # 비교 기준은 그대로 남는다.
    assert kept["base"]["material"] == 8000
    assert (await client.post(f"{url}/economics/confirm", json={"mode": "other", "revision": 5})).status_code == 422


async def test_keep_uses_confirmed_price_without_baseline(client, url):
    await save(client, url, "ck_05", "30,000원", 0, amount=30000)
    result = (await client.put(f"{url}/economics", json={"cost": COSTS, "revision": 0})).json()
    assert result["keep"]["price"] == 30000 and result["keep"]["delta"] is None
    assert result["change"] is None
    res = await client.post(f"{url}/economics/confirm", json={"mode": "change", "revision": 1})
    assert res.status_code == 422 and res.json()["code"] == "COSTS_INCOMPLETE"


# ---------- 개요 · 맥락 ----------


async def test_overview_collects_project_state(client, url, db):
    await save(client, url, "ck_01", "칠보 팔찌", 0)
    await save(client, url, "ck_05", "35,000원", 0, amount=35000)
    message_id = await assistant_says(db, url, "정리했어요.")
    await create(client, url, "record", title="정리", content="내용", message_id=message_id)
    await create(client, url, "document", title="기획서", content="본문")
    await client.patch(url, json={"note": "체인 단가 확인", "revision": 1})

    overview = (await client.get(f"{url}/overview")).json()
    assert overview["project"]["note"] == "체인 단가 확인"
    assert overview["project"]["plan_filled"] == 2
    assert overview["project"]["next_action"] is None
    assert overview["project"]["recent"][0]["text"] == "나만의 메모 수정"
    assert overview["brand"] is None
    assert [item["state"] for item in overview["plan"]] == ["WRITTEN", "UNSET", "UNSET", "UNSET", "WRITTEN", "UNSET"]
    assert overview["counts"] == {"records": 1, "documents": 1, "messages": 1, "assets": 0}
    assert overview["economics"] == {"confirmed_price": 35000, "has_baseline": False}
    assert overview["active_jobs"] == 0


async def test_recent_keeps_latest_twenty(client, url):
    for revision in range(25):
        await save(client, url, "ck_01", f"내용 {revision}", revision)
    entries = (await client.get(url)).json()["recent"]
    assert len(entries) == 20
    assert entries[0]["at"] >= entries[-1]["at"]


async def test_projection_sections_and_bundle_revision(client, url):
    full = (await client.get(f"{url}/projection")).json()
    assert list(full["sections"]) == ["project_basic", "linked_brand", *[f"ck_0{n}" for n in range(1, 7)]]
    assert full["sections"]["ck_01"] == {
        "revision": 0, "label": "상품선정", "state": "UNSET", "content": None, "amount": None,
    }
    part = (await client.get(f"{url}/projection", params={"sections": "project_basic,ck_01"})).json()
    assert list(part["sections"]) == ["project_basic", "ck_01"]
    await save(client, url, "ck_01", "칠보 팔찌", 0)
    after = (await client.get(f"{url}/projection", params={"sections": "project_basic,ck_01"})).json()
    assert after["bundle_revision"] != part["bundle_revision"]
    assert after["sections"]["ck_01"]["content"] == "칠보 팔찌"
    assert (await client.get(f"{url}/projection", params={"sections": "nope"})).status_code == 422
