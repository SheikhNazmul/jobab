"""Taking in a customer message, whichever channel it came from.

Both the Meta webhook worker and the website-chat endpoint call `ingest`. It
stores the message, tells the dashboard, and returns the conversation so the
caller can commit and then ask the agent to reply.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent import dispatch
from app.modules.channels.adapters.base import InboundMessage
from app.modules.channels.models import Channel
from app.modules.inbox import service as inbox
from app.modules.inbox.models import Conversation, Direction, Message, Sender

log = logging.getLogger("app.channels.ingest")


async def ingest(
    session: AsyncSession, channel: Channel, inbound: InboundMessage
) -> tuple[Conversation, Message] | None:
    """Stores one inbound message. Returns (conversation, message), or None if
    it was a duplicate delivery already seen."""
    contact = await inbox.get_or_create_contact(
        session, channel, inbound.sender_id,
        name=inbound.sender_name, phone=inbound.sender_phone,
    )
    convo = await inbox.get_or_create_conversation(session, contact)
    message = await inbox.add_message(
        session, convo,
        direction=Direction.IN, sender=Sender.CUSTOMER,
        text=inbound.text, kind=inbound.kind.value,
        attachments=[{"kind": a.kind.value, "url": a.url, "mime": a.mime}
                     for a in inbound.attachments],
        external_id=inbound.external_id or None,
    )
    if message is None:
        return None  # a repeat delivery of a message we already have
    return convo, message


def should_reply(convo: Conversation) -> bool:
    return convo.ai_enabled and not convo.needs_human and not inbox.ai_is_muted(convo)


def announce_and_schedule(convo: Conversation, message: Message) -> None:
    """After the caller commits: tell the dashboard and schedule the reply."""
    dispatch.publish_new_message(convo, message)
    if should_reply(convo):
        dispatch.schedule_reply(convo.id)
