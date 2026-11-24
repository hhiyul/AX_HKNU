from datetime import datetime, timezone

from bson import ObjectId


async def create_brand(client, **fields):
    res = await client.post("/brands", json={"brand_type": "new", **fields})
    assert res.status_code == 201, res.text
    return res.json()


async def test_create_brand_defaults(client, login):
    await login(client)
    brand = await create_brand(client)
    assert brand["status"] == "draft"
    assert brand["keywords"] == [] and brand["colors"] == []
    assert brand["name"] is None and brand["logo_asset_id"] is None
    assert "user_id" in brand


async def test_create_brand_requires_login_and_type(client, login):
    assert (await client.post("/brands", json={"brand_type": "new"})).status_code == 401
    await login(client)
    # 종류를 보내지 않으면 이미 운영 중인 브랜드로 만든다.
    created = await client.post("/brands", json={"name": "온유칠보", "description": "칠보 공방"})
    assert created.status_code == 201 and created.json()["brand_type"] == "existing"
    assert (await client.post("/brands", json={"brand_type": "other"})).status_code == 422


async def test_prototype_only_fields_are_rejected(client, login):
    await login(client)
    assert (await client.post("/brands", json={"brand_type": "new", "craft_type": "손뜨개"})).status_code == 422


async def test_partial_patch_keeps_other_fields(client, login):
    await login(client)
    brand = await create_brand(client, name="뜨고와", keywords=["미니멀"])
    res = await client.patch(f"/brands/{brand['id']}", json={"description": "  소개  "})
    assert res.status_code == 200
    updated = res.json()
    assert updated["name"] == "뜨고와" and updated["keywords"] == ["미니멀"]
    assert updated["description"] == "소개"
    assert updated["updated_at"] > brand["updated_at"]


async def test_blank_text_becomes_null(client, login):
    await login(client)
    brand = await create_brand(client, name="뜨고와")
    res = await client.patch(f"/brands/{brand['id']}", json={"name": "   "})
    assert res.json()["name"] is None


async def test_null_not_allowed_fields_return_422(client, login):
    await login(client)
    brand = await create_brand(client)
    for field in ("keywords", "colors", "status", "brand_type"):
        res = await client.patch(f"/brands/{brand['id']}", json={field: None})
        assert res.status_code == 422, field


async def test_completed_requires_name(client, login):
    await login(client)
    brand = await create_brand(client)
    url = f"/brands/{brand['id']}"
    for body in ({"status": "completed"}, {"status": "completed", "name": "   "}):
        res = await client.patch(url, json=body)
        assert res.status_code == 422
        assert res.json()["code"] == "NAME_REQUIRED"
    assert (await client.patch(url, json={"status": "completed", "name": "뜨고와"})).status_code == 200
    res = await client.patch(url, json={"name": None})
    assert res.status_code == 422
    assert res.json()["code"] == "NAME_REQUIRED"


async def test_list_shows_only_my_brands(client, login):
    await login(client)
    mine = await create_brand(client)
    await login(client, email="b@test.com")
    await create_brand(client)
    res = await client.get("/brands")
    assert len(res.json()) == 1
    assert res.json()[0]["id"] != mine["id"]


async def test_other_users_brand_is_404(client, login):
    await login(client)
    brand = await create_brand(client)
    await login(client, email="b@test.com")
    url = f"/brands/{brand['id']}"
    assert (await client.get(url)).status_code == 404
    assert (await client.patch(url, json={"name": "x"})).status_code == 404
    assert (await client.delete(url)).status_code == 404


async def test_invalid_id_format_is_404(client, login):
    await login(client)
    res = await client.get("/brands/not-an-id")
    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"


async def test_logo_must_belong_to_brand(client, login, db):
    me = await login(client)
    brand = await create_brand(client)
    other = await create_brand(client)
    now = datetime.now(timezone.utc)
    asset = await db.assets.insert_one(
        {
            "user_id": ObjectId(me["id"]),
            "brand_id": ObjectId(other["id"]),
            "asset_type": "logo",
            "source": "upload",
            "file_url": "/assets/x/file",
            "file_name": "a.png",
            "mime_type": "image/png",
            "created_at": now,
            "updated_at": now,
        }
    )
    logo_id = str(asset.inserted_id)
    url = f"/brands/{brand['id']}"
    assert (await client.patch(url, json={"logo_asset_id": str(ObjectId())})).status_code == 404
    assert (await client.patch(url, json={"logo_asset_id": logo_id})).status_code == 404
    res = await client.patch(f"/brands/{other['id']}", json={"logo_asset_id": logo_id})
    assert res.status_code == 200
    assert res.json()["logo_asset_id"] == logo_id


async def test_delete_brand_keeps_its_projects(client, login):
    await login(client)
    brand = await create_brand(client)
    project = (await client.post("/projects", json={"name": "상품", "brand_id": brand["id"]})).json()
    assert (await client.delete(f"/brands/{brand['id']}")).status_code == 204
    assert (await client.get(f"/brands/{brand['id']}")).status_code == 404
    # 프로젝트는 지우지 않고 "브랜드 없음"으로 남긴다.
    assert (await client.get(f"/projects/{project['id']}")).json()["brand_id"] is None


async def test_text_length_limits(client, login):
    await login(client)
    assert (await client.post("/brands", json={"brand_type": "new", "name": "x" * 201})).status_code == 422
    assert (await client.post("/brands", json={"brand_type": "new", "keywords": ["x" * 101]})).status_code == 422
