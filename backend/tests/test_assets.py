from pathlib import Path

import pytest
from bson import ObjectId

from app.config import get_settings

PNG = b"\x89PNG\r\n\x1a\n" + b"image-data"
JPEG = b"\xff\xd8\xff" + b"image-data"
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"image-data"
MAX_SIZE = 2097152


async def create_brand(client):
    return (await client.post("/brands", json={"brand_type": "new"})).json()


async def upload(client, brand_id, content=PNG, name="logo.png", mime="image/png", asset_type="logo", **fields):
    return await client.post(
        "/assets",
        data={"brand_id": brand_id, "asset_type": asset_type, **fields},
        files={"file": (name, content, mime)},
    )


def stored_files():
    root = Path(get_settings().upload_dir)
    return [path.name for path in root.iterdir()] if root.exists() else []


async def test_upload_png_saved_with_slide_fields(client, login, db):
    await login(client)
    brand = await create_brand(client)
    res = await upload(client, brand["id"])
    assert res.status_code == 201
    asset = res.json()
    assert asset["source"] == "upload"
    assert asset["file_url"] == f"/assets/{asset['id']}/file"
    assert asset["file_name"] == "logo.png"
    assert asset["mime_type"] == "image/png"
    assert asset["brand_id"] == brand["id"]
    assert asset["project_id"] is None and asset["ai_job_id"] is None
    assert await db.assets.find_one({"_id": ObjectId(asset["id"])})


@pytest.mark.parametrize(
    "content,name,mime,expected",
    [
        (JPEG, "a.jpg", "image/jpeg", "image/jpeg"),
        (WEBP, "a.webp", "image/webp", "image/webp"),
        ("메모".encode(), "a.txt", "text/plain", "text/plain"),
        (PNG, "a.bin", "application/octet-stream", "image/png"),
    ],
)
async def test_allowed_types_detected_from_content(client, login, content, name, mime, expected):
    await login(client)
    res = await upload(client, (await create_brand(client))["id"], content, name, mime, "document")
    assert res.status_code == 201
    assert res.json()["mime_type"] == expected


async def test_download_returns_same_bytes_and_safe_headers(client, login):
    await login(client)
    asset = (await upload(client, (await create_brand(client))["id"])).json()
    res = await client.get(asset["file_url"])
    assert res.status_code == 200
    assert res.content == PNG
    assert res.headers["content-type"] == "image/png"
    # 사진은 브라우저에서 바로 보인다(채팅·참고 자료 미리보기).
    assert res.headers["content-disposition"].startswith("inline")
    assert res.headers["x-content-type-options"] == "nosniff"


async def test_documents_are_downloaded_not_shown(client, login):
    await login(client)
    project = (await client.post("/projects", json={"name": "팔찌"})).json()
    res = await client.post("/assets", data={"project_id": project["id"], "asset_type": "document"},
                            files={"file": ("견적.txt", "은 체인 2,800원".encode(), "text/plain")})
    download = await client.get(res.json()["file_url"])
    assert download.headers["content-disposition"].startswith("attachment")
    assert download.headers["x-content-type-options"] == "nosniff"


async def test_size_limit(client, login):
    await login(client)
    brand_id = (await create_brand(client))["id"]
    exact = PNG + b"0" * (MAX_SIZE - len(PNG))
    assert (await upload(client, brand_id, exact)).status_code == 201
    res = await upload(client, brand_id, exact + b"0")
    assert res.status_code == 413
    assert res.json()["code"] == "FILE_TOO_LARGE"


@pytest.mark.parametrize(
    "content,mime",
    [
        (b"%PDF-1.4 text only", "application/pdf"),
        (b"just text", "image/png"),
        (b"\xff\xfe not utf-8", "text/plain"),
        (b"bad\x00text", "text/plain"),
        (b"", "text/plain"),
    ],
)
async def test_disallowed_and_fake_types_rejected(client, login, content, mime):
    await login(client)
    before = stored_files()
    res = await upload(client, (await create_brand(client))["id"], content, "x.bin", mime)
    assert res.status_code == 422
    assert res.json()["code"] == "INVALID_ASSET"
    assert stored_files() == before


async def test_bad_asset_type_rejected(client, login):
    await login(client)
    res = await upload(client, (await create_brand(client))["id"], asset_type="other")
    assert res.status_code == 422


async def test_korean_and_traversal_filename(client, login):
    await login(client)
    asset = (await upload(client, (await create_brand(client))["id"], name="../../evil 고양이.png")).json()
    assert asset["file_name"] == "evil 고양이.png"
    assert asset["id"] in stored_files()
    assert not Path(get_settings().upload_dir, "..", "evil 고양이.png").exists()
    res = await client.get(asset["file_url"])
    assert res.status_code == 200
    assert res.content == PNG


