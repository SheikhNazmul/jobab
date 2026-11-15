import uuid
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password, new_token, token_hash, verify_password
from app.core.utils import clean_text, client_ip, utcnow
from app.modules.shops.models import Membership
from app.modules.users.models import User, UserSession

settings = get_settings()

# The __Host- prefix makes browsers refuse the cookie unless it is Secure,
# has Path=/ and no Domain, which blocks a sibling subdomain from setting it.
COOKIE_NAME = "__Host-sid" if settings.cookie_secure else "sid"


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == normalize_email(email)))


async def create_user(session: AsyncSession, name: str, email: str, password: str) -> User:
    user = User(
        name=clean_text(name, 120),
        email=normalize_email(email),
        password=await hash_password(password),
    )
    session.add(user)
    await session.flush()
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    user = await get_user_by_email(session, email)
    # Always run one hash check, so a missing account takes as long as a
    # wrong password.
    ok = await verify_password(password, user.password if user else None)
    if not ok or user is None or not user.is_active:
        return None
    return user


async def first_membership(session: AsyncSession, user_id: uuid.UUID) -> Membership | None:
    return await session.scalar(
        select(Membership).where(Membership.user_id == user_id).order_by(Membership.created_at)
    )


async def start_session(
    session: AsyncSession, user: User, shop_id: uuid.UUID, request: Request, response: Response
) -> None:
    """Creates a server-side session and sets its cookie. The database keeps
    only a hash, so a leaked database does not give working cookies."""
    token = new_token()
    session.add(
        UserSession(
            token_hash=token_hash(token),
            user_id=user.id,
            shop_id=shop_id,
            ip=client_ip(request)[:64],
            user_agent=clean_text(request.headers.get("user-agent"), 255),
            expires_at=utcnow() + timedelta(days=settings.SESSION_DAYS),
        )
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.SESSION_DAYS * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


async def end_session(session: AsyncSession, request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await session.execute(
            delete(UserSession).where(UserSession.token_hash == token_hash(token))
        )
    response.delete_cookie(
        COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax"
    )


async def end_other_sessions(
    session: AsyncSession, user_id: uuid.UUID, keep_hash: str | None = None
) -> None:
    stmt = delete(UserSession).where(UserSession.user_id == user_id)
    if keep_hash:
        stmt = stmt.where(UserSession.token_hash != keep_hash)
    await session.execute(stmt)
