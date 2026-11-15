import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DemoRequest(Base):
    """A demo request sent from the public homepage form."""

    __tablename__ = "demo_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160))
    business: Mapped[str] = mapped_column(String(160), default="", server_default="")
    phone: Mapped[str] = mapped_column(String(32), default="", server_default="")
    email: Mapped[str] = mapped_column(String(255), default="", server_default="")
    message: Mapped[str] = mapped_column(Text, default="", server_default="")
    ip: Mapped[str] = mapped_column(String(64), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
