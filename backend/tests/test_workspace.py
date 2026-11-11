"""프로젝트 작업실(UX Flow Atlas v0.3) 기능 테스트."""
import pytest
from bson import ObjectId


@pytest.fixture
async def url(client, login):
    await login(client)
    res = await client.post("/projects", json={"name": "핫팩 파우치"})
    assert res.status_code == 201, res.text
    return f"/projects/{res.json()['id']}"


async def save(client, url, key, kind, content, revision, **extra):
    return await client.put(
        f"{url}/plan/{key}", json={"kind": kind, "content": content, "revision": revision, **extra}
    )


# ---------- 상품기획 6개 항목 ----------


async def test_plan_starts_with_six_unset_items(client, url):
    items = (await client.get(f"{url}/plan")).json()
    assert [item["item_key"] for item in items] == [f"ck_0{n}" for n in range(1, 7)]
    assert {item["state"] for item in items} == {"UNSET"}
    assert {item["revision"] for item in items} == {0}
    assert (await client.get(f"{url}/plan/ck_07")).status_code == 422


async def test_draft_then_decide_then_redraft_keeps_decided(client, url):
    draft = (await save(client, url, "ck_01", "DRAFT", "핫팩 파우치 초안", 0)).json()
    assert (draft["state"], draft["revision"], draft["decided"]) == ("DRAFT", 1, None)
    decided = (await save(client, url, "ck_01", "DECIDED", "핫팩 파우치", 1)).json()
    assert (decided["state"], decided["draft"], decided["decided"]["version"]) == ("DECIDED", None, 1)
    # 확정된 항목을 다시 고치는 중에는 기존 확정본이 그대로 남는다.
    again = (await save(client, url, "ck_01", "DRAFT", "보온 파우치로 변경?", 2)).json()
    assert again["state"] == "DRAFT"
    assert again["content"] == "보온 파우치로 변경?"
    assert again["decided"]["content"] == "핫팩 파우치"
    # 초안을 버리면 확정 상태로 돌아간다.
    res = await client.delete(f"{url}/plan/ck_01/draft", params={"revision": 3})
    back = res.json()
    assert (back["state"], back["content"]) == ("DECIDED", "핫팩 파우치")
    assert (await client.delete(f"{url}/plan/ck_01/draft", params={"revision": 4})).json()["code"] == "NO_DRAFT"


async def test_decide_directly_and_history(client, url):
    await save(client, url, "ck_02", "DECIDED", "20대 직장인", 0)
    await save(client, url, "ck_02", "DECIDED", "30대 직장인", 1)
    versions = (await client.get(f"{url}/plan/ck_02/versions")).json()
    assert [(v["version"], v["content"]) for v in versions] == [(2, "30대 직장인"), (1, "20대 직장인")]


async def test_plan_save_conflicts_and_validation(client, url):
    assert (await save(client, url, "ck_01", "DRAFT", "a", 3)).status_code == 409
    await save(client, url, "ck_01", "DRAFT", "a", 0)
    res = await save(client, url, "ck_01", "DRAFT", "b", 0)
    assert res.status_code == 409 and res.json()["code"] == "REVISION_CONFLICT"
    assert (await save(client, url, "ck_01", "DRAFT", "   ", 1)).status_code == 422
    assert (await save(client, url, "ck_01", "DRAFT", "a", 1, amount=1000)).json()["code"] == "AMOUNT_NOT_ALLOWED"
    priced = (await save(client, url, "ck_05", "DECIDED", "판매가", 0, amount=19000)).json()
    assert priced["amount"] == 19000
    # 내용은 다듬지 않고 그대로 저장한다.
    kept = (await save(client, url, "ck_03", "DRAFT", "  앞뒤 공백\n", 0)).json()
    assert kept["content"] == "  앞뒤 공백\n"


async def test_clear_item_returns_to_unset(client, url):
    await save(client, url, "ck_04", "DECIDED", "구성", 0)
    cleared = (await client.delete(f"{url}/plan/ck_04", params={"revision": 1})).json()
    assert (cleared["state"], cleared["content"], cleared["revision"]) == ("UNSET", None, 2)
    # 지운 뒤 다시 확정해도 이력 번호는 이어진다.
    again = (await save(client, url, "ck_04", "DECIDED", "새 구성", 2)).json()
    assert again["decided"]["version"] == 2


