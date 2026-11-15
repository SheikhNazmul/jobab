"""Who is calling, which shop they belong to and what they may do.

Every protected route depends on `AuthDep` (or a stricter variant). The shop
comes from the server-side session, never from the request, so a caller
cannot point a request at another shop's data.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Forbidden, Unauthorized
from app.core.security import token_hash
from app.core.utils import utcnow
from app.db.session import SessionDep, SessionLocal
from app.modules.shops.models import Membership, Role, Shop
from app.modules.users.models import User, UserSession
from app.modules.users.service import COOKIE_NAME

settings = get_settings()


@dataclass
class AuthContext:
    user: User
    shop: Shop
    role: Role
    session_hash: str

    @property
    def is_admin(self) -> bool:
        admin = settings.ADMIN_EMAIL.strip().lower()
        return bool(admin) and self.user.email == admin

    def can(self, *roles: Role) -> bool:
        return self.role in roles


async def load_auth(session: AsyncSession, request: Request) -> AuthContext:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise Unauthorized("Sign in to continue.")
    hashed = token_hash(token)
    row = (
        await session.execute(
            select(UserSession, User, Shop, Membership)
            .join(User, User.id == UserSession.user_id)
            .join(Shop, Shop.id == UserSession.shop_id)
            .join(
                Membership,
                (Membership.user_id == User.id) & (Membership.shop_id == Shop.id),
            )
            .where(UserSession.token_hash == hashed)
        )
    ).first()
    if row is None:
        raise Unauthorized("Sign in to continue.")
    user_session, user, shop, membership = row
    now = utcnow()
    if user_session.expires_at <= now or not user.is_active or not shop.is_active:
        raise Unauthorized("Your session has ended. Sign in again.")
    if now - user_session.last_seen_at > timedelta(minutes=10):
        user_session.last_seen_at = now
    return AuthContext(user=user, shop=shop, role=membership.role, session_hash=hashed)


async def get_auth(request: Request, session: SessionDep) -> AuthContext:
    return await load_auth(session, request)


async def auth_for_stream(request: Request) -> AuthContext:
    """For long-lived streams: check the caller, then release the database
    connection instead of holding it for the life of the stream."""
    async with SessionLocal() as session:
        context = await load_auth(session, request)
        await session.commit()
        return context


def require_roles(*roles: Role):
    async def dependency(auth: Annotated[AuthContext, Depends(get_auth)]) -> AuthContext:
        if auth.role not in roles:
            raise Forbidden("Your role does not allow this action.")
        return auth

    return dependency


async def require_admin(auth: Annotated[AuthContext, Depends(get_auth)]) -> AuthContext:
    if not auth.is_admin:
        raise Forbidden("Only the platform admin can do this.")
    return auth


AuthDep = Annotated[AuthContext, Depends(get_auth)]
ManagerDep = Annotated[AuthContext, Depends(require_roles(Role.OWNER, Role.MANAGER))]
OwnerDep = Annotated[AuthContext, Depends(require_roles(Role.OWNER))]
AdminDep = Annotated[AuthContext, Depends(require_admin)]