async def test_project_must_belong_to_brand(client, login):
    await login(client)
    first, second = await create_brand(client), await create_brand(client)
    project = (await client.post("/projects", json={"name": "상품", "brand_id": first["id"]})).json()
    assert (await upload(client, second["id"], project_id=project["id"])).status_code == 404
    res = await upload(client, first["id"], project_id=project["id"])
    assert res.status_code == 201
    assert res.json()["project_id"] == project["id"]


async def test_upload_to_other_users_brand_is_404(client, login):
    await login(client)
    brand = await create_brand(client)
    await login(client, email="b@test.com")
    assert (await upload(client, brand["id"])).status_code == 404


async def test_other_users_asset_is_404(client, login):
    await login(client)
    brand = await create_brand(client)
    asset = (await upload(client, brand["id"])).json()
    await login(client, email="b@test.com")
    url = f"/assets/{asset['id']}"
    assert (await client.get(url)).status_code == 404
    assert (await client.get(f"{url}/file")).status_code == 404
    assert (await client.delete(url)).status_code == 404
    assert (await client.get("/assets", params={"brand_id": brand["id"]})).status_code == 404


async def test_list_assets_by_brand(client, login):
    await login(client)
    first, second = await create_brand(client), await create_brand(client)
    asset = (await upload(client, first["id"])).json()
    await upload(client, second["id"])
    listed = (await client.get("/assets", params={"brand_id": first["id"]})).json()
    assert [item["id"] for item in listed] == [asset["id"]]
    assert (await client.get("/assets")).status_code == 422


async def test_missing_file_on_disk_returns_404(client, login):
    await login(client)
    asset = (await upload(client, (await create_brand(client))["id"])).json()
    Path(get_settings().upload_dir, asset["id"]).unlink()
    res = await client.get(asset["file_url"])
    assert res.status_code == 404
    assert res.json()["code"] == "FILE_MISSING"
    assert (await client.delete(f"/assets/{asset['id']}")).status_code == 204


async def test_delete_clears_logo_and_file(client, login):
    await login(client)
    brand = await create_brand(client)
    asset = (await upload(client, brand["id"])).json()
    await client.patch(f"/brands/{brand['id']}", json={"logo_asset_id": asset["id"]})
    assert (await client.delete(f"/assets/{asset['id']}")).status_code == 204
    assert (await client.get(f"/brands/{brand['id']}")).json()["logo_asset_id"] is None
    assert asset["id"] not in stored_files()
    assert (await client.get(f"/assets/{asset['id']}")).status_code == 404


async def test_delete_project_keeps_brand_files_and_removes_its_own(client, login):
    await login(client)
    brand = await create_brand(client)
    project = (await client.post("/projects", json={"name": "상품", "brand_id": brand["id"]})).json()
    shared = (await upload(client, brand["id"], project_id=project["id"])).json()
    res = await client.post(
        "/assets",
        data={"project_id": project["id"], "asset_type": "document"},
        files={"file": ("memo.md", "# 메모".encode(), "text/markdown")},
    )
    assert res.status_code == 201, res.text
    own = res.json()
    assert own["brand_id"] is None and own["mime_type"] == "text/markdown"
    assert (await client.delete(f"/projects/{project['id']}")).status_code == 204
    # 휴지통에 있는 동안에는 파일도 그대로다.
    assert (await client.get(f"/assets/{own['id']}")).status_code == 200
    assert (await client.delete(f"/projects/{project['id']}", params={"permanent": "true"})).status_code == 204
    # 브랜드에도 속한 파일은 연결만 풀리고, 프로젝트에만 속한 파일은 지워진다.
    kept = (await client.get(f"/assets/{shared['id']}")).json()
    assert kept["project_id"] is None
    assert (await client.get(f"/assets/{own['id']}")).status_code == 404
    assert shared["id"] in stored_files() and own["id"] not in stored_files()


async def test_upload_needs_brand_or_project(client, login):
    await login(client)
    res = await client.post(
        "/assets", data={"asset_type": "logo"}, files={"file": ("logo.png", PNG, "image/png")}
    )
    assert res.status_code == 422
    assert res.json()["code"] == "ASSET_OWNER_REQUIRED"
    assert (await client.get("/assets")).status_code == 422


async def test_delete_brand_removes_its_own_files_only(client, login):
    await login(client)
    brand = await create_brand(client)
    project = (await client.post("/projects", json={"name": "상품", "brand_id": brand["id"]})).json()
    logo = (await upload(client, brand["id"])).json()
    shared = (await upload(client, brand["id"], project_id=project["id"])).json()
    assert (await client.delete(f"/brands/{brand['id']}")).status_code == 204
    # 브랜드에만 속한 파일은 지워지고, 프로젝트에도 속한 파일은 프로젝트에 남는다.
    assert (await client.get(f"/assets/{logo['id']}")).status_code == 404
    kept = (await client.get(f"/assets/{shared['id']}")).json()
    assert kept["brand_id"] is None and kept["project_id"] == project["id"]
    assert logo["id"] not in stored_files() and shared["id"] in stored_files()


async def test_upload_requires_login(client):
    assert (await upload(client, str(ObjectId()))).status_code == 401
