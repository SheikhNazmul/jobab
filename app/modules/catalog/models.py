import uuid
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimeStampMixin


class Product(Base, TimeStampMixin):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("stock >= 0", name="stock_non_negative"),
        UniqueConstraint("shop_id", "slug"),
        Index(
            "uq_products_shop_source",
            "shop_id",
            "source",
            "source_id",
            unique=True,
            postgresql_where="source_id IS NOT NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str] = mapped_column(String(255), default="", server_default="")
    # Free text: shops on the platform sell different things.
    category: Mapped[str] = mapped_column(String(120), default="", server_default="", index=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Extra words customers use for this product (Bangla, Banglish, nicknames).
    tags: Mapped[str] = mapped_column(Text, default="", server_default="")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    compare_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    stock: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # False = made to order or not counted; the product is always sellable.
    track_stock: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # [{"name": "Red / M", "sku": "...", "price": 1250, "stock": 4}, ...]
    variants: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    specs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    image_url: Mapped[str] = mapped_column(String(500), default="", server_default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    source: Mapped[str] = mapped_column(String(30), default="manual", server_default="manual")
    source_id: Mapped[str | None] = mapped_column(String(120))
    # Meaning-based search. Filled only when EMBEDDING_MODEL is configured.
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    embedding_model: Mapped[str] = mapped_column(String(120), default="", server_default="")

    @property
    def in_stock(self) -> bool:
        return (not self.track_stock) or self.stock > 0


class Bundle(Base, TimeStampMixin):
    """A shop-defined combo offer: products sold together at a price the shop
    sets. The AI may pitch a bundle, but its price and saving are always taken
    from this row and the live catalogue, never invented by the model."""

    __tablename__ = "bundles"
    __table_args__ = (CheckConstraint("price >= 0", name="bundle_price_non_negative"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # [{"product_id": "...", "quantity": 1}, ...]
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    # The shop's set price for the whole bundle; the saving is this versus the
    # sum of the items' current catalogue prices.
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # Products that trigger this offer when a customer is interested in them,
    # even if not in the bundle. Empty = offer when a bundle item is in play.
    trigger_product_ids: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
