import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Variant(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sku: str = Field(default="", max_length=80)
    # Empty price = same as the product. Empty stock = not counted.
    price: float | None = Field(default=None, ge=0, le=10_000_000)
    stock: int | None = Field(default=None, ge=0, le=1_000_000)


def _check_image_url(value: str) -> str:
    value = value.strip()
    if value and not value.startswith(("https://", "http://", "/uploads/")):
        raise ValueError("Use a link that starts with https://")
    return value


def _check_specs(value: dict[str, Any]) -> dict[str, Any]:
    if len(value) > 40:
        raise ValueError("At most 40 specification lines")
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(item, str | int | float | bool):
            raise ValueError("Specification values must be text or numbers")
        cleaned[str(key)[:80]] = item[:300] if isinstance(item, str) else item
    return cleaned


def _check_variants(value: list[Variant]) -> list[Variant]:
    names = [variant.name.strip().lower() for variant in value]
    if len(names) != len(set(names)):
        raise ValueError("Each variant needs its own name")
    return value


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    brand: str = Field(default="", max_length=255)
    category: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=5000)
    tags: str = Field(default="", max_length=1000)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    compare_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    stock: int = Field(default=0, ge=0, le=1_000_000)
    track_stock: bool = True
    variants: list[Variant] = Field(default_factory=list, max_length=60)
    specs: dict[str, Any] = Field(default_factory=dict)
    image_url: str = Field(default="", max_length=500)
    is_active: bool = True

    _image = field_validator("image_url")(_check_image_url)
    _specs = field_validator("specs")(_check_specs)
    _variants = field_validator("variants")(_check_variants)


class ProductPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    brand: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    tags: str | None = Field(default=None, max_length=1000)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    compare_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    stock: int | None = Field(default=None, ge=0, le=1_000_000)
    track_stock: bool | None = None
    variants: list[Variant] | None = Field(default=None, max_length=60)
    specs: dict[str, Any] | None = None
    image_url: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None

    @field_validator("image_url")
    @classmethod
    def _image(cls, value: str | None) -> str | None:
        return None if value is None else _check_image_url(value)

    @field_validator("specs")
    @classmethod
    def _specs(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _check_specs(value)

    @field_validator("variants")
    @classmethod
    def _variants(cls, value: list[Variant] | None) -> list[Variant] | None:
        return None if value is None else _check_variants(value)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    brand: str
    category: str
    description: str
    tags: str
    price: float
    compare_price: float | None
    stock: int
    track_stock: bool
    in_stock: bool
    variants: list[dict[str, Any]]
    specs: dict[str, Any]
    image_url: str
    is_active: bool
    source: str
    created_at: datetime
    updated_at: datetime


class CsvImportIn(BaseModel):
    csv: str = Field(min_length=1, max_length=2_000_000)


class WooSyncIn(BaseModel):
    url: str = Field(min_length=8, max_length=300)
    consumer_key: str = Field(min_length=8, max_length=200)
    consumer_secret: str = Field(min_length=8, max_length=200)
