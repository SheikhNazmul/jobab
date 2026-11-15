import enum
import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimeStampMixin, value_enum


class ChannelType(enum.StrEnum):
    WEBCHAT = "webchat"
    MESSENGER = "messenger"
    INSTAGRAM = "instagram"
    WHATSAPP = "whatsapp"


class Channel(Base, TimeStampMixin):
    """A connected place customers write to: a Page, an Instagram account,
    a WhatsApp number, or the shop's own website chat."""

    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("type", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[ChannelType] = mapped_column(value_enum(ChannelType))
    # Page id, Instagram account id, WhatsApp phone number id, or widget key.
    external_id: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(160), default="", server_default="")
    # Access token, encrypted with a key derived from SECRET_KEY.
    token_encrypted: Mapped[str] = mapped_column(Text, default="", server_default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
