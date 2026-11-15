"""Scheduling and delivery of AI replies.

A customer often sends three quick messages in a row. Answering each one
separately is slow and clumsy, so a reply is scheduled a couple of seconds
after the last message: if another message lands first, the timer restarts and
they are answered together.

Delivery is split from generation. `generate_reply` records the reply and
spends the credit in one transaction; only after that commits do we hand the
text to the channel, so a failed send never silently charges a credit twice or
loses the record.
"""

import asyncio
import logging
import uuid

from app.core.config import get_settings
from app.core.events import bus
from app.db.session import SessionLocal
from app.modules.agent import runtime
from app.modules.channels import service as channels
from app.modules.channels.adapters import deliver_reply
from app.modules.inbox import service as inbox
from app.modules.inbox.models import Conversation, Message
from app.modules.shops.models import Shop

log = logging.getLogger("app.agent.dispatch")
settings = get_settings()

_pending: dict[uuid.UUID, asyncio.Task] = {}


def schedule_reply(conversation_id: uuid.UUID) -> None:
    """Debounced: restarts the timer if called again before it fires."""
    existing = _pending.get(conversation_id)
    if existing and not existing.done():
        existing.cancel()
    _pending[conversation_id] = asyncio.get_running_loop().create_task(
        _wait_then_reply(conversation_id), name=f"reply:{conversation_id}"
    )


async def _wait_then_reply(conversation_id: uuid.UUID) -> None:
    try:
        await asyncio.sleep(settings.REPLY_DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return
    try:
        await run_reply(conversation_id)
    except Exception:  # noqa: BLE001 - a background reply must never crash the worker
        log.exception("Reply failed for conversation %s", conversation_id)
    finally:
        _pending.pop(conversation_id, None)


async def run_reply(conversation_id: uuid.UUID) -> None:
    """Generates and delivers one reply. Opens its own session."""
    async with SessionLocal() as session:
        convo = await session.get(Conversation, conversation_id)
        if convo is None:
            return
        shop = await session.get(Shop, convo.shop_id)
        channel = await channels.get_channel(session, convo.shop_id, convo.channel_id)
        contact = await _contact(session, convo)
        if not shop or not channel or not contact:
            return

        result = await runtime.generate_reply(session, shop, convo, contact, channel)
        if result.outcome.handoff:
            inbox.notify_shop(convo.shop_id, "handoff", {
                "conversation_id": str(convo.id), "reason": result.outcome.handoff_reason,
            })
        for name, data in result.outcome.events:
            inbox.notify_shop(convo.shop_id, name, data)
        if result.reply is None:
            await session.commit()
            return

        reply_id = result.reply.id
        shop_id = convo.shop_id
        convo_id = convo.id
        # Build the message's view while the row is still live (commit expires it),
        # so we can broadcast the reply's content after committing.
        reply_payload = {
            "conversation_id": str(convo_id),
            "message": inbox.message_dict(result.reply),
        }
        token = channels.channel_token(channel)
        recipient = contact.external_id
        channel_type = channel.type
        channel_external_id = channel.external_id
        await session.commit()  # credit spent and reply recorded; now deliver

    # Show the reply live: the dashboard (shop topic) and the website-chat tab
    # (conversation topic). Meta channels ignore this; their customer is not on SSE.
    bus.publish(f"shop:{shop_id}", "message", reply_payload)
    bus.publish(f"conversation:{convo_id}", "message", reply_payload)

    # Delivery happens outside the write transaction.
    await deliver_reply(channel_type, channel_external_id, token, recipient, reply_id)


async def _contact(session, convo: Conversation):
    from app.modules.inbox.models import Contact

    return await session.get(Contact, convo.contact_id)


def publish_new_message(convo: Conversation, message: Message) -> None:
    """Tells the dashboard and any open website-chat tab about a message."""
    payload = {"conversation_id": str(convo.id), "message": inbox.message_dict(message)}
    bus.publish(f"shop:{convo.shop_id}", "message", payload)
    bus.publish(f"conversation:{convo.id}", "message", payload)
