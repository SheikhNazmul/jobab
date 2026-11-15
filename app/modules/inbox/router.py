"""The shared inbox: conversations, their messages, human takeover and the
dashboard's live event stream.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.errors import NotFound
from app.core.events import sse_stream
from app.core.utils import clean_text
from app.db.session import SessionDep
from app.modules.channels import service as channels
from app.modules.channels.adapters import deliver_reply
from app.modules.inbox import service as inbox
from app.modules.inbox.models import (
    Contact,
    Conversation,
    ConversationStatus,
    Direction,
    Message,
    Sender,
)
from app.modules.shops.models import Shop
from app.modules.shops.schemas import ShopSettings
from app.modules.users.deps import AuthContext, AuthDep, auth_for_stream

router = APIRouter(prefix="/api", tags=["inbox"])


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class AiToggleIn(BaseModel):
    enabled: bool


class AssignIn(BaseModel):
    user_id: uuid.UUID | None = None


async def _load(session: SessionDep, auth: AuthContext, convo_id: uuid.UUID):
    row = (
        await session.execute(
            select(Conversation, Contact, channels.Channel)
            .join(Contact, Contact.id == Conversation.contact_id)
            .join(channels.Channel, channels.Channel.id == Conversation.channel_id)
            .where(Conversation.id == convo_id, Conversation.shop_id == auth.shop.id)
        )
    ).first()
    if row is None:
        raise NotFound("That conversation was not found.")
    return row


@router.get("/conversations")
async def list_conversations(
    auth: AuthDep,
    session: SessionDep,
    filter: Literal["all", "open", "closed", "needs_human", "unread"] = "all",
    q: str = Query(default="", max_length=120),
    limit: int = Query(default=40, ge=1, le=100),
):
    stmt = (
        select(Conversation, Contact, channels.Channel)
        .join(Contact, Contact.id == Conversation.contact_id)
        .join(channels.Channel, channels.Channel.id == Conversation.channel_id)
        .where(Conversation.shop_id == auth.shop.id)
    )
    if filter == "open":
        stmt = stmt.where(Conversation.status == ConversationStatus.OPEN)
    elif filter == "closed":
        stmt = stmt.where(Conversation.status == ConversationStatus.CLOSED)
    elif filter == "needs_human":
        stmt = stmt.where(Conversation.needs_human.is_(True))
    elif filter == "unread":
        stmt = stmt.where(Conversation.unread > 0)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(Contact.name.ilike(like) | Contact.phone.ilike(like)
                          | Conversation.last_preview.ilike(like))
    stmt = stmt.order_by(Conversation.last_message_at.desc()).limit(limit)
    rows = await session.execute(stmt)
    return {"items": [inbox.conversation_dict(c, ct, ch) for c, ct, ch in rows]}


@router.get("/conversations/{convo_id}")
async def get_conversation(convo_id: uuid.UUID, auth: AuthDep, session: SessionDep):
    convo, contact, channel = await _load(session, auth, convo_id)
    return inbox.conversation_dict(convo, contact, channel)


@router.get("/conversations/{convo_id}/messages")
async def get_messages(
    convo_id: uuid.UUID,
    auth: AuthDep,
    session: SessionDep,
    before_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=60, ge=1, le=100),
):
    await _load(session, auth, convo_id)
    stmt = select(Message).where(Message.conversation_id == convo_id)
    if before_seq:
        stmt = stmt.where(Message.seq < before_seq)
    rows = await session.scalars(stmt.order_by(Message.seq.desc()).limit(limit))
    messages = [inbox.message_dict(m) for m in reversed(list(rows))]
    await inbox.mark_read(session, auth.shop.id, convo_id)
    return {"messages": messages}


@router.post("/conversations/{convo_id}/reply")
async def send_reply(convo_id: uuid.UUID, body: ReplyIn, auth: AuthDep, session: SessionDep):
    convo, contact, channel = await _load(session, auth, convo_id)
    settings_model = ShopSettings.model_validate((await session.get(Shop, auth.shop.id)).settings)

    message = await inbox.add_message(
        session, convo,
        direction=Direction.OUT, sender=Sender.AGENT, user_id=auth.user.id,
        text=clean_text(body.text, 4000), status="queued",
    )
    # A person has spoken, so the AI stays quiet for a while and the chat is no
    # longer waiting on a human.
    inbox.pause_ai(convo, settings_model.ai.pause_minutes)
    convo.needs_human = False
    convo.needs_human_reason = ""
    convo.unread = 0
    token = channels.channel_token(channel)
    recipient = contact.external_id
    channel_type = channel.type
    channel_external_id = channel.external_id
    await session.commit()

    await deliver_reply(channel_type, channel_external_id, token, recipient, message.id)
    inbox.notify_shop(auth.shop.id, "message",
                      {"conversation_id": str(convo_id), "message": inbox.message_dict(message)})
    return {"ok": True, "message": inbox.message_dict(message)}


@router.post("/conversations/{convo_id}/ai")
async def toggle_ai(convo_id: uuid.UUID, body: AiToggleIn, auth: AuthDep, session: SessionDep):
    convo, _, _ = await _load(session, auth, convo_id)
    convo.ai_enabled = body.enabled
    if body.enabled:
        convo.ai_paused_until = None
        convo.needs_human = False
        convo.needs_human_reason = ""
    inbox.notify_shop(auth.shop.id, "conversation_update",
                      {"conversation_id": str(convo_id), "ai_enabled": body.enabled})
    return {"ok": True, "ai_enabled": convo.ai_enabled}


@router.post("/conversations/{convo_id}/resolve")
async def resolve(convo_id: uuid.UUID, auth: AuthDep, session: SessionDep):
    convo, _, _ = await _load(session, auth, convo_id)
    convo.needs_human = False
    convo.needs_human_reason = ""
    convo.unread = 0
    return {"ok": True}


@router.post("/conversations/{convo_id}/status")
async def set_status(
    convo_id: uuid.UUID,
    auth: AuthDep,
    session: SessionDep,
    status: Annotated[Literal["open", "closed"], Query()],
):
    convo, _, _ = await _load(session, auth, convo_id)
    convo.status = ConversationStatus(status)
    return {"ok": True, "status": convo.status.value}


@router.post("/conversations/{convo_id}/assign")
async def assign(convo_id: uuid.UUID, body: AssignIn, auth: AuthDep, session: SessionDep):
    convo, _, _ = await _load(session, auth, convo_id)
    if body.user_id is not None:
        from app.modules.shops.models import Membership

        member = await session.scalar(
            select(Membership).where(
                Membership.shop_id == auth.shop.id, Membership.user_id == body.user_id
            )
        )
        if member is None:
            raise NotFound("That team member is not on this shop.")
    convo.assigned_user_id = body.user_id
    return {"ok": True}


@router.post("/conversations/{convo_id}/read")
async def mark_read(convo_id: uuid.UUID, auth: AuthDep, session: SessionDep):
    await _load(session, auth, convo_id)
    await inbox.mark_read(session, auth.shop.id, convo_id)
    return {"ok": True}


@router.get("/stream")
async def dashboard_stream(auth: Annotated[AuthContext, Depends(auth_for_stream)]):
    """Live events for the signed-in shop: new messages, orders, hand-offs."""
    return StreamingResponse(
        sse_stream(f"shop:{auth.shop.id}"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
