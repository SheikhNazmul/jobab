from fastapi import APIRouter, Request, Response
from sqlalchemy import update

from app.core.config import get_settings
from app.core.errors import AppError, Conflict, Forbidden, Unauthorized
from app.core.ratelimit import enforce
from app.core.security import hash_password, validate_new_password, verify_password
from app.core.utils import clean_text, client_ip
from app.db.session import SessionDep, SessionLocal
from app.modules.shops.models import Role
from app.modules.shops.service import audit, create_shop, credits_left
from app.modules.users.deps import AuthContext, AuthDep
from app.modules.users.models import User
from app.modules.users.schemas import ChangePasswordIn, LoginIn, SignupIn
from app.modules.users.service import (
    authenticate,
    create_user,
    end_other_sessions,
    end_session,
    first_membership,
    get_user_by_email,
    normalize_email,
    start_session,
)

settings = get_settings()
router = APIRouter(prefix="/api/auth", tags=["auth"])


def me_payload(auth: AuthContext) -> dict:
    shop = auth.shop
    return {
        "user": {"id": auth.user.id, "name": auth.user.name, "email": auth.user.email},
        "shop": {
            "id": shop.id,
            "name": shop.name,
            "plan": shop.plan,
            "credits_total": shop.credits_total,
            "credits_used": shop.credits_used,
            "credits_left": credits_left(shop),
            "period_ends_at": shop.period_ends_at,
        },
        "role": auth.role.value,
        "is_admin": auth.is_admin,
        "app": {
            "name": settings.APP_NAME,
            "base_url": settings.base_url,
            "ai_ready": settings.AI_ENABLED and settings.llm_configured,
            "vision": settings.LLM_VISION,
            "voice": settings.stt_configured,
            "meta_ready": settings.meta_configured,
            "semantic_search": bool(settings.EMBEDDING_MODEL),
        },
    }


async def _audit_failure(action: str, email: str, ip: str) -> None:
    # A failed request rolls its transaction back, so this note is written
    # in a transaction of its own.
    async with SessionLocal() as session:
        await audit(session, action, detail={"email": email}, ip=ip)
        await session.commit()


@router.post("/signup", status_code=201)
async def signup(body: SignupIn, request: Request, response: Response, session: SessionDep):
    ip = client_ip(request)
    enforce(f"signup:{ip}", 5, 3600)
    if not settings.ALLOW_SIGNUP:
        raise Forbidden("Sign-up is closed. Ask the team for an account.")
    validate_new_password(body.password, body.email)
    if await get_user_by_email(session, body.email):
        raise Conflict("An account with this email already exists. Sign in instead.")

    user = await create_user(session, body.name, body.email, body.password)
    shop = await create_shop(session, clean_text(body.shop_name, 160), user)
    await audit(session, "auth.signup", shop_id=shop.id, user_id=user.id, ip=ip)
    await start_session(session, user, shop.id, request, response)
    return me_payload(AuthContext(user=user, shop=shop, role=Role.OWNER, session_hash=""))


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, session: SessionDep):
    ip = client_ip(request)
    email = normalize_email(body.email)
    enforce(f"login-ip:{ip}", 20, 300)
    enforce(
        f"login-email:{email}",
        6,
        300,
        "Too many sign-in attempts for this account. Wait five minutes and try again.",
    )
    user = await authenticate(session, email, body.password)
    membership = await first_membership(session, user.id) if user else None
    if user is None or membership is None:
        await _audit_failure("auth.login_failed", email, ip)
        # One message for every failure, so nobody can test which emails exist.
        raise Unauthorized("Email or password is incorrect.")

    await audit(
        session, "auth.login", shop_id=membership.shop_id, user_id=user.id, ip=ip
    )
    await start_session(session, user, membership.shop_id, request, response)
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response, session: SessionDep):
    await end_session(session, request, response)
    return {"ok": True}


@router.get("/me")
async def me(auth: AuthDep):
    return me_payload(auth)


@router.post("/password")
async def change_password(
    body: ChangePasswordIn, request: Request, auth: AuthDep, session: SessionDep
):
    enforce(f"password:{auth.user.id}", 5, 900)
    if not await verify_password(body.current_password, auth.user.password):
        raise AppError("Your current password is incorrect.", code="wrong_password")
    validate_new_password(body.new_password, auth.user.email)
    await session.execute(
        update(User)
        .where(User.id == auth.user.id)
        .values(password=await hash_password(body.new_password))
    )
    # Every other browser is signed out, in case the old password was known.
    await end_other_sessions(session, auth.user.id, keep_hash=auth.session_hash)
    await audit(
        session,
        "auth.password_changed",
        shop_id=auth.shop.id,
        user_id=auth.user.id,
        ip=client_ip(request),
    )
    return {"ok": True}
