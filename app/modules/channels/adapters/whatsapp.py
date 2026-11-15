"""WhatsApp Cloud API.

Replies are plain text sent inside the 24-hour customer service window, which
opens when the customer messages the business. Reaching a customer outside
that window needs an approved template and is a separate, paid path this
assistant does not use on its own.
"""

import logging
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.http import http_client
from app.modules.channels.adapters.base import (
    InboundAttachment,
    InboundKind,
    InboundMessage,
    OutboundResult,
)

log = logging.getLogger("app.channels.whatsapp")
settings = get_settings()

_TYPE_KIND = {
    "image": InboundKind.IMAGE,
    "audio": InboundKind.AUDIO,
    "voice": InboundKind.AUDIO,
    "video": InboundKind.FILE,
    "document": InboundKind.FILE,
    "sticker": InboundKind.IMAGE,
}


def _graph(path: str) -> str:
    return f"{settings.META_GRAPH_BASE}/{settings.META_GRAPH_VERSION}/{path}"


async def send_text(token: str, phone_number_id: str, recipient: str, text: str) -> OutboundResult:
    try:
        response = await http_client().post(
            _graph(f"{phone_number_id}/messages"),
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": recipient,
                "type": "text",
                "text": {"preview_url": False, "body": text[:4000]},
            },
            timeout=20,
        )
    except httpx.HTTPError as exc:
        log.warning("WhatsApp send failed: %s", type(exc).__name__)
        return OutboundResult(ok=False, error="network")
    if response.status_code >= 300:
        log.warning("WhatsApp send rejected: HTTP %s %s", response.status_code, response.text[:200])
        return OutboundResult(ok=False, error=f"http_{response.status_code}")
    data = response.json()
    message_id = ""
    if data.get("messages"):
        message_id = str(data["messages"][0].get("id", ""))
    return OutboundResult(external_id=message_id)


def parse_messages(body: dict[str, Any]) -> list[InboundMessage]:
    """Reads a WhatsApp Cloud webhook. Status updates (sent/delivered/read)
    carry no `messages` array and produce nothing here."""
    messages: list[InboundMessage] = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {}) or {}
            if change.get("field") != "messages":
                continue
            phone_number_id = str((value.get("metadata") or {}).get("phone_number_id", ""))
            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                for c in value.get("contacts", []) or []
            }
            for item in value.get("messages", []) or []:
                sender = str(item.get("from", ""))
                msg_type = item.get("type", "text")
                text = ""
                kind = InboundKind.TEXT
                attachments: list[InboundAttachment] = []
                if msg_type == "text":
                    text = str((item.get("text") or {}).get("body", ""))
                elif msg_type in ("button", "interactive"):
                    text = _interactive_text(item)
                else:
                    kind = _TYPE_KIND.get(msg_type, InboundKind.OTHER)
                    media = item.get(msg_type) or {}
                    text = str(media.get("caption", ""))
                    if media.get("id"):
                        attachments.append(
                            InboundAttachment(kind=kind, url=f"wa:{media['id']}",
                                              mime=str(media.get("mime_type", "")))
                        )
                messages.append(
                    InboundMessage(
                        channel_external_id=phone_number_id,
                        sender_id=sender,
                        external_id=str(item.get("id", "")),
                        text=text,
                        kind=kind,
                        attachments=attachments,
                        sender_name=names.get(sender, ""),
                        sender_phone=sender,
                        timestamp=int(item.get("timestamp", 0)),
                        raw=item,
                    )
                )
    return messages


def parse_statuses(body: dict[str, Any]) -> list[tuple[str, str]]:
    """Returns (message_id, status) pairs from delivery receipts."""
    out: list[tuple[str, str]] = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {}) or {}
            for status in value.get("statuses", []) or []:
                out.append((str(status.get("id", "")), str(status.get("status", ""))))
    return out


def _interactive_text(item: dict[str, Any]) -> str:
    if item.get("type") == "button":
        return str((item.get("button") or {}).get("text", ""))
    interactive = item.get("interactive") or {}
    for key in ("button_reply", "list_reply"):
        if key in interactive:
            return str(interactive[key].get("title", ""))
    return ""


async def fetch_media(token: str, media_id: str) -> tuple[bytes, str] | None:
    """Downloads a voice note or image: first its URL, then the bytes. Both
    calls need the token, so media never leaks to anyone without it."""
    try:
        meta = await http_client().get(
            _graph(media_id), headers={"Authorization": f"Bearer {token}"}, timeout=15
        )
        if meta.status_code >= 300:
            return None
        url = meta.json().get("url")
        if not url:
            return None
        binary = await http_client().get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=30
        )
        if binary.status_code >= 300:
            return None
        return binary.content, meta.json().get("mime_type", "application/octet-stream")
    except httpx.HTTPError:
        return None
