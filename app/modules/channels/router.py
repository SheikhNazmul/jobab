"""Connecting and managing the shop's channels."""

import uuid
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.core.utils import client_ip
from app.db.session import SessionDep
from app.modules.channels import service as channels
from app.modules.channels.models import ChannelType
from app.modules.shops.service import audit
from app.modules.users.deps import AuthDep, ManagerDep

settings = get_settings()
router = APIRouter(prefix="/api/channels", tags=["channels"])


class MetaConnectIn(BaseModel):
    type: Literal["messenger", "instagram", "whatsapp"]
    external_id: str = Field(min_length=1, max_length=160)
    name: str = Field(default="", max_length=160)
    token: str = Field(min_length=10, max_length=600)


class ChannelPatch(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    is_active: bool | None = None
    token: str | None = Field(default=None, max_length=600)


@router.get("")
async def list_(auth: AuthDep, session: SessionDep):
    rows = await channels.list_channels(session, auth.shop.id)
    return {"channels": [channels.channel_public(c) for c in rows]}


@router.get("/widget-code")
async def widget_code(auth: AuthDep, session: SessionDep):
    channel = await channels.webchat_channel(session, auth.shop.id)
    if channel is None:
        raise NotFound("No website chat channel.")
    snippet = (
        f'<script src="{settings.base_url}/widget.js" '
        f'data-key="{channel.external_id}" defer></script>'
    )
    return {"key": channel.external_id, "snippet": snippet}


@router.post("/meta", status_code=201)
async def connect_meta(
    body: MetaConnectIn, request: Request, auth: ManagerDep, session: SessionDep
):
    channel = await channels.connect_meta_channel(
        session, auth.shop.id,
        type_=ChannelType(body.type), external_id=body.external_id.strip(),
        name=body.name or body.type.title(), token=body.token,
    )
    await audit(session, "channel.connected", shop_id=auth.shop.id, user_id=auth.user.id,
                target=body.type, detail={"external_id": body.external_id}, ip=client_ip(request))
    return channels.channel_public(channel)


@router.patch("/{channel_id}")
async def patch(channel_id: uuid.UUID, body: ChannelPatch, auth: ManagerDep, session: SessionDep):
    channel = await channels.get_channel(session, auth.shop.id, channel_id)
    if channel is None:
        raise NotFound("Channel not found.")
    if body.name is not None:
        channel.name = body.name[:160]
    if body.is_active is not None:
        channel.is_active = body.is_active
    if body.token:
        from app.core.security import encrypt_secret

        if channel.type == ChannelType.WEBCHAT:
            raise AppError("Website chat has no access token.", code="no_token")
        channel.token_encrypted = encrypt_secret(body.token)
    await session.flush()
    return channels.channel_public(channel)


@router.post("/{channel_id}/rotate-key")
async def rotate_key(channel_id: uuid.UUID, auth: ManagerDep, session: SessionDep):
    channel = await channels.get_channel(session, auth.shop.id, channel_id)
    if channel is None or channel.type != ChannelType.WEBCHAT:
        raise NotFound("No such website chat channel.")
    channels.rotate_widget_key(channel)
    await session.flush()
    return channels.channel_public(channel)


@router.delete("/{channel_id}")
async def delete(channel_id: uuid.UUID, auth: ManagerDep, session: SessionDep):
    channel = await channels.get_channel(session, auth.shop.id, channel_id)
    if channel is None:
        raise NotFound("Channel not found.")
    if channel.type == ChannelType.WEBCHAT:
        raise AppError("The website chat channel cannot be removed.", code="keep_webchat")
    await session.delete(channel)
    return {"ok": True}
