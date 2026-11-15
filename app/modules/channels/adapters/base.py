"""Shared types for channel adapters.

An adapter turns a platform's own webhook into a list of `InboundMessage`, and
sends a reply with `send_text`. The agent and the webhook worker use only
these shapes, so they do not need to know which platform a chat is on.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class InboundKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    FILE = "file"
    OTHER = "other"


@dataclass
class InboundAttachment:
    kind: InboundKind
    url: str = ""
    mime: str = ""


@dataclass
class InboundMessage:
    """One message a customer sent, normalised across platforms."""

    channel_external_id: str  # Page id, IG account id or WhatsApp phone-number id
    sender_id: str  # the customer's id on that platform
    external_id: str  # the platform's message id, for de-duplication
    text: str = ""
    kind: InboundKind = InboundKind.TEXT
    attachments: list[InboundAttachment] = field(default_factory=list)
    sender_name: str = ""
    sender_phone: str = ""
    timestamp: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class InboundComment:
    """A public comment on a post (Facebook or Instagram)."""

    channel_external_id: str
    comment_id: str
    post_id: str
    author_id: str
    author_name: str
    text: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundResult:
    external_id: str = ""
    ok: bool = True
    error: str = ""
