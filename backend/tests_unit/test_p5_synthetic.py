import asyncio
import json
import sqlite3

import httpx
import pytest

from app.ai.p5.provider import DemoProvider
from app.ai.p5.server import create_app, decode_attachment, is_document_request
from app.ai.p5.store import Store
from app.ai.p5.usage import SearchResult, SummaryText
from app.ai.responses import Delivery, ProviderError


@pytest.fixture
async def env(tmp_path):
    db = Store(str(tmp_path / "synthetic.sqlite"))
    app = create_app(db, DemoProvider())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield app, db, client
    for task in list(app.state.tasks.values()):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    db.close()


async def start_and_wait(app, client, sid, body):
    r = await client.post(f"/p5/projects/demo/sessions/{sid}/runs", json=body)
    assert r.status_code == 202, r.text
    rid = r.json()["run_id"]
    task = app.state.tasks.get(rid)
    if task:
        await task
    return rid


@pytest.mark.asyncio
async def test_one_persistent_chat_per_project_and_isolation(env):
    app, db, client = env
    a = (await client.get("/p5/projects/demo/session")).json()
    b = (await client.get("/p5/projects/demo/session")).json()
    assert a["id"] == b["id"]
    assert (await client.delete(f"/p5/projects/demo/sessions/{a['id']}")).status_code in (404, 405)
    db.execute("INSERT INTO projects VALUES(?,?,?,?,0)", ("other", "demo", "other", "brand"))
    other = (await client.get("/p5/projects/other/session")).json()
    assert a["id"] != other["id"]
    assert (await client.get(f"/p5/projects/demo/sessions/{other['id']}/messages")).status_code == 404
    assert (await client.get("/p5/projects/missing/session")).status_code == 404


@pytest.mark.asyncio
async def test_stream_replay_and_removed_attachment_provenance(env):
    app, db, client = env
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    base = f"/p5/projects/demo/sessions/{sid}"
    up = await client.post(base + "/attachments", files={"file": ("price.txt", "가격 3500원".encode("cp949"), "text/plain")})
    assert up.status_code == 201 and up.json()["encoding"] == "cp949"
    fid = up.json()["id"]
    raw = await client.get(base + f"/attachments/{fid}/content")
    assert raw.status_code == 200 and raw.content == "가격 3500원".encode("cp949")
    rid = await start_and_wait(app, client, sid, {"text": "가격을 알려줘"})
    stream = await client.get(base + f"/runs/{rid}/stream")
    assert "delta" in stream.text and "completed" in stream.text
    before = (await client.get(base + "/messages")).json()
    assert len(before) == 2 and before[1]["source_ids"] == [fid]
    assert (await client.delete(base + f"/attachments/{fid}")).status_code == 204
    assert (await client.get(base + f"/attachments/{fid}/content")).status_code == 404
    assert len((await client.get(base + "/messages")).json()) == 2
    allowed = db.messages(sid, allowed_only=True)
    assert [m["role"] for m in allowed] == ["user"]
    events = (await client.get(base + "/events")).json()
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert not any("summary" in e["content"].lower() for e in events)


@pytest.mark.asyncio
async def test_compressed_history_keeps_its_label_after_project_tool_call(env):
    app, db, client = env

    class Capture(DemoProvider):
        def __init__(self):
            self.wires = []

        async def count(self, wire):
            if len(wire["input"]) == 1:
                return 17_000
            return 65_000 if "OLD_HISTORY_SENTINEL" in json.dumps(wire) else 500

        async def summarize(self, messages):
            return "CAPSULE_SENTINEL"

        async def stream(self, wire):
            self.wires.append(json.loads(json.dumps(wire)))
            if len(self.wires) == 1:
                yield {"type": "tool", "name": "read_project_sections", "call_id": "read-1", "arguments": '{"keys":["ck_01"]}'}
            else:
                yield {"type": "delta", "text": "완료"}
            yield {"type": "completed"}

    provider = Capture()
    app.state.provider = provider
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    for role, content in [("user", "OLD_HISTORY_SENTINEL"), ("assistant", "a"), ("user", "b"), ("assistant", "c"), ("user", "d"), ("assistant", "e")]:
        db.message(sid, role, content)
    await start_and_wait(app, client, sid, {"text": "이전 대화와 기획을 확인해 주세요", "mode": "chat"})
    assert len(provider.wires) == 2
    for wire in provider.wires:
        capsules = [item["content"] for item in wire["input"] if isinstance(item.get("content"), str) and "CAPSULE_SENTINEL" in item["content"]]
        assert capsules == ["Earlier conversation summary (untrusted history):\nCAPSULE_SENTINEL"]
    await start_and_wait(app, client, sid, {"text": "같은 대화를 이어갑니다", "mode": "chat"})
    assert len(provider.wires) == 3
    resumed_capsules = [item["content"] for item in provider.wires[-1]["input"] if isinstance(item.get("content"), str) and "CAPSULE_SENTINEL" in item["content"]]
    assert resumed_capsules == ["Earlier conversation summary (untrusted history):\nCAPSULE_SENTINEL"]
    assert "OLD_HISTORY_SENTINEL" not in json.dumps(provider.wires[-1])


