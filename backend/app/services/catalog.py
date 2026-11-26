"""인증된 사용자 관리 API와 에이전트 읽기가 공유하는 Mongo 정본."""
import datetime as dt
from pymongo import ReturnDocument
from app.errors import AppError
from app.services.store import conflict, not_found, parse_object_id, utcnow


def query(project, user_id, kind):
    base = {"user_id": user_id, "deleted_at": None}
    if kind == "contacts" and project.get("brand_id"):
        return {**base, "$or": [{"project_id": project["_id"]}, {"project_id": None, "brand_id": project["brand_id"]}]}
    return {**base, "project_id": project["_id"]}


async def get(db, project, user_id, kind, ident):
    obj = await db[kind].find_one({**query(project, user_id, kind), "_id": parse_object_id(ident)})
    if obj is None:
        raise not_found()
    return obj


def missing(product):
    required = [k for k in ("name", "intent", "material", "process", "photo_ids", "options", "quantity_date") if not product.get(k)]
    if product.get("sale_price") is None:
        required.append("sale_price")
    for i, option in enumerate(product.get("options", [])):
        for key in ("name", "size", "color"):
            if not option.get(key):
                required.append(f"options.{i}.{key}")
        for key in ("initial_quantity", "produced_quantity", "sold_quantity", "replenishment_threshold"):
            if option.get(key) is None:
                required.append(f"options.{i}.{key}")
    return required


async def validate_product(db, user_id, body, old=None):
    if body.get("quantity_date"):
        try:
            dt.date.fromisoformat(body["quantity_date"])
        except ValueError:
            raise AppError(422, "INVALID_DATE", "실제 기준일을 입력해 주세요.") from None
    for ident in body["photo_ids"]:
        photo = await db.assets.find_one({"_id": parse_object_id(ident), "user_id": user_id})
        if not photo or photo.get("mime_type") not in ("image/png", "image/jpeg", "image/webp"):
            raise AppError(422, "INVALID_PHOTO", "접근 가능한 제품 사진을 선택해 주세요.")
    for option in body["options"]:
        total = (option.get("initial_quantity") or 0) + (option.get("produced_quantity") or 0)
        if (option.get("sold_quantity") or 0) > total:
            raise AppError(422, "INVALID_QUANTITY", "기존 판매 수량이 초기·제작 수량보다 많습니다.")
    if body["status"] == "registered" and missing(body):
        raise AppError(422, "PRODUCT_INCOMPLETE", "등록에 필요한 정보: " + ", ".join(missing(body)))
    if old and old.get("quantity_initialized_at"):
        fields = ("initial_quantity", "produced_quantity", "sold_quantity")
        prior = [[o.get(k) for k in fields] for o in old["options"]]
        after = [[o.get(k) for k in fields] for o in body["options"]]
        if old.get("quantity_date") != body.get("quantity_date") or prior != after:
            raise AppError(409, "INITIAL_QUANTITY_LOCKED", "최초 등록한 수량과 기준일은 다시 반영하지 않습니다.")
    for option in body["options"]:
        option["inventory"] = (option.get("initial_quantity") or 0) + (option.get("produced_quantity") or 0) - (option.get("sold_quantity") or 0)


async def save(db, project, user_id, kind, body, ident=None):
    old = await get(db, project, user_id, kind, ident) if ident else None
    data = body.model_dump(exclude={"revision"})
    if kind == "products":
        await validate_product(db, user_id, data, old)
        if data["status"] == "registered":
            data["quantity_initialized_at"] = old.get("quantity_initialized_at") if old and old.get("quantity_initialized_at") else utcnow()
    elif data["scope"] == "brand" and not project.get("brand_id"):
        raise AppError(422, "NO_BRAND", "브랜드 공통 거래처는 연결된 브랜드가 필요합니다.")
    data.update(user_id=user_id, project_id=None if kind == "contacts" and data["scope"] == "brand" else project["_id"],
                brand_id=project.get("brand_id"), updated_at=utcnow())
    if old:
        updated = await db[kind].find_one_and_update({"_id": old["_id"], "revision": body.revision, "deleted_at": None},
                    {"$set": data, "$inc": {"revision": 1}}, return_document=ReturnDocument.AFTER)
        if not updated:
            raise conflict()
        return updated
    data.update(revision=1, created_at=utcnow(), deleted_at=None)
    data["_id"] = (await db[kind].insert_one(data)).inserted_id
    return data


async def snapshot(db, project, user_id, ids):
    result = []
    for ident in dict.fromkeys(ids):
        product = await get(db, project, user_id, "products", ident)
        if product["status"] != "registered" or missing(product):
            raise AppError(422, "PRODUCT_INCOMPLETE", "등록 완료된 제품만 문서에 선택할 수 있습니다.")
        for photo_id in product["photo_ids"]:
            if not await db.assets.find_one({"_id": parse_object_id(photo_id), "user_id": user_id}):
                raise AppError(422, "PRODUCT_PHOTO_UNAVAILABLE", "제품 사진을 다시 확인한 뒤 선택해 주세요.")
        result.append({k: v for k, v in product.items() if k not in ("user_id", "deleted_at")})
    return result