async def test_batch_reports_each_item(client, url):
    await save(client, url, "ck_01", "DRAFT", "이미 있음", 0)
    body = {
        "items": [
            {"item_key": "ck_01", "kind": "DECIDED", "content": "낡은 화면", "revision": 0},
            {"item_key": "ck_02", "kind": "DECIDED", "content": "고객", "revision": 0},
        ]
    }
    results = (await client.post(f"{url}/plan/batch", json=body)).json()
    assert [(r["item_key"], r["status"]) for r in results] == [("ck_01", "REJECTED"), ("ck_02", "APPLIED")]
    assert results[0]["code"] == "REVISION_CONFLICT"
    assert (await client.get(f"{url}/plan/ck_01")).json()["content"] == "이미 있음"
    body["items"].append(dict(body["items"][0]))
    assert (await client.post(f"{url}/plan/batch", json=body)).status_code == 422


async def test_export_md_and_txt_have_same_text(client, url):
    await save(client, url, "ck_05", "DECIDED", "판매가", 0, amount=19000)
    md = await client.get(f"{url}/plan/export", params={"format": "md"})
    txt = await client.get(f"{url}/plan/export", params={"format": "txt"})
    assert md.headers["content-type"] == "text/markdown; charset=utf-8"
    assert txt.headers["content-type"] == "text/plain; charset=utf-8"
    assert md.text == txt.text
    assert "## 가격·원가 (확정)" in md.text and "19,000원" in md.text and "## 상품 선정 (미작성)" in md.text
    assert (await client.get(f"{url}/plan/export", params={"format": "pdf"})).status_code == 422


# ---------- 중복 요청 방지 ----------


async def test_idempotency_key_applies_once(client, url):
    headers = {"Idempotency-Key": "save-1"}
    body = {"kind": "DECIDED", "content": "핫팩", "revision": 0}
    first = await client.put(f"{url}/plan/ck_01", json=body, headers=headers)
    second = await client.put(f"{url}/plan/ck_01", json=body, headers=headers)
    assert first.status_code == second.status_code == 200
    assert second.json()["revision"] == 1
    assert len((await client.get(f"{url}/plan/ck_01/versions")).json()) == 1
    shown = (await client.get("/requests/save-1")).json()
    assert shown["state"] == "APPLIED" and shown["result"]["item_key"] == "ck_01"
    assert (await client.get("/requests/none")).status_code == 404


async def test_rejected_request_is_remembered(client, url):
    headers = {"Idempotency-Key": "save-2"}
    body = {"kind": "DRAFT", "content": "핫팩", "revision": 5}
    for _ in range(2):
        res = await client.put(f"{url}/plan/ck_01", json=body, headers=headers)
        assert res.status_code == 409
    shown = (await client.get("/requests/save-2")).json()
    assert shown["state"] == "NOT_APPLIED" and shown["error"]["code"] == "REVISION_CONFLICT"
    await client.post("/auth/logout")
    await client.post(
        "/auth/signup", json={"email": "b@test.com", "password": "password123", "creator_name": "다른 사람"}
    )
    assert (await client.get("/requests/save-2")).status_code == 404


# ---------- 추가 기록 ----------


async def test_record_entries(client, url):
    assert (await client.get(f"{url}/record")).json()["revision"] == 0
    res = await client.post(f"{url}/record/entries", json={"title": "포장", "content": "친환경 포장", "revision": 0})
    assert res.status_code == 201, res.text
    record = res.json()
    entry = record["entries"][0]
    assert (record["revision"], entry["state"], entry["source"]) == (1, "DRAFT", "USER")
    assert (await client.post(f"{url}/record/entries", json={"title": "x", "content": "y", "revision": 0})).status_code == 409
    entry_url = f"{url}/record/entries/{entry['entry_id']}"
    updated = (await client.patch(entry_url, json={"state": "DECIDED", "revision": 1})).json()
    assert updated["entries"][0]["state"] == "DECIDED" and updated["entries"][0]["title"] == "포장"
    assert (await client.patch(f"{url}/record/entries/{ObjectId()}", json={"title": "x", "revision": 2})).status_code == 404
    emptied = (await client.delete(entry_url, params={"revision": 2})).json()
    assert emptied["entries"] == [] and emptied["revision"] == 3


