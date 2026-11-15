import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimeStampMixin, value_enum


class OrderStatus(enum.StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PACKED = "packed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class Order(Base, TimeStampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("shop_id", "number"),
        CheckConstraint("total >= 0", name="total_non_negative"),
        Index("ix_orders_shop_created", "shop_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"))
    number: Mapped[str] = mapped_column(String(40))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), index=True
    )
    channel_type: Mapped[str] = mapped_column(String(20), default="", server_default="")
    customer_name: Mapped[str] = mapped_column(String(160), default="", server_default="")
    phone: Mapped[str] = mapped_column(String(32), default="", server_default="", index=True)
    address: Mapped[str] = mapped_column(Text, default="", server_default="")
    zone_id: Mapped[str] = mapped_column(String(60), default="", server_default="")
    zone_name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    # Snapshot of what was sold: name, variant, unit price and quantity at the
    # time of the order. Later catalog edits do not change past orders.
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    delivery_charge: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    # False when the shop has not set delivery zones, so a person must confirm.
    delivery_confirmed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    status: Mapped[OrderStatus] = mapped_column(
        value_enum(OrderStatus), default=OrderStatus.PENDING, server_default="pending", index=True
    )
    payment_method: Mapped[str] = mapped_column(String(80), default="", server_default="")
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    courier: Mapped[str] = mapped_column(String(80), default="", server_default="")
    tracking_code: Mapped[str] = mapped_column(String(120), default="", server_default="")
    created_by: Mapped[str] = mapped_column(String(10), default="agent", server_default="agent")
    # True when the AI placed an order above the shop's review limit.
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class OrderQuote(Base):
    """A priced summary the AI showed the customer.

    The AI can only create an order from a quote made in the same chat a short
    time earlier. That is how "confirm before ordering" is enforced in code
    and not just asked for in the prompt.
    """

    __tablename__ = "order_quotes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    used: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
