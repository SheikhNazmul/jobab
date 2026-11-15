"""Delivering an already-recorded reply to the customer on their channel.

The reply is stored first (with its credit spent) and delivered here. Website
chat has no outside service to call: the visitor's open tab receives the reply
over the event stream, so delivery just marks it sent.
"""

import logging
import uuid

from app.core.events import bus
from app.db.session import SessionLocal
from app.modules.channels.adapters import meta, whatsapp
from app.modules.channels.models import ChannelType
from app.modules.inbox.models import Message

log = logging.getLogger("app.channels.deliver")


async def deliver_reply(
    channel_type: ChannelType,
    channel_external_id: str,
    token: str,
    recipient: str,
    reply_id: uuid.UUID,
) -> None:
    async with SessionLocal() as session:
        message = await session.get(Message, reply_id)
        if message is None:
            return

        if channel_type == ChannelType.WEBCHAT:
            result_id, ok, error = "", True, ""
        elif channel_type in (ChannelType.MESSENGER, ChannelType.INSTAGRAM):
            res = await meta.send_text(token, recipient, message.text)
            result_id, ok, error = res.external_id, res.ok, res.error
        elif channel_type == ChannelType.WHATSAPP:
            res = await whatsapp.send_text(token, channel_external_id, recipient, message.text)
            result_id, ok, error = res.external_id, res.ok, res.error
        else:
            result_id, ok, error = "", False, "unknown_channel"

        message.status = "sent" if ok else "failed"
        if result_id:
            message.external_id = result_id
        if not ok:
            message.error = error
            log.warning("Reply %s not delivered on %s: %s", reply_id, channel_type.value, error)
        await session.commit()

        payload = {
            "conversation_id": str(message.conversation_id),
            "message_id": str(message.id),
            "status": message.status,
        }
        bus.publish(f"shop:{message.shop_id}", "message_status", payload)
        bus.publish(f"conversation:{message.conversation_id}", "message_status", payload)
