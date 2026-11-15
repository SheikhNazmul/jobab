"""Channels: the connected places customers write from, and the access tokens
used to answer them. Tokens are encrypted at rest and never leave the server.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret, encrypt_secret, mask_secret, new_token
from app.modules.channels.models import Channel, ChannelType

# Channels customers reach from outside. Website chat is created automatically
# and needs no token.
META_TYPES = (ChannelType.MESSENGER, ChannelType.INSTAGRAM, ChannelType.WHATSAPP)


async def list_channels(session: AsyncSession, shop_id: uuid.UUID) -> list[Channel]:
    rows = await session.scalars(
        select(Channel).where(Channel.shop_id == shop_id).order_by(Channel.created_at)
    )
    return list(rows)


async def get_channel(
    session: AsyncSession, shop_id: uuid.UUID, channel_id: uuid.UUID
) -> Channel | None:
    return await session.scalar(
        select(Channel).where(Channel.id == channel_id, Channel.shop_id == shop_id)
    )


async def find_by_external(
    session: AsyncSession, type_: ChannelType, external_id: str
) -> Channel | None:
    return await session.scalar(
        select(Channel).where(Channel.type == type_, Channel.external_id == external_id)
    )


async def webchat_channel(session: AsyncSession, shop_id: uuid.UUID) -> Channel | None:
    return await session.scalar(
        select(Channel).where(
            Channel.shop_id == shop_id, Channel.type == ChannelType.WEBCHAT
        )
    )


async def find_by_widget_key(session: AsyncSession, key: str) -> Channel | None:
    return await session.scalar(
        select(Channel).where(
            Channel.type == ChannelType.WEBCHAT, Channel.external_id == key
        )
    )


def channel_token(channel: Channel) -> str:
    return decrypt_secret(channel.token_encrypted) if channel.token_encrypted else ""


async def connect_meta_channel(
    session: AsyncSession,
    shop_id: uuid.UUID,
    *,
    type_: ChannelType,
    external_id: str,
    name: str,
    token: str,
    config: dict[str, Any] | None = None,
) -> Channel:
    """Connects a Page, Instagram account or WhatsApp number, or re-connects
    one that was linked before (which rotates its token)."""
    existing = await find_by_external(session, type_, external_id)
    if existing is not None and existing.shop_id != shop_id:
        # The same Page cannot be claimed by two shops.
        from app.core.errors import Conflict

        raise Conflict("That account is already connected to another shop.")
    if existing is None:
        existing = Channel(shop_id=shop_id, type=type_, external_id=external_id)
        session.add(existing)
    existing.name = name[:160]
    existing.token_encrypted = encrypt_secret(token)
    existing.is_active = True
    if config:
        existing.config = {**existing.config, **config}
    await session.flush()
    return existing


def rotate_widget_key(channel: Channel) -> None:
    channel.external_id = f"wk_{new_token(18)}"


def channel_public(channel: Channel) -> dict[str, Any]:
    token = channel_token(channel)
    return {
        "id": str(channel.id),
        "type": channel.type.value,
        "name": channel.name,
        "external_id": channel.external_id,
        "is_active": channel.is_active,
        "has_token": bool(channel.token_encrypted),
        "token_mask": mask_secret(token),
        "config": {k: v for k, v in channel.config.items() if "secret" not in k},
        "created_at": channel.created_at,
    }
