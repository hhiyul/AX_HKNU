"""UT 전에 손본 작은 동작들: 문서 제목, TXT 변환, 검색 뒤 문서 전환, 대화에서 문서로, 서버 종료."""
import asyncio
import json

import pytest
from bson import ObjectId

from ai_fakes import COMPLETED, FakeProvider, answer, tool_call
from app.ai.events import RunEvents
from app.ai.export import to_text
from app.ai.runtime import Runtime, set_runtime
from app.services import ai_chat, chat
from app.services.ai_chat import _doc_title


@pytest.mark.parametrize(("text", "title"), [
    ("# **칠보** 팔찌 `기획서`\n본문", "칠보 팔찌 기획서"),
    ("```\n# 코드 주석\n```\n## 진짜 제목", "진짜 제목"),
    ("# #\n본문", "생성 문서"),
    ("제목 없음", "생성 문서"),
    ("## [링크 제목](https://example.com)", "링크 제목"),
])
def test_document_title_is_clean(text, title):
    assert _doc_title(text) == title


def test_text_export_drops_script_and_style():
    body = "# 제목\n\n<script>alert('x')</script>\n\n<style>p{color:red}</style>\n\n본문"
    text = to_text(body)
    assert "alert" not in text and "color" not in text
    assert "제목" in text and "본문" in text


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


async def test_search_results_survive_route_to_document(db, setup):
    url = f"/projects/{setup['project_id']}"
    revision = (await setup["client"].get(url)).json()["revision"]
    await setup["client"].patch(url, json={"search_enabled": True, "revision": revision})
    provider = FakeProvider(
        [
            [tool_call("web_search_generalized", {"concepts": ["craft"]}), COMPLETED],
            [tool_call("route_document", {}, call_id="c2"), COMPLETED],
            answer("# 문서\n검색 반영"),
        ],
        search_results=[{"text": "수공예 시장 결과", "urls": ["https://example.com/a"]}],
    )
    await run(db, setup, provider, "시장 상황 보고 정리해서 남겨 줘")
    wire = json.dumps(provider.calls[2]["input"], ensure_ascii=False)
    assert "수공예 시장 결과" in wire
    assert len(provider.queries) == 1


async def test_auto_document_from_chat_keeps_conversation(db, setup):
    project_id = ObjectId(setup["project_id"])
    await chat.add_message(db, setup["user_id"], project_id, "USER", "고객은 30대 직장인으로 하자")
    provider = FakeProvider([answer("# 기획서\n본문")])
    await run(db, setup, provider, "이 내용으로 기획서 작성해 줘")
    assert "고객은 30대 직장인으로 하자" in json.dumps(provider.calls[0]["input"], ensure_ascii=False)


async def test_shutdown_stops_running_runs_and_releases_lock(db, setup):
    import main

    gate = asyncio.Event()
    provider = FakeProvider([answer("반쯤", " 나머지")], gate=gate)
    runtime = Runtime(provider, RunEvents(), {}, "gpt-6-luna")
    set_runtime(runtime)
    try:
        project = await db.projects.find_one({"_id": ObjectId(setup["project_id"])})
        run_id = await ai_chat.start_run(db, runtime, setup["user_id"], project, "x")
        await asyncio.wait_for(provider.paused.wait(), 5)
        await main.stop_running_runs()
        saved = await db.ai_runs.find_one({"_id": run_id})
        assert saved["status"] == "stopped"
        assert (await db.projects.find_one({"_id": project["_id"]}))["active_run"] is None
        assert runtime.tasks == {}
    finally:
        set_runtime(None)