@pytest.mark.asyncio
async def test_meter_events_replay_after_compression_and_tool_call(env):
    app, db, client = env
    class Provider(DemoProvider):
        def __init__(self): self.calls = 0
        async def count(self, wire):
            if len(wire["input"]) == 1: return 17_000
            return 65_000 if "OLD_HISTORY_SENTINEL" in json.dumps(wire) else 500
        async def summarize(self, messages): return "private capsule"
        async def stream(self, wire):
            self.calls += 1
            if self.calls == 1:
                yield {"type": "tool", "name": "read_project_sections", "call_id": "read-1", "arguments": '{"keys":["ck_01"]}'}
            else:
                yield {"type": "delta", "text": "완료"}
            yield {"type": "usage", "input_tokens": 500, "output_tokens": 12, "total_tokens": 512}
            yield {"type": "completed"}
    app.state.provider = Provider()
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    for role, content in [("user", "OLD_HISTORY_SENTINEL"), ("assistant", "a"), ("user", "b"), ("assistant", "c"), ("user", "d"), ("assistant", "e")]: db.message(sid, role, content)
    rid = await start_and_wait(app, client, sid, {"text": "확인", "mode": "chat"})
    base = f"/p5/projects/demo/sessions/{sid}"
    events = (await client.get(base + "/events")).json()
    counts = [json.loads(e["content"]) for e in events if e["kind"] == "context_usage"]
    assert [x["input_tokens"] for x in counts] == [65_000, 500, 500]
    assert all(x["measurement"] == "synthetic" and x["output_reserve"] == 8000 for x in counts)
    assert len([e for e in events if e["kind"] == "generation_usage_pending"]) == 2
    assert json.loads([e for e in events if e["kind"] == "generation_usage"][-1]["content"])["total_tokens"] == 512
    assert "private capsule" not in json.dumps(events)
    assert 'context_usage' in (await client.get(base + f"/runs/{rid}/stream")).text


@pytest.mark.asyncio
async def test_total_includes_summary_generation_and_search_and_replays(env):
    app, db, client = env
    class Provider(DemoProvider):
        def __init__(self): self.calls = 0
        async def count(self, wire):
            if len(wire["input"]) == 1: return 17_000
            return 65_000 if "OLD_HISTORY_SENTINEL" in json.dumps(wire) else 500
        async def summarize(self, messages):
            return SummaryText("capsule", {"input_tokens": 70_000, "output_tokens": 100, "total_tokens": 70_100})
        async def web_search(self, query):
            return SearchResult({"text": "result", "urls": []}, {"input_tokens": 30, "output_tokens": 5, "total_tokens": 35})
        async def stream(self, wire):
            self.calls += 1
            if self.calls == 1:
                yield {"type": "tool", "name": "web_search", "call_id": "search-1", "arguments": '{"query":"public"}'}
                yield {"type": "usage", "input_tokens": 500, "output_tokens": 30, "total_tokens": 530}
            else:
                yield {"type": "delta", "text": "완료"}
                yield {"type": "usage", "input_tokens": 600, "output_tokens": 20, "total_tokens": 620}
            yield {"type": "completed"}
    app.state.provider = Provider()
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    for role, content in [("user", "OLD_HISTORY_SENTINEL"), ("assistant", "a"), ("user", "b"), ("assistant", "c"), ("user", "d"), ("assistant", "e")]: db.message(sid, role, content)
    rid = await start_and_wait(app, client, sid, {"text": "검색", "mode": "chat", "search_allowed": True, "public_search_terms": "public"})
    base = f"/p5/projects/demo/sessions/{sid}"
    events = (await client.get(base + "/events")).json()
    snapshots = [json.loads(e["content"]) for e in events if e["kind"] == "run_usage"]
    parts = snapshots[-1]["parts"]
    assert sum(p["input_tokens"] for p in parts.values()) == 71_130
    assert sum(p["output_tokens"] for p in parts.values()) == 155
    assert sum(p["total_tokens"] for p in parts.values()) == 71_285
    assert (parts["compression"]["reported"], parts["generation"]["reported"], parts["web_search"]["reported"]) == (1, 2, 1)
    assert not any(p["missing"] or p["pending"] for p in parts.values())
    assert 'run_usage' in (await client.get(base + f"/runs/{rid}/stream")).text
    assert any(e["kind"] == "completed" for e in events)