async def test_similar_record_is_suggested_not_blocked(client, url):
    await client.post(f"{url}/record/entries", json={"title": "포장", "content": "친환경 종이 포장을 쓴다", "revision": 0})
    found = (await client.post(f"{url}/record/similar", json={"content": "친환경 종이 포장을 쓴다."})).json()
    assert len(found) == 1 and found[0]["score"] >= 0.5
    same_title = (await client.post(f"{url}/record/similar", json={"title": " 포장 ", "content": "전혀 다른 이야기"})).json()
    assert same_title[0]["score"] == 1.0
    assert (await client.post(f"{url}/record/similar", json={"content": "배송은 택배로"})).json() == []
    # 비슷해도 저장은 된다.
    res = await client.post(f"{url}/record/entries", json={"title": "포장", "content": "친환경 종이 포장을 쓴다", "revision": 1})
    assert res.status_code == 201


# ---------- 경제성 ----------


async def test_costs_keep_unknown_apart_from_zero(client, url):
    items = [
        {"label": "원단", "category": "material", "amount": 3000},
        {"label": "택배", "category": "shipping", "amount": 0},
        {"label": "포장", "category": "packaging", "amount": None},
        {"label": "수수료", "category": "fee", "unit": "PERCENT", "amount": 10.5},
    ]
    res = await client.put(f"{url}/economics/costs", json={"items": items, "revision": 0})
    assert res.status_code == 200, res.text
    saved = res.json()
    assert [item["amount"] for item in saved["items"]] == [3000, 0, None, 10.5]
    assert (await client.put(f"{url}/economics/costs", json={"items": [], "revision": 0})).status_code == 409
    for bad in ({"label": "x", "amount": 10.5}, {"label": "x", "unit": "PERCENT", "amount": 101}, {"label": "x", "amount": -1}):
        assert (await client.put(f"{url}/economics/costs", json={"items": [bad], "revision": 1})).status_code == 422, bad


async def test_scenario_excludes_unknown_and_unapproved(client, url):
    items = [
        {"label": "원단", "amount": 3000},
        {"label": "포장", "amount": None},
        {"label": "인건비", "amount": 2000, "basis": "ASSUMPTION"},
        {"label": "수수료", "unit": "PERCENT", "amount": 10},
    ]
    await client.put(f"{url}/economics/costs", json={"items": items, "revision": 0})
    res = await client.post(f"{url}/economics/scenarios", json={"price": 10000, "label": "기본"})
    assert res.status_code == 201, res.text
    scenario = res.json()
    assert (scenario["unit_cost"], scenario["fee"], scenario["profit"], scenario["margin_rate"]) == (3000, 1000, 6000, 60.0)
    assert scenario["complete"] is False and scenario["uses_assumptions"] is False
    assert scenario["excluded"] == [
        {"label": "포장", "reason": "UNKNOWN"},
        {"label": "인건비", "reason": "UNAPPROVED_ASSUMPTION"},
    ]
    items[1]["amount"] = 500
    items[2]["approved"] = True
    await client.put(f"{url}/economics/costs", json={"items": items, "revision": 1})
    full = (await client.post(f"{url}/economics/scenarios", json={"price": 10000})).json()
    assert (full["unit_cost"], full["profit"], full["complete"], full["uses_assumptions"]) == (5500, 3500, True, True)
    assert full["sheet_revision"] == 2
    # 비용이 하나도 없으면 완전한 계산이 아니다.
    assert len((await client.get(f"{url}/economics/scenarios")).json()) == 2
    zero = (await client.post(f"{url}/economics/scenarios", json={"price": 0})).json()
    assert zero["margin_rate"] is None


async def test_adopting_scenario_writes_a_draft_price(client, url):
    await save(client, url, "ck_05", "DECIDED", "판매가 9,000원", 0, amount=9000)
    scenario = (await client.post(f"{url}/economics/scenarios", json={"price": 12000, "label": "프리미엄"})).json()
    assert scenario["complete"] is False
    adopt = f"{url}/economics/scenarios/{scenario['id']}/adopt"
    assert (await client.post(adopt, json={"revision": 0})).status_code == 409
    res = await client.post(adopt, json={"revision": 1})
    assert res.status_code == 200, res.text
    item = res.json()["item"]
    # 초안으로만 넘어가고 확정본은 그대로다.
    assert (item["state"], item["amount"], item["decided"]["amount"]) == ("DRAFT", 12000, 9000)
    assert item["content"] == "판매가 12,000원 (프리미엄)"
    assert res.json()["scenario"]["adopted_at"] is not None
    assert (await client.delete(f"{url}/economics/scenarios/{scenario['id']}")).status_code == 204
    assert (await client.get(f"{url}/economics/scenarios")).json() == []


