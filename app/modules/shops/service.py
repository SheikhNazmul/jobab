import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import new_token
from app.core.utils import utcnow
from app.modules.automation.models import UsageEvent
from app.modules.channels.models import Channel, ChannelType
from app.modules.shops.models import AuditLog, Membership, Role, Shop
from app.modules.shops.schemas import ShopSettings
from app.modules.users.models import User

settings = get_settings()


def shop_settings(shop: Shop) -> ShopSettings:
    """Stored settings with defaults filled in for anything missing."""
    return ShopSettings.model_validate(shop.settings or {})


def credits_left(shop: Shop) -> int:
    return max(shop.credits_total - shop.credits_used, 0)


def period_active(shop: Shop) -> bool:
    return shop.period_ends_at is None or shop.period_ends_at > utcnow()


def can_use_ai(shop: Shop) -> bool:
    return shop.is_active and period_active(shop) and credits_left(shop) > 0


async def create_shop(session: AsyncSession, name: str, owner: User) -> Shop:
    """A new shop starts on the trial plan with a website-chat channel ready."""
    shop = Shop(
        name=name,
        plan="trial",
        credits_total=settings.TRIAL_CREDITS,
        period_ends_at=utcnow() + timedelta(days=settings.TRIAL_DAYS),
        settings=ShopSettings().model_dump(mode="json"),
    )
    session.add(shop)
    await session.flush()
    session.add(Membership(shop_id=shop.id, user_id=owner.id, role=Role.OWNER))
    session.add(
        Channel(
            shop_id=shop.id,
            type=ChannelType.WEBCHAT,
            external_id=f"wk_{new_token(18)}",
            name="Website chat",
        )
    )
    await session.flush()
    return shop


async def spend_credit(
    session: AsyncSession,
    shop_id: uuid.UUID,
    kind: str,
    *,
    conversation_id: uuid.UUID | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> bool:
    """Takes one credit if the shop has one. Safe when two replies race:
    the check and the update are a single statement."""
    result = await session.execute(
        update(Shop)
        .where(
            Shop.id == shop_id,
            Shop.is_active.is_(True),
            Shop.credits_used < Shop.credits_total,
            (Shop.period_ends_at.is_(None)) | (Shop.period_ends_at > utcnow()),
        )
        .values(credits_used=Shop.credits_used + 1)
        .returning(Shop.id)
    )
    if result.first() is None:
        return False
    session.add(
        UsageEvent(
            shop_id=shop_id,
            kind=kind,
            conversation_id=conversation_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    )
    return True


async def audit(
    session: AsyncSession,
    action: str,
    *,
    shop_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    target: str = "",
    detail: dict[str, Any] | None = None,
    ip: str = "",
) -> None:
    """Records a security- or money-relevant action. Never store secrets or
    message text in `detail`."""
    session.add(
        AuditLog(
            shop_id=shop_id,
            user_id=user_id,
            action=action,
            target=target[:160],
            detail=detail or {},
            ip=ip[:64],
        )
    )


async def owner_count(session: AsyncSession, shop_id: uuid.UUID) -> int:
    rows = await session.execute(
        select(Membership.id).where(Membership.shop_id == shop_id, Membership.role == Role.OWNER)
    )
    return len(rows.all())
