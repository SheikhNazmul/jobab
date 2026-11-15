"""Conversations and messages: the shared helpers the channels, the agent and
the dashboard all use to read and write a chat.

Nothing here sends anything to a customer. Sending belongs to the channel
adapters; this module only records what was said and keeps the conversation
row (preview, unread, timestamps, hand-off flags) in step.
"""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import bus
from app.core.utils import utcnow
from app.modules.channels.models import Channel
from app.modules.inbox.models import (
    Contact,
    Conversation,
    ConversationStatus,
    Direction,
    Message,
    Sender,
)


async def get_or_create_contact(
    session: AsyncSession,
    channel: Channel,
    external_id: str,
    *,
    name: str = "",
    phone: str = "",
    token_hash: str | None = None,
) -> Contact:
    """Finds the person on this channel, or creates them. Safe against two
    webhook deliveries racing: the insert ignores a duplicate and we re-read."""
    stmt = (
        insert(Contact)
        .values(
            shop_id=channel.shop_id,
            channel_id=channel.id,
            external_id=external_id,
            name=name[:160],
            phone=phone[:32],
            token_hash=token_hash,
        )
        .on_conflict_do_nothing(index_elements=["channel_id", "external_id"])
    )
    await session.execute(stmt)
    contact = await session.scalar(
        select(Contact).where(Contact.channel_id == channel.id, Contact.external_id == external_id)
    )
    assert contact is not None  # noqa: S101 - just inserted or already there
    changed = False
    if name and contact.name != name[:160]:
        contact.name, changed = name[:160], True
    if phone and not contact.phone:
        contact.phone, changed = phone[:32], True
    contact.last_seen_at = utcnow()
    if changed:
        await session.flush()
    return contact


async def get_or_create_conversation(session: AsyncSession, contact: Contact) -> Conversation:
    convo = await session.scalar(
        select(Conversation).where(Conversation.contact_id == contact.id)
    )
    if convo is None:
        convo = Conversation(
            shop_id=contact.shop_id,
            channel_id=contact.channel_id,
            contact_id=contact.id,
            last_message_at=utcnow(),
        )
        session.add(convo)
        await session.flush()
    return convo


def _preview(text: str, kind: str) -> str:
    return (text or f"[{kind}]").strip().replace("\n", " ")[:200]


async def add_message(
    session: AsyncSession,
    convo: Conversation,
    *,
    direction: Direction,
    sender: Sender,
    text: str = "",
    kind: str = "text",
    user_id: uuid.UUID | None = None,
    attachments: list[dict[str, Any]] | None = None,
    external_id: str | None = None,
    status: str | None = None,
    meta: dict[str, Any] | None = None,
) -> Message | None:
    """Records one message and updates the conversation. Returns None when the
    same platform message was already stored (a repeat webhook delivery)."""
    if external_id:
        exists = await session.scalar(
            select(Message.id).where(
                Message.conversation_id == convo.id, Message.external_id == external_id
            )
        )
        if exists:
            return None

    now = utcnow()
    message = Message(
        shop_id=convo.shop_id,
        conversation_id=convo.id,
        direction=direction,
        sender=sender,
        user_id=user_id,
        kind=kind,
        text=text,
        attachments=attachments or [],
        external_id=external_id,
        status=status or ("received" if direction == Direction.IN else "queued"),
        meta=meta or {},
        created_at=now,
    )
    session.add(message)

    if direction != Direction.INTERNAL:
        convo.last_message_at = now
        convo.last_preview = _preview(text, kind)
        if direction == Direction.IN:
            convo.unread += 1
            convo.last_customer_at = now
            if convo.status == ConversationStatus.CLOSED:
                convo.status = ConversationStatus.OPEN
    await session.flush()
    return message


async def mark_read(session: AsyncSession, shop_id: uuid.UUID, convo_id: uuid.UUID) -> None:
    await session.execute(
        update(Conversation)
        .where(Conversation.id == convo_id, Conversation.shop_id == shop_id)
        .values(unread=0)
    )


def pause_ai(convo: Conversation, minutes: int) -> None:
    """Keeps the AI quiet after a person replies, so the two never talk over
    each other. minutes <= 0 means do not pause."""
    convo.ai_paused_until = utcnow() + timedelta(minutes=minutes) if minutes > 0 else None


def ai_is_muted(convo: Conversation) -> bool:
    if not convo.ai_enabled:
        return True
    return bool(convo.ai_paused_until and convo.ai_paused_until > utcnow())


async def set_handoff(
    session: AsyncSession, convo: Conversation, reason: str, *, needs_human: bool = True
) -> None:
    convo.needs_human = needs_human
    convo.needs_human_reason = reason[:255] if needs_human else ""
    await session.flush()


def message_dict(message: Message) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "seq": message.seq,
        "direction": message.direction.value,
        "sender": message.sender.value,
        "kind": message.kind,
        "text": message.text,
        "attachments": message.attachments,
        "status": message.status,
        "error": message.error,
        "created_at": message.created_at,
    }


def conversation_dict(convo: Conversation, contact: Contact, channel: Channel) -> dict[str, Any]:
    return {
        "id": str(convo.id),
        "channel": {"id": str(channel.id), "type": channel.type.value, "name": channel.name},
        "contact": {
            "id": str(contact.id),
            "name": contact.name or "Customer",
            "phone": contact.phone,
            "address": contact.address,
        },
        "status": convo.status.value,
        "ai_enabled": convo.ai_enabled,
        "ai_muted": ai_is_muted(convo),
        "needs_human": convo.needs_human,
        "needs_human_reason": convo.needs_human_reason,
        "assigned_user_id": str(convo.assigned_user_id) if convo.assigned_user_id else None,
        "labels": convo.labels,
        "unread": convo.unread,
        "last_preview": convo.last_preview,
        "last_message_at": convo.last_message_at,
        "last_customer_at": convo.last_customer_at,
    }


def notify_shop(shop_id: uuid.UUID, name: str, data: dict[str, Any]) -> None:
    bus.publish(f"shop:{shop_id}", name, data)


def notify_conversation(convo_id: uuid.UUID, name: str, data: dict[str, Any]) -> None:
    bus.publish(f"conversation:{convo_id}", name, data)