# ---------- 기획 산출물 ----------


async def test_document_lifecycle(client, url):
    res = await client.post(
        f"{url}/documents",
        json={"doc_type": "PLAN", "title": "상품 기획서", "content": "초안", "basis": ["DECIDED", "DRAFT"]},
    )
    assert res.status_code == 201, res.text
    doc = res.json()
    assert (doc["state"], doc["version"], doc["revision"], doc["uses_unconfirmed"]) == ("DRAFT", 0, 1, True)
    doc_url = f"{url}/documents/{doc['id']}"
    final = (await client.post(f"{doc_url}/finalize", json={"revision": 1})).json()
    assert (final["state"], final["version"], final["revision"]) == ("FINAL", 1, 2)
    assert (await client.post(f"{doc_url}/finalize", json={"revision": 1})).status_code == 409
    # 최종본을 고치면 다시 작성 중이 되고, 이전 최종본은 이력에 남는다.
    edited = (await client.patch(doc_url, json={"content": "고친 내용", "revision": 2})).json()
    assert (edited["state"], edited["content"], edited["version"]) == ("DRAFT", "고친 내용", 1)
    versions = (await client.get(f"{doc_url}/versions")).json()
    assert [(v["version"], v["content"]) for v in versions] == [(1, "초안")]
    assert len((await client.get(f"{url}/documents")).json()) == 1
    assert (await client.delete(doc_url)).status_code == 204
    assert (await client.get(doc_url)).status_code == 404
    assert (await client.post(f"{url}/documents", json={"doc_type": "ETC", "title": "x"})).status_code == 422


async def test_document_goes_stale_when_plan_changes(client, url):
    await save(client, url, "ck_01", "DECIDED", "핫팩", 0)
    doc = (await client.post(f"{url}/documents", json={"doc_type": "INTRO", "title": "소개서"})).json()
    assert doc["stale_items"] == [] and doc["uses_unconfirmed"] is False
    doc_url = f"{url}/documents/{doc['id']}"
    # 초안만 바뀐 것은 문서에 영향이 없다.
    await save(client, url, "ck_01", "DRAFT", "보온 파우치", 1)
    assert (await client.get(doc_url)).json()["stale_items"] == []
    await save(client, url, "ck_01", "DECIDED", "보온 파우치", 2)
    await save(client, url, "ck_03", "DECIDED", "따뜻함", 0)
    stale = (await client.get(doc_url)).json()
    assert stale["stale_items"] == ["ck_01", "ck_03"]
    assert stale["content"] == ""
    synced = (await client.post(f"{doc_url}/sync", json={"revision": 1})).json()
    assert synced["stale_items"] == [] and synced["revision"] == 2


# ---------- AI 채팅 ----------


async def test_chat_is_one_per_project(client, url):
    chat = (await client.get(f"{url}/chat")).json()
    assert (chat["search_enabled"], chat["message_count"], chat["revision"]) == (False, 0, 1)
    assert (await client.get(f"{url}/chat")).json()["id"] == chat["id"]
    first = await client.post(f"{url}/chat/messages", json={"content": "상품 아이디어 좀"})
    assert first.status_code == 201, first.text
    second = (await client.post(f"{url}/chat/messages", json={"content": "하나 더"})).json()
    assert (first.json()["ordinal"], second["ordinal"], second["role"]) == (1, 2, "USER")
    messages = (await client.get(f"{url}/chat/messages")).json()
    assert [m["content"] for m in messages] == ["상품 아이디어 좀", "하나 더"]
    after = (await client.get(f"{url}/chat")).json()
    # 메시지를 보내도 설정 revision은 그대로다.
    assert (after["message_count"], after["revision"]) == (2, 1)
    assert (await client.post(f"{url}/chat/messages", json={"content": "x", "role": "ASSISTANT"})).status_code == 422
    assert (await client.post(f"{url}/chat/messages", json={"content": ""})).status_code == 422


