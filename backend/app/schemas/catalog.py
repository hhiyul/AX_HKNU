"""문서 작성용 제품 정본. 반복 판매·재고 운영은 포함하지 않는다."""
from typing import Annotated, Literal
from pydantic import Field, StringConstraints
from app.schemas.common import RequestModel, Revision

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Won = Annotated[int, Field(strict=True, ge=0, le=10**12)]
Quantity = Annotated[int, Field(strict=True, ge=0, le=10**9)]


class Option(RequestModel):
    name: Text
    size: Text = ""
    color: Text = ""
    sale_price: Won | None = None  # None inherits base; 0 is an actual free price.
    initial_quantity: Quantity | None = None
    produced_quantity: Quantity | None = None
    sold_quantity: Quantity | None = None
    replenishment_threshold: Quantity | None = None


class Product(RequestModel):
    name: Text = ""
    intent: Text = ""
    material: Text = ""
    process: Text = ""
    photo_ids: Annotated[list[str], Field(max_length=20)] = []
    cost: Won | None = None
    sale_price: Won | None = None
    price_estimated: bool = True
    quantity_date: Annotated[str | None, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None
    options: Annotated[list[Option], Field(max_length=100)] = []
    status: Literal["draft", "registered"] = "draft"


class ProductEdit(Product):
    revision: Revision


class Contact(RequestModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    category: Text = ""
    contact: Text = ""
    note: Text = ""
    scope: Literal["project", "brand"] = "project"


class ContactEdit(Contact):
    revision: Revision
