import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimeStampMixin, value_enum


class Role(enum.StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    AGENT = "agent"


class Shop(Base, TimeStampMixin):
    """One business on the platform. Every other row belongs to a shop."""

    __tablename__ = "shops"
    __table_args__ = (
        CheckConstraint("credits_total >= 0", name="credits_total_non_negative"),
        CheckConstraint("credits_used >= 0", name="credits_used_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160))
    plan: Mapped[str] = mapped_column(String(40), default="trial", server_default="trial")
    # One credit = one AI reply, one suggested reply or one comment auto-reply.
    credits_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    credits_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    period_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_order_no: Mapped[int] = mapped_column(Integer, default=1001, server_default="1001")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Membership(Base, TimeStampMixin):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("shop_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[Role] = mapped_column(value_enum(Role))


class AuditLog(Base):
    """Who did what, for actions that matter to security or money."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shops.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(80), index=True)
    target: Mapped[str] = mapped_column(String(160), default="", server_default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    ip: Mapped[str] = mapped_column(String(64), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
