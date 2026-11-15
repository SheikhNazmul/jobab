import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimeStampMixin, value_enum


class ConversationStatus(enum.StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class Direction(enum.StrEnum):
    IN = "in"  # from the customer
    OUT = "out"  # to the customer
    INTERNAL = "internal"  # team notes and system events, never sent


class Sender(enum.StrEnum):
    CUSTOMER = "customer"
    AI = "ai"
    AGENT = "agent"
    SYSTEM = "system"


class Contact(Base, TimeStampMixin):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("channel_id", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(160), default="", server_default="")
    phone: Mapped[str] = mapped_column(String(32), default="", server_default="")
    address: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Website-chat visitors prove who they are with a token; we keep its hash.
    token_hash: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Conversation(Base, TimeStampMixin):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_shop_last", "shop_id", "last_message_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"))
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[ConversationStatus] = mapped_column(
        value_enum(ConversationStatus), default=ConversationStatus.OPEN, server_default="open"
    )
    # The team's switch for this chat.
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Set when a person replies, so the AI does not talk over them.
    ai_paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set by the AI (hand-off, complaint) or by the system (no credits, AI error).
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    needs_human_reason: Mapped[str] = mapped_column(String(255), default="", server_default="")
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    labels: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    unread: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_customer_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_preview: Mapped[str] = mapped_column(String(255), default="", server_default="")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_seq", "conversation_id", "seq"),
        # A repeat delivery of the same platform message is rejected here.
        Index(
            "uq_messages_conversation_external",
            "conversation_id",
            "external_id",
            unique=True,
            postgresql_where="external_id IS NOT NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Strictly increasing, used to order messages and to ask "what is new".
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    direction: Mapped[Direction] = mapped_column(value_enum(Direction))
    sender: Mapped[Sender] = mapped_column(value_enum(Sender))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20), default="text", server_default="text")
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="received", server_default="received")
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
