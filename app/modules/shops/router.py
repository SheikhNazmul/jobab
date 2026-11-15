import uuid
from datetime import timedelta

from fastapi import APIRouter, Request
from sqlalchemy import delete, func, select, update

from app.core.errors import AppError, Conflict, NotFound
from app.core.security import validate_new_password
from app.core.utils import clean_text, client_ip, utcnow
from app.db.session import SessionDep
from app.modules.automation.models import UsageEvent
from app.modules.shops.models import AuditLog, Membership, Role, Shop
from app.modules.shops.schemas import (
    MemberIn,
    MemberOut,
    MemberRoleIn,
    ShopProfileIn,
    ShopSettings,
)
from app.modules.shops.service import audit, credits_left, owner_count, shop_settings
from app.modules.users.deps import AuthDep, ManagerDep, OwnerDep
from app.modules.users.models import User, UserSession
from app.modules.users.service import create_user, get_user_by_email

router = APIRouter(prefix="/api/shop", tags=["shop"])


@router.get("/settings")
async def get_settings_(auth: AuthDep):
    return {"name": auth.shop.name, "settings": shop_settings(auth.shop).model_dump(mode="json")}


@router.put("/settings")
async def put_settings(body: ShopSettings, request: Request, auth: ManagerDep, session: SessionDep):
    await session.execute(
        update(Shop).where(Shop.id == auth.shop.id).values(settings=body.model_dump(mode="json"))
    )
    await audit(
        session,
        "shop.settings_changed",
        shop_id=auth.shop.id,
        user_id=auth.user.id,
        ip=client_ip(request),
    )
    return {"settings": body.model_dump(mode="json")}


@router.patch("/profile")
async def patch_profile(body: ShopProfileIn, auth: OwnerDep, session: SessionDep):
    name = clean_text(body.name, 160)
    await session.execute(update(Shop).where(Shop.id == auth.shop.id).values(name=name))
    return {"name": name}


@router.get("/usage")
async def usage(auth: AuthDep, session: SessionDep):
    since = utcnow() - timedelta(days=14)
    day = func.date_trunc("day", UsageEvent.created_at)
    rows = await session.execute(
        select(day.label("day"), UsageEvent.kind, func.count())
        .where(UsageEvent.shop_id == auth.shop.id, UsageEvent.created_at >= since)
        .group_by(day, UsageEvent.kind)
        .order_by(day)
    )
    shop = auth.shop
    return {
        "plan": shop.plan,
        "credits_total": shop.credits_total,
        "credits_used": shop.credits_used,
        "credits_left": credits_left(shop),
        "period_ends_at": shop.period_ends_at,
        "daily": [{"day": d.date().isoformat(), "kind": k, "count": c} for d, k, c in rows],
    }


@router.get("/team", response_model=list[MemberOut])
async def team(auth: AuthDep, session: SessionDep):
    rows = await session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.shop_id == auth.shop.id)
        .order_by(Membership.created_at)
    )
    return [
        MemberOut(
            id=m.id, user_id=u.id, name=u.name, email=u.email, role=m.role, created_at=m.created_at
        )
        for m, u in rows
    ]


@router.post("/team", response_model=MemberOut, status_code=201)
async def add_member(body: MemberIn, request: Request, auth: OwnerDep, session: SessionDep):
    validate_new_password(body.password, body.email)
    if await get_user_by_email(session, body.email):
        raise Conflict("This email already has an account.")
    user = await create_user(session, body.name, body.email, body.password)
    membership = Membership(shop_id=auth.shop.id, user_id=user.id, role=body.role)
    session.add(membership)
    await session.flush()
    await audit(
        session,
        "team.member_added",
        shop_id=auth.shop.id,
        user_id=auth.user.id,
        target=user.email,
        detail={"role": body.role.value},
        ip=client_ip(request),
    )
    return MemberOut(
        id=membership.id,
        user_id=user.id,
        name=user.name,
        email=user.email,
        role=membership.role,
        created_at=membership.created_at or utcnow(),
    )


async def _member(session, shop_id: uuid.UUID, membership_id: uuid.UUID) -> Membership:
    membership = await session.scalar(
        select(Membership).where(Membership.id == membership_id, Membership.shop_id == shop_id)
    )
    if membership is None:
        raise NotFound("That team member was not found.")
    return membership


@router.patch("/team/{membership_id}")
async def change_role(
    membership_id: uuid.UUID,
    body: MemberRoleIn,
    request: Request,
    auth: OwnerDep,
    session: SessionDep,
):
    membership = await _member(session, auth.shop.id, membership_id)
    losing_owner = membership.role == Role.OWNER and body.role != Role.OWNER
    if losing_owner and await owner_count(session, auth.shop.id) <= 1:
        raise AppError("A shop needs at least one owner.", code="last_owner")
    membership.role = body.role
    await audit(
        session,
        "team.role_changed",
        shop_id=auth.shop.id,
        user_id=auth.user.id,
        target=str(membership.user_id),
        detail={"role": body.role.value},
        ip=client_ip(request),
    )
    return {"ok": True}


@router.delete("/team/{membership_id}")
async def remove_member(
    membership_id: uuid.UUID, request: Request, auth: OwnerDep, session: SessionDep
):
    membership = await _member(session, auth.shop.id, membership_id)
    if membership.user_id == auth.user.id:
        raise AppError("You cannot remove yourself.", code="cannot_remove_self")
    # The account is switched off and signed out everywhere, not just unlinked.
    await session.execute(delete(UserSession).where(UserSession.user_id == membership.user_id))
    await session.execute(
        update(User).where(User.id == membership.user_id).values(is_active=False)
    )
    await session.delete(membership)
    await audit(
        session,
        "team.member_removed",
        shop_id=auth.shop.id,
        user_id=auth.user.id,
        target=str(membership.user_id),
        ip=client_ip(request),
    )
    return {"ok": True}


@router.get("/audit")
async def audit_log(auth: OwnerDep, session: SessionDep):
    rows = await session.execute(
        select(AuditLog, User.name)
        .outerjoin(User, User.id == AuditLog.user_id)
        .where(AuditLog.shop_id == auth.shop.id)
        .order_by(AuditLog.created_at.desc())
        .limit(100)
    )
    return [
        {
            "id": log.id,
            "action": log.action,
            "target": log.target,
            "detail": log.detail,
            "by": name or "",
            "ip": log.ip,
            "created_at": log.created_at,
        }
        for log, name in rows
    ]
