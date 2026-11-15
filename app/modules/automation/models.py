import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, value_enum


class EventStatus(enum.StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    IGNORED = "ignored"


class WebhookEvent(Base):
    """An inbound platform event, stored before it is processed.

    Storing first means a crash does not lose a customer's message, and the
    unique key means a repeat delivery from Meta is recognised and skipped.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (Index("ix_webhook_events_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_key: Mapped[str] = mapped_column(String(255), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[EventStatus] = mapped_column(
        value_enum(EventStatus), default=EventStatus.PENDING, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageEvent(Base):
    """One spent credit, with the token counts the model reported."""

    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_shop_created", "shop_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column()
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
