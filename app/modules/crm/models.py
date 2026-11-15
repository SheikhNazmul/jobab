import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimeStampMixin, value_enum


class LeadStatus(enum.StrEnum):
    NEW = "new"
    CONTACTED = "contacted"
    WON = "won"
    LOST = "lost"


class ComplaintStatus(enum.StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class Lead(Base, TimeStampMixin):
    """Someone interested who has not ordered yet."""

    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(160), default="", server_default="")
    phone: Mapped[str] = mapped_column(String(32), default="", server_default="")
    interest: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[LeadStatus] = mapped_column(
        value_enum(LeadStatus), default=LeadStatus.NEW, server_default="new"
    )


class Complaint(Base, TimeStampMixin):
    __tablename__ = "complaints"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL")
    )
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[ComplaintStatus] = mapped_column(
        value_enum(ComplaintStatus), default=ComplaintStatus.OPEN, server_default="open"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommentLog(Base):
    """A public comment on a post and what we answered."""

    __tablename__ = "comment_logs"
    __table_args__ = (UniqueConstraint("channel_id", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(160))
    post_id: Mapped[str] = mapped_column(String(160), default="", server_default="")
    author_id: Mapped[str] = mapped_column(String(160), default="", server_default="")
    author_name: Mapped[str] = mapped_column(String(160), default="", server_default="")
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    reply_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    private_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(20), default="received", server_default="received")
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
