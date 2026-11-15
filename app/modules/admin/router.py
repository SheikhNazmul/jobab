"""Platform admin: the operator's view across all shops.

Only the account whose email matches ADMIN_EMAIL may use these routes.
"""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.errors import NotFound
from app.core.utils import client_ip, utcnow
from app.db.session import SessionDep
from app.modules.automation.models import UsageEvent
from app.modules.orders.models import Order
from app.modules.shops.models import Membership, Role, Shop
from app.modules.shops.service import audit
from app.modules.users.deps import AdminDep
from app.modules.users.models import User

router = APIRouter(prefix="/api/admin", tags=["admin"])


class CreditsIn(BaseModel):
    amount: int = Field(ge=1, le=1_000_000)


class PlanIn(BaseModel):
    plan: str = Field(min_length=1, max_length=40)
    days: int | None = Field(default=None, ge=1, le=3650)
    credits: int | None = Field(default=None, ge=0, le=10_000_000)


@router.get("/shops")
async def list_shops(auth: AdminDep, session: SessionDep):
    owner = (
        select(Membership.shop_id, func.min(User.email).label("owner_email"))
        .join(User, User.id == Membership.user_id)
        .where(Membership.role == Role.OWNER)
        .group_by(Membership.shop_id)
        .subquery()
    )
    rows = await session.execute(
        select(Shop, owner.c.owner_email).outerjoin(owner, owner.c.shop_id == Shop.id)
        .order_by(Shop.created_at.desc())
    )
    return {
        "shops": [
            {
                "id": str(shop.id),
                "name": shop.name,
                "plan": shop.plan,
                "owner_email": email or "",
                "credits_total": shop.credits_total,
                "credits_used": shop.credits_used,
                "credits_left": max(shop.credits_total - shop.credits_used, 0),
                "period_ends_at": shop.period_ends_at,
                "is_active": shop.is_active,
                "created_at": shop.created_at,
            }
            for shop, email in rows
        ]
    }


@router.get("/overview")
async def overview(auth: AdminDep, session: SessionDep):
    since = utcnow() - timedelta(days=30)
    return {
        "shops": await session.scalar(select(func.count()).select_from(Shop)) or 0,
        "active_shops": await session.scalar(
            select(func.count()).select_from(Shop).where(Shop.is_active.is_(True))) or 0,
        "orders_30d": await session.scalar(
            select(func.count()).select_from(Order).where(Order.created_at >= since)) or 0,
        "ai_replies_30d": await session.scalar(
            select(func.count()).select_from(UsageEvent).where(
                UsageEvent.kind == "ai_reply", UsageEvent.created_at >= since)) or 0,
    }


async def _shop(session: SessionDep, shop_id: uuid.UUID) -> Shop:
    shop = await session.get(Shop, shop_id)
    if shop is None:
        raise NotFound("Shop not found.")
    return shop


@router.post("/shops/{shop_id}/credits")
async def add_credits(
    shop_id: uuid.UUID, body: CreditsIn, request: Request, auth: AdminDep, session: SessionDep
):
    shop = await _shop(session, shop_id)
    shop.credits_total += body.amount
    await audit(session, "admin.credits_added", shop_id=shop.id, user_id=auth.user.id,
                detail={"amount": body.amount}, ip=client_ip(request))
    return {"ok": True, "credits_total": shop.credits_total}


@router.post("/shops/{shop_id}/plan")
async def set_plan(
    shop_id: uuid.UUID, body: PlanIn, request: Request, auth: AdminDep, session: SessionDep
):
    shop = await _shop(session, shop_id)
    shop.plan = body.plan
    if body.days is not None:
        shop.period_ends_at = utcnow() + timedelta(days=body.days)
    if body.credits is not None:
        shop.credits_total = body.credits
        shop.credits_used = 0
    await audit(session, "admin.plan_changed", shop_id=shop.id, user_id=auth.user.id,
                detail={"plan": body.plan}, ip=client_ip(request))
    return {"ok": True, "plan": shop.plan, "period_ends_at": shop.period_ends_at}
