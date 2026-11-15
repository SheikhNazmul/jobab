"""The website chat widget's own API.

A visitor on the shop's website gets a random visitor token the first time
they open the chat. The browser keeps it and sends it back as a header; the
server stores only its hash. These routes are open to the public (no sign-in)
and are rate limited and CSRF-exempt, since the token in the header, not a
cookie, says who the visitor is.
"""

import uuid

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.errors import NotFound, Unauthorized
from app.core.events import sse_stream
from app.core.ratelimit import enforce
from app.core.security import new_token, token_hash
from app.core.utils import clean_text, client_ip
from app.db.session import SessionDep, StreamingSessionDep
from app.modules.channels import ingest as ingest_service
from app.modules.channels import service as channels
from app.modules.channels.adapters.base import InboundMessage
from app.modules.channels.models import Channel
from app.modules.inbox import service as inbox
from app.modules.inbox.models import Contact, Conversation, Direction, Message
from app.modules.shops.models import Shop
from app.modules.shops.schemas import ShopSettings

router = APIRouter(prefix="/api/widget", tags=["widget"])


class StartIn(BaseModel):
    visitor_token: str | None = Field(default=None, max_length=120)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


async def _channel(session: SessionDep, key: str) -> Channel:
    channel = await channels.find_by_widget_key(session, key)
    if channel is None or not channel.is_active:
        raise NotFound("This chat is not available.")
    return channel


async def _contact_from_token(
    session: SessionDep, channel: Channel, token: str | None
) -> Contact | None:
    if not token:
        return None
    return await session.scalar(
        select(Contact).where(
            Contact.channel_id == channel.id, Contact.token_hash == token_hash(token)
        )
    )


def _widget_config(shop: Shop) -> dict:
    s = ShopSettings.model_validate(shop.settings or {})
    return {
        "shop_name": shop.name,
        "title": shop.name,
        "greeting": s.business.description[:200] if s.business.description else "",
    }


@router.post("/{key}/session")
async def start(key: str, body: StartIn, request: Request, session: SessionDep):
    enforce(f"widget-start:{client_ip(request)}", 30, 300)
    channel = await _channel(session, key)
    shop = await session.get(Shop, channel.shop_id)

    contact = await _contact_from_token(session, channel, body.visitor_token)
    token = body.visitor_token or ""
    if contact is None:
        token = new_token(18)
        contact = Contact(
            shop_id=channel.shop_id,
            channel_id=channel.id,
            external_id=f"web_{new_token(12)}",
            token_hash=token_hash(token),
        )
        session.add(contact)
        await session.flush()
    convo = await inbox.get_or_create_conversation(session, contact)

    rows = await session.scalars(
        select(Message)
        .where(Message.conversation_id == convo.id, Message.direction != Direction.INTERNAL)
        .order_by(Message.seq.desc())
        .limit(40)
    )
    history = [inbox.message_dict(m) for m in reversed(list(rows))]
    return {
        "visitor_token": token,
        "conversation_id": str(convo.id),
        "config": _widget_config(shop),
        "messages": history,
    }


@router.post("/{key}/message")
async def send(
    key: str,
    body: MessageIn,
    request: Request,
    session: SessionDep,
    x_visitor_token: str = Header(default=""),
):
    enforce(f"widget-msg:{client_ip(request)}", 40, 300)
    channel = await _channel(session, key)
    contact = await _contact_from_token(session, channel, x_visitor_token)
    if contact is None:
        raise Unauthorized("Start a chat session first.")

    inbound = InboundMessage(
        channel_external_id=channel.external_id,
        sender_id=contact.external_id,
        external_id="",  # website messages have no external id; never duplicated
        text=clean_text(body.text, 4000),
    )
    result = await ingest_service.ingest(session, channel, inbound)
    await session.commit()
    if result is None:
        return {"ok": True}
    convo, message = result
    ingest_service.announce_and_schedule(convo, message)
    return {"ok": True, "message": inbox.message_dict(message)}


@router.get("/{key}/messages")
async def poll(
    key: str,
    session: SessionDep,
    after_seq: int = 0,
    x_visitor_token: str = Header(default=""),
):
    """Fallback for browsers or networks where the live stream will not hold."""
    channel = await _channel(session, key)
    contact = await _contact_from_token(session, channel, x_visitor_token)
    if contact is None:
        raise Unauthorized("Start a chat session first.")
    convo = await session.scalar(
        select(Conversation).where(Conversation.contact_id == contact.id)
    )
    if convo is None:
        return {"messages": []}
    rows = await session.scalars(
        select(Message)
        .where(
            Message.conversation_id == convo.id,
            Message.seq > after_seq,
            Message.direction != Direction.INTERNAL,
        )
        .order_by(Message.seq)
        .limit(50)
    )
    return {"messages": [inbox.message_dict(m) for m in rows]}


@router.get("/{key}/stream")
async def stream(
    key: str,
    session: StreamingSessionDep,
    token: str = "",
):
    """Live updates for one visitor's chat. The visitor token is a query
    parameter here because EventSource cannot set headers."""
    channel = await _channel(session, key)
    contact = await _contact_from_token(session, channel, token)
    if contact is None:
        raise Unauthorized("Start a chat session first.")
    convo = await session.scalar(
        select(Conversation).where(Conversation.contact_id == contact.id)
    )
    if convo is None:
        raise NotFound("No chat yet.")
    convo_id: uuid.UUID = convo.id
    await session.commit()  # release the connection before the long stream
    return StreamingResponse(
        sse_stream(f"conversation:{convo_id}"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