async def test_chat_settings_and_references(client, url):
    other = (await client.post("/projects", json={"name": "참고 프로젝트"})).json()
    project_id = url.rsplit("/", 1)[1]
    asset = (
        await client.post(
            "/assets",
            data={"project_id": project_id, "asset_type": "document"},
            files={"file": ("note.txt", "메모".encode(), "text/plain")},
        )
    ).json()
    await client.get(f"{url}/chat")
    res = await client.patch(
        f"{url}/chat",
        json={"search_enabled": True, "file_refs": [asset["id"]], "chip_refs": [other["id"]], "revision": 1},
    )
    assert res.status_code == 200, res.text
    chat = res.json()
    assert (chat["search_enabled"], chat["file_refs"], chat["chip_refs"], chat["revision"]) == (
        True, [asset["id"]], [other["id"]], 2,
    )
    assert (await client.patch(f"{url}/chat", json={"search_enabled": False, "revision": 1})).status_code == 409
    # 자기 프로젝트나 없는 자료는 참고로 걸 수 없다.
    assert (await client.patch(f"{url}/chat", json={"chip_refs": [project_id], "revision": 2})).status_code == 404
    assert (await client.patch(f"{url}/chat", json={"file_refs": [str(ObjectId())], "revision": 2})).status_code == 404
    sent = await client.post(f"{url}/chat/messages", json={"content": "이 파일 봐줘", "asset_ids": [asset["id"]]})
    assert sent.json()["asset_ids"] == [asset["id"]]
    # 파일을 지우면 채팅의 참고 목록에서도 빠진다.
    assert (await client.delete(f"/assets/{asset['id']}")).status_code == 204
    assert (await client.get(f"{url}/chat")).json()["file_refs"] == []


async def test_pipeline_can_store_assistant_message(client, url, db):
    from app.services import chat

    project = await db.projects.find_one({})
    await client.post(f"{url}/chat/messages", json={"content": "질문"})
    await chat.add_message(db, project["user_id"], project["_id"], "ASSISTANT", "답변", completion="PARTIAL")
    messages = (await client.get(f"{url}/chat/messages")).json()
    assert [(m["ordinal"], m["role"], m["completion"]) for m in messages] == [
        (1, "USER", "COMPLETE"), (2, "ASSISTANT", "PARTIAL"),
    ]


# ---------- AI 제안 ----------


async def test_proposal_does_not_touch_plan_until_applied(client, url):
    await save(client, url, "ck_02", "DECIDED", "20대 직장인", 0)
    message = (await client.post(f"{url}/chat/messages", json={"content": "고객 추천해줘"})).json()
    res = await client.post(
        f"{url}/proposals", json={"target": "ck_02", "content": "30대 워킹맘", "message_id": message["id"]}
    )
    assert res.status_code == 201, res.text
    proposal = res.json()
    assert (proposal["state"], proposal["base_revision"], proposal["target_changed"]) == ("PENDING", 1, False)
    assert (await client.get(f"{url}/plan/ck_02")).json()["content"] == "20대 직장인"
    held = (await client.post(f"{url}/proposals/{proposal['id']}/hold")).json()
    assert held["state"] == "HELD"
    # 제안을 받은 뒤 사용자가 직접 항목을 고친 경우
    await save(client, url, "ck_02", "DRAFT", "직접 고침", 1)
    shown = (await client.get(f"{url}/proposals/{proposal['id']}")).json()
    assert (shown["current_revision"], shown["target_changed"]) == (2, True)
    apply_url = f"{url}/proposals/{proposal['id']}/apply"
    stale = await client.post(apply_url, json={"content": "30대 워킹맘", "revision": 1})
    assert stale.status_code == 409 and stale.json()["code"] == "REVISION_CONFLICT"
    assert (await client.get(f"{url}/proposals/{proposal['id']}")).json()["state"] == "HELD"
    applied = await client.post(apply_url, json={"content": "30대 워킹맘 (수정)", "kind": "DECIDED", "revision": 2})
    assert applied.status_code == 200, applied.text
    assert (applied.json()["state"], applied.json()["applied_content"]) == ("APPLIED", "30대 워킹맘 (수정)")
    item = (await client.get(f"{url}/plan/ck_02")).json()
    assert (item["state"], item["content"], item["decided"]["source"]) == ("DECIDED", "30대 워킹맘 (수정)", "AI")
    again = await client.post(apply_url, json={"content": "x", "revision": 3})
    assert again.status_code == 409 and again.json()["code"] == "PROPOSAL_CLOSED"
    assert (await client.post(f"{url}/proposals/{proposal['id']}/reject")).status_code == 409


