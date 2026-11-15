"""Facebook Messenger and Instagram.

Both use the same Page webhook shape and the same Send API, so one adapter
serves both. Replies to a customer message inside the 24-hour window use
messaging_type RESPONSE, which is the only type an automated reply needs.
"""

import logging
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.http import http_client
from app.modules.channels.adapters.base import (
    InboundAttachment,
    InboundComment,
    InboundKind,
    InboundMessage,
    OutboundResult,
)
from app.modules.channels.models import ChannelType

log = logging.getLogger("app.channels.meta")
settings = get_settings()

_ATTACHMENT_KIND = {
    "image": InboundKind.IMAGE,
    "audio": InboundKind.AUDIO,
    "video": InboundKind.FILE,
    "file": InboundKind.FILE,
}


def _graph(path: str) -> str:
    return f"{settings.META_GRAPH_BASE}/{settings.META_GRAPH_VERSION}/{path}"


async def send_text(token: str, recipient_id: str, text: str) -> OutboundResult:
    """Sends up to 2000 characters to one person. Longer replies are split."""
    chunks = _split(text, 1900)
    last = OutboundResult()
    for chunk in chunks:
        try:
            response = await http_client().post(
                _graph("me/messages"),
                params={"access_token": token},
                json={
                    "recipient": {"id": recipient_id},
                    "messaging_type": "RESPONSE",
                    "message": {"text": chunk},
                },
                timeout=20,
            )
        except httpx.HTTPError as exc:
            log.warning("Messenger send failed: %s", type(exc).__name__)
            return OutboundResult(ok=False, error="network")
        if response.status_code >= 300:
            log.warning("Messenger send rejected: HTTP %s %s", response.status_code,
                        response.text[:200])
            return OutboundResult(ok=False, error=f"http_{response.status_code}")
        last = OutboundResult(external_id=str(response.json().get("message_id", "")))
    return last


async def send_comment_reply(token: str, comment_id: str, text: str) -> OutboundResult:
    try:
        response = await http_client().post(
            _graph(f"{comment_id}/comments"),
            params={"access_token": token},
            json={"message": text[:800]},
            timeout=20,
        )
    except httpx.HTTPError:
        return OutboundResult(ok=False, error="network")
    if response.status_code >= 300:
        return OutboundResult(ok=False, error=f"http_{response.status_code}")
    return OutboundResult(external_id=str(response.json().get("id", "")))


async def send_private_reply(token: str, comment_id: str, text: str) -> OutboundResult:
    """A one-time private message to someone who commented on a post."""
    try:
        response = await http_client().post(
            _graph("me/messages"),
            params={"access_token": token},
            json={"recipient": {"comment_id": comment_id}, "message": {"text": text[:1900]}},
            timeout=20,
        )
    except httpx.HTTPError:
        return OutboundResult(ok=False, error="network")
    if response.status_code >= 300:
        return OutboundResult(ok=False, error=f"http_{response.status_code}")
    return OutboundResult(external_id=str(response.json().get("message_id", "")))


async def fetch_profile_name(token: str, user_id: str) -> str:
    try:
        response = await http_client().get(
            _graph(user_id), params={"fields": "name", "access_token": token}, timeout=15
        )
        if response.status_code < 300:
            return str(response.json().get("name", ""))
    except httpx.HTTPError:
        pass
    return ""


def parse_messages(entry_type: ChannelType, body: dict[str, Any]) -> list[InboundMessage]:
    """Reads a Messenger or Instagram webhook into normalised messages.

    Echoes, delivery receipts and read receipts are ignored; only real
    inbound messages come back.
    """
    messages: list[InboundMessage] = []
    for entry in body.get("entry", []):
        page_id = str(entry.get("id", ""))
        for event in entry.get("messaging", []):
            message = event.get("message")
            if not message or message.get("is_echo"):
                continue
            sender = str(event.get("sender", {}).get("id", ""))
            if not sender:
                continue
            attachments = []
            kind = InboundKind.TEXT
            for item in message.get("attachments", []) or []:
                a_kind = _ATTACHMENT_KIND.get(item.get("type", ""), InboundKind.OTHER)
                url = (item.get("payload") or {}).get("url", "")
                attachments.append(InboundAttachment(kind=a_kind, url=url))
                if kind == InboundKind.TEXT:
                    kind = a_kind
            messages.append(
                InboundMessage(
                    channel_external_id=page_id,
                    sender_id=sender,
                    external_id=str(message.get("mid", "")),
                    text=str(message.get("text", "")),
                    kind=kind,
                    attachments=attachments,
                    timestamp=int(event.get("timestamp", 0)),
                    raw=event,
                )
            )
    return messages


def parse_comments(body: dict[str, Any]) -> list[InboundComment]:
    """Reads `feed`/`comments` change events into normalised comments."""
    comments: list[InboundComment] = []
    for entry in body.get("entry", []):
        page_id = str(entry.get("id", ""))
        for change in entry.get("changes", []):
            value = change.get("value", {}) or {}
            if change.get("field") not in ("feed", "comments"):
                continue
            if value.get("item") not in ("comment", None) or value.get("verb") not in ("add", None):
                continue
            comment_id = str(value.get("comment_id") or value.get("id") or "")
            text = str(value.get("message", ""))
            if not comment_id or not text:
                continue
            author = value.get("from", {}) or {}
            comments.append(
                InboundComment(
                    channel_external_id=page_id,
                    comment_id=comment_id,
                    post_id=str(value.get("post_id", "")),
                    author_id=str(author.get("id", "")),
                    author_name=str(author.get("name", "")),
                    text=text,
                    raw=value,
                )
            )
    return comments


def _split(text: str, size: int) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text] if text else [""]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > size and current:
            parts.append(current.strip())
            current = ""
        current += line + "\n"
    if current.strip():
        parts.append(current.strip())
    return parts or [text[:size]]
