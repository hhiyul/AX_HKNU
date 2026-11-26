from fastapi import APIRouter, Depends
from app.deps import Workspace, Once
from app.schemas import catalog as schema
from app.schemas.common import to_api
from app.services import catalog

router = APIRouter(prefix="/projects/{project_id}", tags=["catalog"])


@router.get("/products")
async def products(ws: Workspace = Depends()):
    return to_api(await ws.db.products.find(catalog.query(ws.project, ws.user_id, "products")).sort("updated_at", -1).to_list())


@router.post("/products", status_code=201)
async def create_product(body: schema.Product, once: Once = Depends(), ws: Workspace = Depends()):
    return to_api(await once.run(lambda: catalog.save(ws.db, ws.project, ws.user_id, "products", body)))


@router.put("/products/{ident}")
async def update_product(ident: str, body: schema.ProductEdit, ws: Workspace = Depends()):
    return to_api(await catalog.save(ws.db, ws.project, ws.user_id, "products", body, ident))


@router.get("/contacts")
async def contacts(ws: Workspace = Depends()):
    return to_api(await ws.db.contacts.find(catalog.query(ws.project, ws.user_id, "contacts")).sort("updated_at", -1).to_list())


@router.post("/contacts", status_code=201)
async def create_contact(body: schema.Contact, once: Once = Depends(), ws: Workspace = Depends()):
    return to_api(await once.run(lambda: catalog.save(ws.db, ws.project, ws.user_id, "contacts", body)))


@router.put("/contacts/{ident}")
async def update_contact(ident: str, body: schema.ContactEdit, ws: Workspace = Depends()):
    return to_api(await catalog.save(ws.db, ws.project, ws.user_id, "contacts", body, ident))