async def test_reject_and_record_proposals(client, url):
    rejected = (await client.post(f"{url}/proposals", json={"target": "ck_01", "content": "양말"})).json()
    assert (await client.post(f"{url}/proposals/{rejected['id']}/reject")).json()["state"] == "REJECTED"
    assert (await client.get(f"{url}/plan/ck_01")).json()["state"] == "UNSET"
    note = (await client.post(f"{url}/proposals", json={"target": "record", "content": "겨울 한정 판매"})).json()
    apply_url = f"{url}/proposals/{note['id']}/apply"
    missing = await client.post(apply_url, json={"content": "겨울 한정 판매", "revision": 0})
    assert missing.status_code == 422 and missing.json()["code"] == "TITLE_REQUIRED"
    res = await client.post(apply_url, json={"content": "겨울 한정 판매", "title": "판매 시기", "revision": 0})
    assert res.status_code == 200, res.text
    entry = (await client.get(f"{url}/record")).json()["entries"][0]
    assert (entry["title"], entry["source"], entry["source_ref"]) == ("판매 시기", "AI", note["id"])
    pending = (await client.get(f"{url}/proposals", params={"state": "PENDING"})).json()
    assert pending == []
    assert len((await client.get(f"{url}/proposals")).json()) == 2
    assert (await client.post(f"{url}/proposals", json={"target": "ck_09", "content": "x"})).status_code == 422
    bad = await client.post(f"{url}/proposals", json={"target": "ck_01", "content": "x", "message_id": str(ObjectId())})
    assert bad.status_code == 404


# ---------- 개요 · 맥락 ----------


async def test_overview_collects_project_state(client, url):
    brand = (await client.post("/brands", json={"brand_type": "new", "name": "따숨"})).json()
    await client.patch(url, json={"brand_id": brand["id"], "revision": 1})
    await save(client, url, "ck_01", "DECIDED", "핫팩", 0)
    await save(client, url, "ck_02", "DRAFT", "고객", 0)
    await client.post(f"{url}/documents", json={"doc_type": "PLAN", "title": "기획서"})
    await save(client, url, "ck_01", "DECIDED", "보온 파우치", 1)
    await client.post(f"{url}/proposals", json={"target": "ck_03", "content": "따뜻함"})
    await client.put(f"{url}/economics/costs", json={"items": [{"label": "포장"}], "revision": 0})
    await client.post(f"{url}/record/entries", json={"title": "메모", "content": "내용", "revision": 0})
    await client.post(f"{url}/chat/messages", json={"content": "안녕"})
    res = await client.get(f"{url}/overview")
    assert res.status_code == 200, res.text
    overview = res.json()
    assert overview["brand"]["name"] == "따숨"
    assert [item["state"] for item in overview["plan"]][:3] == ["DECIDED", "DRAFT", "UNSET"]
    assert overview["proposals"] == {"pending": 1, "held": 0}
    assert overview["documents"]["count"] == 1
    assert overview["documents"]["stale"][0]["stale_items"] == ["ck_01"]
    assert overview["economics"] == {"cost_items": 1, "unknown_costs": 1, "scenarios": 0, "adopted_price": None}
    assert (overview["record_entries"], overview["chat_messages"], overview["active_jobs"]) == (1, 1, 0)


async def test_projection_sections_and_bundle_revision(client, url):
    full = (await client.get(f"{url}/projection")).json()
    assert list(full["sections"]) == ["project_basic", "linked_brand", *[f"ck_0{n}" for n in range(1, 7)]]
    assert full["sections"]["linked_brand"]["brand"] is None
    part = (await client.get(f"{url}/projection", params={"sections": "project_basic,ck_01"})).json()
    assert list(part["sections"]) == ["project_basic", "ck_01"]
    same = (await client.get(f"{url}/projection", params={"sections": "project_basic,ck_01"})).json()
    assert same["bundle_revision"] == part["bundle_revision"]
    # 포함하지 않은 섹션이 바뀌어도 묶음 버전은 그대로다.
    await save(client, url, "ck_02", "DRAFT", "고객", 0)
    assert (await client.get(f"{url}/projection", params={"sections": "project_basic,ck_01"})).json()[
        "bundle_revision"
    ] == part["bundle_revision"]
    await save(client, url, "ck_01", "DECIDED", "핫팩", 0)
    changed = (await client.get(f"{url}/projection", params={"sections": "project_basic,ck_01"})).json()
    assert changed["bundle_revision"] != part["bundle_revision"]
    assert changed["sections"]["ck_01"]["decided"] == {"content": "핫팩", "amount": None}
    assert (await client.get(f"{url}/projection", params={"sections": "ck_99"})).status_code == 422