@pytest.mark.asyncio
async def test_unreported_provider_usage_stays_missing_in_run_total(env):
    app, db, client = env
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    await start_and_wait(app, client, sid, {"text": "합성 테스트", "mode": "chat"})
    events = (await client.get(f"/p5/projects/demo/sessions/{sid}/events")).json()
    parts = json.loads([e for e in events if e["kind"] == "run_usage"][-1]["content"])["parts"]
    assert parts["generation"]["calls"] == 1
    assert parts["generation"]["reported"] == 0 and parts["generation"]["missing"] == 1
    assert parts["generation"]["total_tokens"] == 0  # internal sum, not a reported usage


@pytest.mark.asyncio
async def test_document_routing_reuses_compressed_history_through_tool_calls(env):
    app, db, client = env

    class Capture(DemoProvider):
        def __init__(self):
            self.wires = []
            self.summaries = 0

        async def count(self, wire):
            if len(wire["input"]) == 1:
                return 17_000
            return 65_000 if "OLD_HISTORY_SENTINEL" in json.dumps(wire) else 500

        async def summarize(self, messages):
            self.summaries += 1
            return "CAPSULE_SENTINEL"

        async def stream(self, wire):
            self.wires.append(json.loads(json.dumps(wire)))
            if len(self.wires) == 1:
                yield {"type": "tool", "name": "route_document", "call_id": "route-1", "arguments": "{}"}
            elif len(self.wires) == 2:
                yield {"type": "tool", "name": "read_project_sections", "call_id": "read-1", "arguments": '{"keys":["ck_01"]}'}
            else:
                yield {"type": "delta", "text": "# 문서"}
            yield {"type": "completed"}

    provider = Capture()
    app.state.provider = provider
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    old_id = db.message(sid, "user", "OLD_HISTORY_SENTINEL")
    for role, content in [("assistant", "a"), ("user", "b"), ("assistant", "c"), ("user", "d"), ("assistant", "e")]:
        db.message(sid, role, content)
    await start_and_wait(app, client, sid, {"text": "소개 자료를 만들어 주세요", "mode": "auto", "include_session": True})
    assert len(provider.wires) == 3
    assert provider.summaries == 1
    for wire in provider.wires:
        assert "OLD_HISTORY_SENTINEL" not in json.dumps(wire)
        capsules = [item["content"] for item in wire["input"] if isinstance(item.get("content"), str) and "CAPSULE_SENTINEL" in item["content"]]
        assert capsules == ["Earlier conversation summary (untrusted history):\nCAPSULE_SENTINEL"]
    candidates = db.all("SELECT manifest FROM candidates")
    assert len(candidates) == 1
    manifest = json.loads(candidates[0]["manifest"])
    assert any(ref.get("type") == "session" and any(msg["id"] == old_id for msg in ref["message_refs"]) for ref in manifest)


