"""AI에게 줄 자료(services/ai_context.py) 테스트."""
import pytest
from bson import ObjectId

from app.ai.context import history_summary_text
from app.services import ai_context, chat
from app.services.store import utcnow

OWN_KEYS = ["project_basic", "linked_brand", *[f"ck_0{n}" for n in range(1, 7)]]


@pytest.fixture
async def me(client, login):
    return ObjectId((await login(client))["id"])


async def make_project(client, name):
    return (await client.post("/projects", json={"name": name})).json()["id"]


async def load(db, project_id):
    return await db.projects.find_one({"_id": ObjectId(project_id)})


async def test_sections_cover_this_project(client, db, me):
    pid = await make_project(client, "칠보 팔찌")
    sections = await ai_context.build_sections(db, await load(db, pid), me)
    assert list(sections) == OWN_KEYS
    assert sections["ck_01"]["state"] == "SKIPPED"
    assert sections["project_basic"]["name"] == "칠보 팔찌"
    assert {section["source"] for section in sections.values()} == {"this_project"}


async def test_references_included_with_prefixed_keys(client, db, me):
    pid = await make_project(client, "칠보 팔찌")
    rid = await make_project(client, "지난 팔찌")
    res = await client.put(f"/projects/{rid}/items/plan/ck_02", json={"content": "선물 고객", "revision": 1})
    assert res.status_code == 200, res.text
    assert (await client.patch(f"/projects/{pid}", json={"chip_refs": [rid], "revision": 1})).status_code == 200
    sections = await ai_context.build_sections(db, await load(db, pid), me)
    ref = sections[f"ref:{rid}:ck_02"]
    assert ref["content"] == "선물 고객"
    assert (ref["source"], ref["project_name"]) == ("reference_project", "지난 팔찌")
    assert sections["ck_02"]["content"] is None


async def test_references_skip_foreign_trashed_and_missing(client, db, me, login):
    pid = await make_project(client, "칠보 팔찌")
    trashed = await make_project(client, "버린 프로젝트")
    await client.delete(f"/projects/{trashed}")
    await login(client, email="b@test.com")
    foreign = await make_project(client, "남의 프로젝트")
    refs = [ObjectId(foreign), ObjectId(trashed), ObjectId()]
    await db.projects.update_one({"_id": ObjectId(pid)}, {"$set": {"chip_refs": refs}})
    sections = await ai_context.build_sections(db, await load(db, pid), me)
    assert list(sections) == OWN_KEYS


async def test_references_capped_at_five(client, db, me):
    pid = await make_project(client, "칠보 팔찌")
    refs = [await make_project(client, f"참고 {n}") for n in range(7)]
    await db.projects.update_one({"_id": ObjectId(pid)}, {"$set": {"chip_refs": [ObjectId(r) for r in refs]}})
    sections = await ai_context.build_sections(db, await load(db, pid), me)
    used = list(dict.fromkeys(key.split(":")[1] for key in sections if key.startswith("ref:")))
    assert used == refs[:5]


def test_read_sections_returns_only_known_keys():
    sections = {"ck_01": {"content": "a"}, "ck_02": {"content": "b"}}
    assert ai_context.read_sections(sections, ["ck_02", "nope", "ref:x:ck_01", "ck_01"]) == [
        {"key": "ck_02", "content": "b"},
        {"key": "ck_01", "content": "a"},
    ]


def test_prefix_and_read_tool_shape():
    assert ai_context.prefix_from({"ck_01": {"content": "a"}}) == [{"key": "ck_01", "content": "a"}]
    assert ai_context.READ_TOOL["name"] == "read_project_sections"
    assert "ref:<project_id>:<section>" in ai_context.READ_TOOL["description"]


async def say(db, project_id, role, content, completion="COMPLETE"):
    project = await load(db, project_id)
    message = await chat.add_message(db, project["user_id"], project["_id"], role, content, completion=completion)
    return message["_id"]


async def test_history_marks_partial_answers_and_excludes_current(client, db, me):
    pid = await make_project(client, "칠보 팔찌")
    await say(db, pid, "USER", "a")
    await say(db, pid, "ASSISTANT", "b", completion="PARTIAL")
    await say(db, pid, "ASSISTANT", "c", completion="UNKNOWN")
    current = await say(db, pid, "USER", "d")
    history, revision = await ai_context.load_history(db, ObjectId(pid), current)
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "a"),
        ("assistant", "(중간에 끊긴 답변) b"),
        ("assistant", "(중간에 끊긴 답변) c"),
    ]
    assert revision == 0


async def test_history_uses_capsule_when_all_covered_present(client, db, me):
    pid = await make_project(client, "칠보 팔찌")
    a = await say(db, pid, "USER", "a")
    b = await say(db, pid, "ASSISTANT", "b")
    await say(db, pid, "USER", "c")
    current = await say(db, pid, "USER", "d")
    capsule = {"text": "a와 b 요약", "covered_ids": [str(a), str(b)], "revision": 2}
    await db.projects.update_one({"_id": ObjectId(pid)}, {"$set": {"chat_capsule": capsule}})
    history, revision = await ai_context.load_history(db, ObjectId(pid), current)
    assert history[0] == {
        "id": "capsule", "role": "user", "content": history_summary_text("a와 b 요약"),
        "covered_ids": [str(a), str(b)], "source_ids": [],
    }
    assert [m["content"] for m in history[1:]] == ["c"]
    assert revision == 2


async def test_capsule_dropped_when_covered_message_missing(client, db, me):
    pid = await make_project(client, "칠보 팔찌")
    a = await say(db, pid, "USER", "a")
    b = await say(db, pid, "ASSISTANT", "b")
    current = await say(db, pid, "USER", "c")
    capsule = {"text": "요약", "covered_ids": [str(a), str(b)], "revision": 1}
    await db.projects.update_one({"_id": ObjectId(pid)}, {"$set": {"chat_capsule": capsule, "updated_at": utcnow()}})
    await db.messages.delete_one({"_id": a})
    history, revision = await ai_context.load_history(db, ObjectId(pid), current)
    assert [m["content"] for m in history] == ["b"]
    assert revision == 1
