"""Orders on the dashboard: list, view, create by hand, update status."""

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.core.errors import NotFound
from app.core.utils import client_ip, normalize_phone
from app.db.session import SessionDep
from app.modules.orders import service as orders
from app.modules.orders.models import Order, OrderStatus
from app.modules.orders.service import LineRequest
from app.modules.shops.models import Shop
from app.modules.shops.service import audit
from app.modules.users.deps import AuthDep, ManagerDep

router = APIRouter(prefix="/api/orders", tags=["orders"])


class OrderLineIn(BaseModel):
    product_id: uuid.UUID
    variant: str | None = Field(default=None, max_length=120)
    quantity: int = Field(default=1, ge=1, le=1000)


class ManualOrderIn(BaseModel):
    items: list[OrderLineIn] = Field(min_length=1, max_length=50)
    customer_name: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=32)
    address: str = Field(default="", max_length=1000)
    delivery_zone_id: str | None = Field(default=None, max_length=60)
    payment_method: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=1000)


class OrderPatch(BaseModel):
    status: Literal[
        "pending", "confirmed", "packed", "shipped", "delivered", "cancelled", "returned"
    ] | None = None
    courier: str | None = Field(default=None, max_length=80)
    tracking_code: str | None = Field(default=None, max_length=120)
    customer_name: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=1000)
    payment_method: str | None = Field(default=None, max_length=80)


@router.get("")
async def list_orders(
    auth: AuthDep,
    session: SessionDep,
    status: str = Query(default="", max_length=20),
    q: str = Query(default="", max_length=120),
    needs_review: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
):
    where = [Order.shop_id == auth.shop.id]
    if status:
        where.append(Order.status == OrderStatus(status))
    if needs_review:
        where.append(Order.needs_review.is_(True))
    if q.strip():
        like = f"%{q.strip()}%"
        where.append(or_(Order.number.ilike(like), Order.customer_name.ilike(like),
                         Order.phone.ilike(like)))
    total = await session.scalar(select(func.count()).select_from(Order).where(*where))
    rows = await session.scalars(
        select(Order).where(*where).order_by(Order.created_at.desc()).limit(limit)
    )
    return {"total": total or 0, "items": [orders.order_dict(o) for o in rows]}


@router.get("/stats")
async def order_stats(auth: AuthDep, session: SessionDep):
    rows = await session.execute(
        select(Order.status, func.count(), func.coalesce(func.sum(Order.total), 0))
        .where(Order.shop_id == auth.shop.id)
        .group_by(Order.status)
    )
    return {"by_status": [{"status": s.value, "count": c, "total": float(t)} for s, c, t in rows]}


@router.post("", status_code=201)
async def create_order(
    body: ManualOrderIn, request: Request, auth: ManagerDep, session: SessionDep
):
    shop = await session.get(Shop, auth.shop.id)
    lines = [
        LineRequest(product_id=str(i.product_id), variant=i.variant, quantity=i.quantity)
        for i in body.items
    ]
    order = await orders.create_manual_order(
        session, shop, lines=lines, zone_id=body.delivery_zone_id,
        customer_name=body.customer_name,
        phone=normalize_phone(body.phone) or body.phone,
        address=body.address, payment_method=body.payment_method, note=body.note,
    )
    await audit(session, "order.created_manual", shop_id=auth.shop.id, user_id=auth.user.id,
                target=order.number, ip=client_ip(request))
    return orders.order_dict(order)


@router.get("/{order_id}")
async def get_order(order_id: uuid.UUID, auth: AuthDep, session: SessionDep):
    order = await orders.get_order(session, auth.shop.id, order_id)
    if order is None:
        raise NotFound("Order not found.")
    return orders.order_dict(order)


@router.patch("/{order_id}")
async def patch_order(
    order_id: uuid.UUID, body: OrderPatch, request: Request, auth: ManagerDep, session: SessionDep
):
    order = await orders.get_order(session, auth.shop.id, order_id)
    if order is None:
        raise NotFound("Order not found.")
    if body.status is not None and body.status != order.status.value:
        await orders.update_status(session, auth.shop.id, order, OrderStatus(body.status))
        await audit(session, "order.status_changed", shop_id=auth.shop.id, user_id=auth.user.id,
                    target=order.number, detail={"status": body.status}, ip=client_ip(request))
    for field in ("courier", "tracking_code", "customer_name", "phone", "address", "note",
                  "payment_method"):
        value = getattr(body, field)
        if value is not None:
            setattr(order, field, value)
    if order.needs_review and body.status in ("confirmed", "cancelled"):
        order.needs_review = False
    await session.flush()
    return orders.order_dict(order)