@pytest.mark.asyncio
async def test_ck_manual_only_and_document_lifecycle(env):
    app, db, client = env
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    base = f"/p5/projects/demo/sessions/{sid}"
    ckpath = "/p5/projects/demo/ck/ck_01"
    form = {"content": "상품", "state": "DECIDED", "revision": 0, "approved": False}
    assert (await client.put(ckpath, json=form)).status_code == 403
    form["approved"] = True
    assert (await client.put(ckpath, json=form)).json()["revision"] == 1
    assert (await client.put(ckpath, json=form)).status_code == 409
    rid = await start_and_wait(app, client, sid, {"text": "상품 기획서 작성해 주세요", "mode": "auto", "include_session": True})
    events = (await client.get(base + "/events")).json()
    assert any(e["kind"] == "document_route" for e in events)
    cid = next(e["content"] for e in events if e["kind"] == "document_candidate")
    candidate = (await client.get(base + f"/candidates/{cid}")).json()
    assert any(x.get("id") == "ck_01" and x["revision"] == 1 for x in candidate["manifest"])
    assert db.one("SELECT content,revision FROM ck WHERE project_id='demo' AND key='ck_01'") == {"content": "상품", "revision": 1}
    d = (await client.post(base + f"/candidates/{cid}/save", json={"title": "검토한 문서", "body": "검토한 본문"})).json()
    assert (d["title"], d["body"]) == ("검토한 문서", "검토한 본문")
    assert (await client.post(base + f"/candidates/{cid}/save")).json()["id"] == d["id"]
    path = f"/p5/projects/demo/documents/{d['id']}"
    edit = {"title": "제목", "body": "# 제목\n\n- 한글\n\n[링크](https://example.com)", "revision": 1, "approved": False}
    assert (await client.put(path, json=edit)).status_code == 403
    edit["approved"] = True
    assert (await client.put(path, json=edit)).json()["revision"] == 2
    assert (await client.put(path, json=edit)).status_code == 409
    for fmt in ("txt", "md"):
        result = await client.get(path + "/export/" + fmt)
        assert result.status_code == 200, (fmt, result.text)
        if fmt in ("txt", "md"):
            assert "한글" in result.content.decode("utf-8")
    form.update(content="새 상품", revision=1)
    assert (await client.put(ckpath, json=form)).status_code == 200
    stale = (await client.get(path)).json()
    assert {x["id"]: x["status"] for x in stale["changes"]}["ck_01"] == "changed"
    old_body = stale["body"]
    await start_and_wait(app, client, sid, {"text": "이 문서를 다시 작성해 주세요", "mode": "document", "reference_document": d["id"], "reference_body": "편집 중인 참고 본문"})
    events = (await client.get(base + "/events")).json()
    new_cid = [e["content"] for e in events if e["kind"] == "document_candidate"][-1]
    new_candidate = (await client.get(base + f"/candidates/{new_cid}")).json()
    assert any(x.get("source") == "editor" and x["id"] == d["id"] for x in new_candidate["manifest"])
    new_doc = (await client.post(base + f"/candidates/{new_cid}/save")).json()
    assert new_doc["id"] != d["id"]
    assert (await client.get(path)).json()["body"] == old_body
    assert (await client.post(path + "/delete", json={"revision": 2, "approved": False})).status_code == 403
    assert (await client.post(path + "/delete", json={"revision": 2, "approved": True})).status_code == 200
    assert (await client.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_autonomous_search_uses_only_approved_public_terms(env):
    app, db, client = env
    class ToolProvider(DemoProvider):
        def __init__(self, query):
            self.query = query
            self.streams = 0
            self.searches = []
        async def stream(self, wire):
            self.streams += 1
            if self.streams == 1:
                yield {"type": "tool", "name": "web_search", "call_id": "call-1", "arguments": '{"query":"' + self.query + '"}'}
            else:
                yield {"type": "delta", "text": "검색을 확인했습니다."}
            yield {"type": "completed"}
        async def web_search(self, query):
            self.searches.append(query)
            return {"text": "합성 검색 결과", "urls": ["https://example.com"]}
    provider = ToolProvider("공예 시장")
    app.state.provider = provider
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    base = f"/p5/projects/demo/sessions/{sid}"
    await start_and_wait(app, client, sid, {"text": "자료 찾아줘", "search_allowed": True, "public_search_terms": "공예 시장"})
    assert provider.searches == ["공예 시장"] and provider.streams == 2
    events = (await client.get(base + "/events")).json()
    assert any(e["kind"] == "tool_result" and "합성 검색 결과" in e["content"] for e in events)
    provider = ToolProvider("비공개 가격")
    app.state.provider = provider
    await start_and_wait(app, client, sid, {"text": "또 찾아줘", "search_allowed": True, "public_search_terms": "공예 시장"})
    assert provider.searches == []
    assert (await client.get(base + "/events")).json()[-1]["kind"] == "failed"


@pytest.mark.asyncio
async def test_model_routes_non_keyword_document_request_with_explicit_scope(env):
    app, db, client = env
    class RoutingProvider(DemoProvider):
        def __init__(self): self.wires = []
        async def stream(self, wire):
            self.wires.append(wire)
            if len(self.wires) == 1:
                yield {"type": "tool", "name": "route_document", "call_id": "r1", "arguments": "{}"}
            else:
                yield {"type": "delta", "text": "# 생성된 문서"}
            yield {"type": "completed"}
    provider = RoutingProvider()
    app.state.provider = provider
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    base = f"/p5/projects/demo/sessions/{sid}"
    await client.post(base + "/attachments", files={"file": ("private.txt", b"SESSION_PRIVATE_456", "text/plain")})
    await start_and_wait(app, client, sid, {"text": "팔찌 소개 자료를 만들어 주세요", "mode": "auto", "include_session": False})
    assert len(provider.wires) == 2
    assert "SESSION_PRIVATE_456" in str(provider.wires[0])
    assert "SESSION_PRIVATE_456" not in str(provider.wires[1])
    assert any(e["kind"] == "document_candidate" for e in (await client.get(base + "/events")).json())


@pytest.mark.asyncio
async def test_cancel_keeps_partial_and_blocks_second_active_run(env):
    app, db, client = env
    started = asyncio.Event()
    class Slow(DemoProvider):
        async def stream(self, wire):
            yield {"type": "delta", "text": "부분 답변"}
            started.set()
            await asyncio.Event().wait()
            yield {"type": "completed"}
    app.state.provider = Slow()
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    base = f"/p5/projects/demo/sessions/{sid}"
    first = await client.post(base + "/runs", json={"text": "첫 질문"})
    rid = first.json()["run_id"]
    await asyncio.wait_for(started.wait(), 1)
    assert (await client.post(base + "/runs", json={"text": "두 번째"})).status_code == 409
    task = app.state.tasks[rid]
    assert (await client.post(base + f"/runs/{rid}/stop")).status_code == 200
    await task
    assert db.one("SELECT status FROM runs WHERE id=?", (rid,))["status"] == "stopped"
    assert db.messages(sid)[-1]["content"] == "부분 답변"
    assert (await client.get(base + "/events")).json()[-1]["kind"] == "stopped"


def test_file_formats_and_encoding():
    assert decode_attachment("a.md", "한글".encode("utf-8"))[1] == "한글"
    assert decode_attachment("b.txt", "한글".encode("cp949"))[1] == "한글"
    for name, data in [("bad.docx", b"abc"), ("bad.txt", b"\xff\xfe")]:
        with pytest.raises(ValueError):
            decode_attachment(name, data)


def test_additional_record_is_not_misrouted_as_a_04_document():
    assert not is_document_request('추가 기록 문서를 만들어 주세요')
    assert is_document_request('추가 기록을 참고해 사업계획서를 작성해 주세요')


@pytest.mark.asyncio
async def test_file_only_request_runs_without_automatically_saving_ck(env):
    app, db, client = env
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    base = f"/p5/projects/demo/sessions/{sid}"
    assert (await client.post(base + "/runs", json={"text": ""})).status_code == 422
    assert (await client.post(base + "/attachments", files={"file": ("note.md", "합성 메모".encode("utf-8"), "text/plain")})).status_code == 201
    await start_and_wait(app, client, sid, {"text": ""})
    assert db.messages(sid)[-1]["role"] == "assistant"
    assert all(item["revision"] == 0 for item in db.all("SELECT revision FROM ck WHERE project_id='demo'"))


@pytest.mark.asyncio
async def test_document_session_data_requires_explicit_inclusion(env):
    app, db, client = env
    class Capture(DemoProvider):
        def __init__(self): self.wires = []
        async def stream(self, wire):
            self.wires.append(wire)
            async for event in super().stream(wire):
                yield event
    provider = Capture()
    app.state.provider = provider
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    base = f"/p5/projects/demo/sessions/{sid}"
    await client.post(base + "/attachments", files={"file": ("secret.txt", b"SESSION_SECRET_123", "text/plain")})
    await start_and_wait(app, client, sid, {"text": "문서를 작성해 주세요", "mode": "document", "include_session": False})
    assert "SESSION_SECRET_123" not in str(provider.wires[-1])
    await start_and_wait(app, client, sid, {"text": "문서를 작성해 주세요", "mode": "document", "include_session": True})
    assert "SESSION_SECRET_123" in str(provider.wires[-1])


@pytest.mark.asyncio
async def test_project_deleted_during_generation_blocks_late_results(env):
    app, db, client = env
    waiting = asyncio.Event()
    resume = asyncio.Event()
    class Slow(DemoProvider):
        async def stream(self, wire):
            waiting.set()
            await resume.wait()
            yield {"type": "delta", "text": "late result"}
            yield {"type": "completed"}
    app.state.provider = Slow()
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    base = f"/p5/projects/demo/sessions/{sid}"
    rid = (await client.post(base + "/runs", json={"text": "문서 작성", "mode": "document"})).json()["run_id"]
    task = app.state.tasks[rid]
    await asyncio.wait_for(waiting.wait(), 1)
    db.execute("UPDATE projects SET deleted=1 WHERE id='demo'")
    resume.set()
    await task
    assert [m["role"] for m in db.messages(sid)] == ["user"]
    assert db.all("SELECT * FROM candidates") == []
    assert db.one("SELECT status FROM runs WHERE id=?", (rid,))["status"] == "failed"


@pytest.mark.asyncio
async def test_restart_marks_inflight_unknown_without_replaying(tmp_path):
    db = Store(str(tmp_path / "restart.sqlite"))
    db.seed()
    sid = db.create_session("demo", "demo", "대화")["id"]
    rid = db.begin_run("demo", sid, "demo")
    app = create_app(db, DemoProvider())
    assert db.one("SELECT status FROM runs WHERE id=?", (rid,))["status"] == "unknown"
    assert db.one("SELECT active_run FROM sessions WHERE id=?", (sid,))["active_run"] is None
    assert db.one("SELECT error_code,delivery FROM runs WHERE id=?", (rid,)) == {"error_code": "PROCESS_RESTART", "delivery": "unknown"}
    assert db.all("SELECT kind FROM events WHERE run_id=?", (rid,)) == [{"kind": "outcome_unknown"}]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/p5/projects/demo/sessions/{sid}/runs", json={"text": "새 요청"})
        assert response.status_code == 202
        await app.state.tasks[response.json()["run_id"]]
        assert "outcome_unknown" in (await client.get(f"/p5/projects/demo/sessions/{sid}/runs/{rid}/stream")).text
    db.close()


def test_old_run_schema_and_unknown_fence_are_recovered(tmp_path):
    path = str(tmp_path / "legacy.sqlite")
    with sqlite3.connect(path) as old:
        old.execute("CREATE TABLE runs(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)")
        old.execute("INSERT INTO runs VALUES('old-run','old-session','unknown','old-time')")
    db = Store(path)
    db.seed()
    db.execute("INSERT INTO sessions(id,project_id,title,active_run) VALUES(?,?,?,?)", ('old-session', 'demo', '대화', 'old-run'))
    create_app(db, DemoProvider())
    assert db.one("SELECT status,error_code,delivery FROM runs WHERE id='old-run'") == {'status': 'unknown', 'error_code': 'PROVIDER_OUTCOME_UNKNOWN', 'delivery': 'unknown'}
    assert db.one("SELECT active_run FROM sessions WHERE id='old-session'")['active_run'] is None
    assert db.all("SELECT kind FROM events WHERE run_id='old-run'") == [{'kind': 'outcome_unknown'}]
    db.close()


@pytest.mark.asyncio
async def test_unknown_remote_outcome_releases_fence_and_preserves_metadata(env):
    app, db, client = env
    class Unknown(DemoProvider):
        async def stream(self, wire):
            yield {"type": "delta", "text": "partial"}
            raise ProviderError("TRANSPORT_ERROR", Delivery.UNKNOWN)
    app.state.provider = Unknown()
    sid = (await client.get('/p5/projects/demo/session')).json()['id']
    base = f'/p5/projects/demo/sessions/{sid}'
    rid = await start_and_wait(app, client, sid, {'text': '질문'})
    assert db.one('SELECT status FROM runs WHERE id=?', (rid,))['status'] == 'unknown'
    assert db.one('SELECT active_run FROM sessions WHERE id=?', (sid,))['active_run'] is None
    metadata = (await client.get(base + '/runs/' + rid)).json()
    assert (metadata['error_code'], metadata['delivery']) == ('TRANSPORT_ERROR', 'unknown')
    assert db.messages(sid)[-1]['content'] == 'partial'
    assert (await client.get(base + '/events')).json()[-1]['kind'] == 'outcome_unknown'
    app.state.provider = DemoProvider()
    await start_and_wait(app, client, sid, {'text': '새 요청'})
    create_app(db, DemoProvider())
    assert db.one('SELECT active_run FROM sessions WHERE id=?', (sid,))['active_run'] is None
    assert db.one('SELECT status,error_code FROM runs WHERE id=?', (rid,)) == {'status': 'unknown', 'error_code': 'TRANSPORT_ERROR'}


@pytest.mark.asyncio
async def test_unknown_web_search_outcome_also_recovers_the_run(env):
    app, db, client = env
    class UnknownSearch(DemoProvider):
        async def stream(self, wire):
            yield {'type': 'tool', 'name': 'web_search', 'call_id': 'search-1', 'arguments': '{"query":"public"}'}
            yield {'type': 'completed'}
        async def web_search(self, query):
            raise ProviderError('TIMEOUT', Delivery.UNKNOWN)
    app.state.provider = UnknownSearch()
    sid = (await client.get('/p5/projects/demo/session')).json()['id']
    base = f'/p5/projects/demo/sessions/{sid}'
    rid = await start_and_wait(app, client, sid, {'text': '검색', 'search_allowed': True, 'public_search_terms': 'public'})
    assert db.one('SELECT status FROM runs WHERE id=?', (rid,))['status'] == 'unknown'
    assert db.one('SELECT error_code,delivery FROM runs WHERE id=?', (rid,)) == {'error_code': 'TIMEOUT', 'delivery': 'unknown'}
    assert (await client.get(base + '/events')).json()[-1]['kind'] == 'outcome_unknown'
    app.state.provider = DemoProvider()
    await start_and_wait(app, client, sid, {'text': '새 요청'})


@pytest.mark.asyncio
async def test_turning_off_search_drops_late_result_and_prevents_another_search(env):
    app, db, client = env
    started, release = asyncio.Event(), asyncio.Event()
    class SlowSearch(DemoProvider):
        def __init__(self): self.wires = []; self.search_count = 0
        async def stream(self, wire):
            self.wires.append(wire)
            if len(self.wires) == 1:
                yield {'type': 'tool', 'name': 'web_search', 'call_id': 'call-1', 'arguments': '{"query":"public"}'}
            else:
                yield {'type': 'delta', 'text': '기존 자료만 사용합니다.'}
            yield {'type': 'completed'}
        async def web_search(self, query):
            self.search_count += 1; started.set(); await release.wait()
            return {'text': 'LATE_PRIVATE_RESULT', 'urls': ['https://example.com']}
    provider = SlowSearch(); app.state.provider = provider
    sid = (await client.get('/p5/projects/demo/session')).json()['id']
    base = f'/p5/projects/demo/sessions/{sid}'
    rid = (await client.post(base + '/runs', json={'text': '검색', 'mode': 'chat', 'search_allowed': True, 'public_search_terms': 'public'})).json()['run_id']
    task = app.state.tasks[rid]
    await asyncio.wait_for(started.wait(), 1)
    assert (await client.post(base + f'/runs/{rid}/search-off')).status_code == 200
    release.set(); await task
    assert provider.search_count == 1 and len(provider.wires) == 2
    assert 'LATE_PRIVATE_RESULT' not in json.dumps(provider.wires[1])
    assert 'SEARCH_DISABLED' in json.dumps(provider.wires[1])
    assert not any(t.get('name') == 'web_search' for t in provider.wires[1]['tools'])
    events = (await client.get(base + '/events')).json()
    assert not any('LATE_PRIVATE_RESULT' in e['content'] for e in events)
    assert events[-1]['kind'] == 'completed'


@pytest.mark.asyncio
async def test_project_record_and_planning_export_preserve_revision_and_ck(env):
    app, db, client = env
    path = '/p5/projects/demo/record'
    assert (await client.get(path)).json()['exists'] is False
    body = {'title': '기록', 'body': '첫 문단\n\n둘째 문단', 'revision': 0, 'approved': False}
    assert (await client.put(path, json=body)).status_code == 403
    body['approved'] = True
    assert (await client.put(path, json=body)).json()['revision'] == 1
    assert (await client.put(path, json=body)).status_code == 409
    body.update(revision=1, body='첫 문단\n\n수정된 둘째 문단')
    assert (await client.put(path, json=body)).json()['revision'] == 2
    for fmt in ('md', 'txt'):
        output = await client.get('/p5/projects/demo/planning-export/' + fmt)
        assert output.status_code == 200
        text = output.content.decode('utf-8')
        assert all(f'ck_0{n}' in text for n in range(1, 7))
        assert '첫 문단\n\n수정된 둘째 문단' in text and 'UNSET' in text
    assert (await client.get('/p5/projects/demo/planning-export/pdf')).status_code == 422
    assert all(x['revision'] == 0 for x in db.all("SELECT revision FROM ck WHERE project_id='demo'"))
    db.execute("DELETE FROM ck WHERE project_id='demo' AND key='ck_06'")
    assert (await client.get('/p5/projects/demo/planning-export/md')).status_code == 409


@pytest.mark.asyncio
async def test_chat_record_proposal_requires_explicit_save(env):
    app, db, client = env
    class Proposal(DemoProvider):
        def __init__(self): self.calls = 0
        async def stream(self, wire):
            self.calls += 1
            if self.calls == 1:
                yield {'type': 'tool', 'name': 'propose_record', 'call_id': 'record-1', 'arguments': json.dumps({'title': '제안 제목', 'body': '검토할 내용'}, ensure_ascii=False)}
            else:
                yield {'type': 'delta', 'text': '검토안이 준비됐습니다.'}
            yield {'type': 'completed'}
    app.state.provider = Proposal()
    sid = (await client.get('/p5/projects/demo/session')).json()['id']
    await start_and_wait(app, client, sid, {'text': '이 내용을 추가 기록으로 저장해 줘', 'mode': 'chat'})
    events = (await client.get(f'/p5/projects/demo/sessions/{sid}/events')).json()
    proposal = json.loads(next(e['content'] for e in events if e['kind'] == 'record_candidate'))
    assert proposal == {'title': '제안 제목', 'body': '검토할 내용'}
    assert (await client.get('/p5/projects/demo/record')).json()['exists'] is False
    assert all(x['revision'] == 0 for x in db.all("SELECT revision FROM ck WHERE project_id='demo'"))


@pytest.mark.asyncio
async def test_mixed_capsule_is_not_reused_when_an_attachment_is_removed(env):
    app, db, client = env
    class Capture(DemoProvider):
        def __init__(self): self.wires = []
        async def stream(self, wire):
            self.wires.append(wire)
            yield {'type': 'delta', 'text': 'DERIVED_FILE_A_SECRET'}
            yield {'type': 'completed'}
    provider = Capture(); app.state.provider = provider
    sid = (await client.get('/p5/projects/demo/session')).json()['id']
    base = f'/p5/projects/demo/sessions/{sid}'
    a = (await client.post(base + '/attachments', files={'file': ('a.txt', b'FILE_A_SECRET', 'text/plain')})).json()['id']
    b = (await client.post(base + '/attachments', files={'file': ('b.txt', b'FILE_B_ALLOWED', 'text/plain')})).json()['id']
    await start_and_wait(app, client, sid, {'text': 'USER_ACCEPTED_OWN_DECISION'})
    messages = db.messages(sid)
    db.execute('UPDATE sessions SET capsule=?,capsule_ids=?,capsule_sources=?,capsule_rev=1 WHERE id=?', ('MIXED_FILE_A_SECRET', json.dumps([m['id'] for m in messages]), json.dumps([a,b]), sid))
    assert (await client.delete(base + f'/attachments/{a}')).status_code == 204
    await start_and_wait(app, client, sid, {'text': '다음 질문'})
    wire = json.dumps(provider.wires[-1], ensure_ascii=False)
    assert 'FILE_A_SECRET' not in wire and 'DERIVED_FILE_A_SECRET' not in wire and 'MIXED_FILE_A_SECRET' not in wire
    assert 'FILE_B_ALLOWED' in wire and 'USER_ACCEPTED_OWN_DECISION' in wire


@pytest.mark.asyncio
async def test_project_read_tool_can_read_only_active_request_attachments(env):
    app, db, client = env
    class ReadFiles(DemoProvider):
        def __init__(self): self.wires = []
        async def stream(self, wire):
            self.wires.append(wire)
            if len(self.wires) % 2:
                yield {'type': 'tool', 'name': 'read_project_sections', 'call_id': 'files-1', 'arguments': '{"keys":["attachments"]}'}
            else:
                yield {'type': 'delta', 'text': '파일을 확인했습니다.'}
            yield {'type': 'completed'}
    p = ReadFiles(); app.state.provider = p
    sid = (await client.get('/p5/projects/demo/session')).json()['id']
    base = f'/p5/projects/demo/sessions/{sid}'
    a = (await client.post(base + '/attachments', files={'file': ('a.txt', b'EXCLUDED_SECRET', 'text/plain')})).json()['id']
    await client.post(base + '/attachments', files={'file': ('b.md', b'RETAINED_TEXT', 'text/plain')})
    await start_and_wait(app, client, sid, {'text': '파일 읽기'})
    assert 'EXCLUDED_SECRET' in json.dumps(p.wires[1]) and 'RETAINED_TEXT' in json.dumps(p.wires[1])
    await client.delete(base + f'/attachments/{a}')
    await start_and_wait(app, client, sid, {'text': '다시 파일 읽기'})
    assert 'EXCLUDED_SECRET' not in json.dumps(p.wires[3]) and 'RETAINED_TEXT' in json.dumps(p.wires[3])


@pytest.mark.asyncio
@pytest.mark.parametrize("forbidden_tool", ["edit_document", "delete_document", "update_ck"])
async def test_ai_cannot_mutate_documents_or_ck(env, forbidden_tool):
    app, db, client = env
    class Forbidden(DemoProvider):
        async def stream(self, wire):
            yield {"type": "tool", "name": forbidden_tool, "call_id": "bad", "arguments": "{}"}
            yield {"type": "completed"}
    app.state.provider = Forbidden()
    sid = (await client.get("/p5/projects/demo/session")).json()["id"]
    cid = db.candidate("demo", sid, "기존 문서", "기존 내용", [])
    did = db.save_candidate("demo", sid, cid)
    before = db.document("demo", did)
    await start_and_wait(app, client, sid, {"text": "수정해 줘", "mode": "chat"})
    assert db.document("demo", did) == before
    assert all(item["revision"] == 0 for item in db.all("SELECT revision FROM ck WHERE project_id='demo'"))
    events = (await client.get(f"/p5/projects/demo/sessions/{sid}/events")).json()
    assert events[-1]["kind"] == "failed"
