"""정산 수입·지출 내역: 적기·고치기·휴지통·복원과 합계."""
import pytest
from bson import ObjectId


@pytest.fixture
async def url(client, login):
    await login(client)
    project = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return f"/projects/{project['id']}"


async def add(client, url, **body):
    entry = {"type": "income", "title": "팔찌 판매", "amount": 49000, "date": "2026-11-20", **body}
    res = await client.post(f"{url}/ledger", json=entry)
    assert res.status_code == 201, res.text
    return res.json()


async def test_entries_and_totals(client, url):
    sale = await add(client, url, note="와디즈 1차")
    await add(client, url, type="expense", title="은 체인 구입", amount=28000, date="2026-11-22")
    await add(client, url, type="income", title="플리마켓", amount=30000, date="2026-11-18")
    ledger = (await client.get(f"{url}/ledger")).json()
    assert (ledger["income"], ledger["expense"]) == (79000, 28000)
    # 날짜가 늦은 것부터
    assert [e["title"] for e in ledger["entries"]] == ["은 체인 구입", "팔찌 판매", "플리마켓"]
    assert (sale["type"], sale["amount"], sale["date"], sale["note"], sale["status"]) == ("income", 49000, "2026-11-20", "와디즈 1차", "active")
    recent = (await client.get(url)).json()["recent"]
    assert recent[0]["text"] == "정산 내역 추가 · 수입 30,000원"


async def test_update_with_revision(client, url):
    entry = await add(client, url)
    res = await client.patch(f"{url}/ledger/{entry['id']}", json={"amount": 53000, "revision": entry["revision"]})
    assert res.status_code == 200 and res.json()["amount"] == 53000 and res.json()["title"] == "팔찌 판매"
    stale = await client.patch(f"{url}/ledger/{entry['id']}", json={"amount": 1, "revision": entry["revision"]})
    assert stale.status_code == 409
    assert (await client.get(f"{url}/ledger")).json()["income"] == 53000


async def test_trash_and_restore_change_totals(client, url):
    entry = await add(client, url, type="expense", title="포장재", amount=1500)
    assert (await client.delete(f"{url}/ledger/{entry['id']}")).status_code == 204
    ledger = (await client.get(f"{url}/ledger")).json()
    assert (ledger["expense"], ledger["entries"]) == (0, [])
    trash = (await client.get(f"{url}/ledger", params={"status": "trash"})).json()
    assert [e["status"] for e in trash["entries"]] == ["trash"]
    restored = await client.post(f"{url}/ledger/{entry['id']}/restore")
    assert restored.status_code == 200 and restored.json()["status"] == "active"
    assert (await client.get(f"{url}/ledger")).json()["expense"] == 1500


@pytest.mark.parametrize("body", [
    {"amount": 0}, {"amount": -100}, {"amount": 1_000_000_000_001}, {"type": "refund"},
    {"title": " "}, {"date": "2026-13-01"}, {"note": "x" * 201},
])
async def test_invalid_entries_are_rejected(client, url, body):
    entry = {"type": "income", "title": "판매", "amount": 1000, "date": "2026-11-20", **body}
    assert (await client.post(f"{url}/ledger", json=entry)).status_code == 422


async def test_ledger_is_scoped_to_owner_and_project(client, url, login):
    entry = await add(client, url)
    other = (await client.post("/projects", json={"name": "다른 프로젝트"})).json()
    assert (await client.delete(f"/projects/{other['id']}/ledger/{entry['id']}")).status_code == 404
    assert (await client.patch(f"{url}/ledger/{ObjectId()}", json={"amount": 1, "revision": 1})).status_code == 404
    await client.post("/auth/logout")
    await login(client, email="other@example.com")
    assert (await client.get(f"{url}/ledger")).status_code == 404


async def test_deleting_project_removes_ledger_and_conversations(client, url, db):
    await add(client, url)
    await client.post(f"{url}/conversations", json={"title": "대화"})
    project_id = ObjectId(url.rsplit("/", 1)[1])
    assert (await client.delete(url)).status_code == 204
    res = await client.delete(url, params={"permanent": "true"})
    assert res.status_code == 204, res.text
    assert await db.ledger_entries.count_documents({"project_id": project_id}) == 0
    assert await db.conversations.count_documents({"project_id": project_id}) == 0
