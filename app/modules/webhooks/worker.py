"""Processing stored webhook events, one at a time, off the request path.

Meta expects a fast 200 from the webhook, so the endpoint only stores the raw
event. This worker drains those events: it parses each one, stores the
customer messages, schedules AI replies, and handles public comments. Storing
first means a crash loses nothing; the message-level de-duplication means a
repeat delivery is harmless.
"""

import asyncio
import hashlib
import json
import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.utils import utcnow
from app.db.session import SessionLocal
from app.modules.automation.models import EventStatus, WebhookEvent
from app.modules.channels import ingest as ingest_service
from app.modules.channels import service as channels
from app.modules.channels.adapters import meta, whatsapp
from app.modules.channels.adapters.base import InboundComment
from app.modules.channels.models import ChannelType
from app.modules.crm.models import CommentLog
from app.modules.inbox import service as inbox

log = logging.getLogger("app.webhooks.worker")
settings = get_settings()

_OBJECT_TO_TYPE = {
    "page": ChannelType.MESSENGER,
    "instagram": ChannelType.INSTAGRAM,
    "whatsapp_business_account": ChannelType.WHATSAPP,
}


def event_key(object_type: str, raw: bytes) -> str:
    return f"{object_type}:{hashlib.sha256(raw).hexdigest()}"


async def store_event(session: AsyncSession, object_type: str, raw: bytes) -> bool:
    """Saves a raw webhook for the worker. Returns False if we already have it."""
    from sqlalchemy.dialects.postgresql import insert

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False
    result = await session.execute(
        insert(WebhookEvent)
        .values(
            event_key=event_key(object_type, raw),
            payload={"object": object_type, "body": payload},
        )
        .on_conflict_do_nothing(index_elements=["event_key"])
    )
    return result.rowcount > 0


async def drain_once(limit: int = 20) -> int:
    """Processes up to `limit` pending events. Each is marked done only after
    it is handled, so a crash mid-event leaves it pending to retry; the
    message-level de-duplication makes a retry harmless."""
    async with SessionLocal() as session:
        events = list(
            await session.scalars(
                select(WebhookEvent)
                .where(WebhookEvent.status == EventStatus.PENDING)
                .order_by(WebhookEvent.created_at)
                .limit(limit)
            )
        )

    handled = 0
    for event in events:
        status, error = EventStatus.DONE, ""
        try:
            await _process(event.payload)
            handled += 1
        except Exception as exc:  # noqa: BLE001 - one bad event must not stop the rest
            log.exception("Webhook event %s failed", event.id)
            status, error = EventStatus.FAILED, str(exc)[:500]
        async with SessionLocal() as session:
            await session.execute(
                update(WebhookEvent)
                .where(WebhookEvent.id == event.id)
                .values(status=status, error=error, processed_at=utcnow(),
                        attempts=WebhookEvent.attempts + 1)
            )
            await session.commit()
    return handled


async def _process(payload: dict[str, Any]) -> None:
    object_type = payload.get("object", "")
    body = payload.get("body", {})
    channel_type = _OBJECT_TO_TYPE.get(object_type)
    if channel_type is None:
        return

    if channel_type == ChannelType.WHATSAPP:
        inbound = whatsapp.parse_messages(body)
        statuses = whatsapp.parse_statuses(body)
    else:
        inbound = meta.parse_messages(channel_type, body)
        statuses = []

    to_schedule: list[tuple[Any, Any]] = []
    async with SessionLocal() as session:
        for message in inbound:
            channel = await channels.find_by_external(
                session, channel_type, message.channel_external_id
            )
            if channel is None or not channel.is_active:
                continue
            result = await ingest_service.ingest(session, channel, message)
            if result is not None:
                to_schedule.append(result)
        await _apply_statuses(session, statuses)
        await session.commit()

    # After commit: tell the dashboard and schedule the replies.
    for convo, message in to_schedule:
        ingest_service.announce_and_schedule(convo, message)

    # Comments are handled separately (Facebook + Instagram feed changes).
    if channel_type in (ChannelType.MESSENGER, ChannelType.INSTAGRAM):
        for comment in meta.parse_comments(body):
            await _handle_comment(channel_type, comment)


async def _apply_statuses(session: AsyncSession, statuses: list[tuple[str, str]]) -> None:
    from app.modules.inbox.models import Message

    rank = {"queued": 0, "sent": 1, "delivered": 2, "read": 3, "failed": 4}
    for external_id, status in statuses:
        if not external_id:
            continue
        message = await session.scalar(select(Message).where(Message.external_id == external_id))
        if message and rank.get(status, 0) > rank.get(message.status, 0):
            message.status = status


async def _handle_comment(channel_type: ChannelType, comment: InboundComment) -> None:
    async with SessionLocal() as session:
        channel = await channels.find_by_external(
            session, channel_type, comment.channel_external_id
        )
        if channel is None or not channel.is_active:
            return
        existing = await session.scalar(
            select(CommentLog).where(
                CommentLog.channel_id == channel.id, CommentLog.external_id == comment.comment_id
            )
        )
        if existing is not None:
            return
        from app.modules.shops.models import Shop
        from app.modules.shops.schemas import ShopSettings

        shop = await session.get(Shop, channel.shop_id)
        shop_settings = ShopSettings.model_validate(shop.settings or {})
        log_row = CommentLog(
            shop_id=channel.shop_id, channel_id=channel.id, external_id=comment.comment_id,
            post_id=comment.post_id, author_id=comment.author_id,
            author_name=comment.author_name, text=comment.text,
        )
        session.add(log_row)

        token = channels.channel_token(channel)
        if shop_settings.comments.auto_reply and token:
            public = await meta.send_comment_reply(token, comment.comment_id,
                                                   shop_settings.comments.public_text)
            log_row.reply_text = shop_settings.comments.public_text if public.ok else ""
            if shop_settings.comments.private_reply:
                private = await meta.send_private_reply(token, comment.comment_id,
                                                        shop_settings.comments.private_text)
                log_row.private_text = shop_settings.comments.private_text if private.ok else ""
            log_row.status = "replied" if public.ok else "failed"
        await session.commit()
        inbox.notify_shop(channel.shop_id, "comment", {
            "id": str(log_row.id), "author_name": comment.author_name, "text": comment.text,
        })


async def worker_loop(stop: asyncio.Event) -> None:
    """Runs in the background for the life of the app."""
    log.info("Webhook worker started")
    while not stop.is_set():
        try:
            handled = await drain_once()
        except Exception:  # noqa: BLE001
            log.exception("Webhook drain failed")
            handled = 0
        # Busy? Keep going. Idle? Wait a second before looking again.
        if handled == 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except TimeoutError:
                pass
    log.info("Webhook worker stopped")
