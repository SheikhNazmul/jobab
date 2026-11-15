import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.modules.shops.models import Role


class BusinessSettings(BaseModel):
    description: str = Field(default="", max_length=2000)
    phone: str = Field(default="", max_length=40)
    address: str = Field(default="", max_length=300)
    hours: str = Field(default="", max_length=200)
    country: str = Field(default="BD", min_length=2, max_length=2)
    timezone: str = Field(default="Asia/Dhaka", max_length=64)
    currency: str = Field(default="BDT", min_length=3, max_length=3)
    currency_symbol: str = Field(default="৳", max_length=4)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown time zone") from exc
        return value

    @field_validator("country", "currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class AISettings(BaseModel):
    enabled: bool = True
    tone: str = Field(default="Friendly, polite and brief.", max_length=300)
    # The shop's own guidance for the assistant, in the owner's words.
    instructions: str = Field(default="", max_length=4000)
    handoff_message: str = Field(
        default="A team member will reply to you shortly.", max_length=500
    )
    # After a person replies, the AI stays quiet for this long.
    pause_minutes: int = Field(default=30, ge=0, le=1440)
    # Say honestly that it is an AI assistant when a customer asks.
    disclose_ai: bool = True
    # AI orders above this total are saved for a person to review first.
    review_orders_over: float | None = Field(default=None, ge=0)
    max_quantity_per_item: int = Field(default=20, ge=1, le=1000)
    # A ceiling on AI replies in one chat per hour. Stops loops with other bots.
    max_replies_per_hour: int = Field(default=40, ge=5, le=300)
    # How much the AI suggests add-ons and bundles: off, soft or active.
    upsell_mode: str = Field(default="soft", pattern=r"^(off|soft|active)$")


class DeliveryZone(BaseModel):
    id: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    charge: float = Field(ge=0, le=1_000_000)
    eta: str = Field(default="", max_length=120)

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9_-]+", value):
            raise ValueError("Use lowercase letters, numbers, dashes or underscores")
        return value


class DeliverySettings(BaseModel):
    zones: list[DeliveryZone] = Field(default_factory=list, max_length=20)
    free_over: float | None = Field(default=None, ge=0)
    note: str = Field(default="", max_length=500)

    @field_validator("zones")
    @classmethod
    def _unique_ids(cls, zones: list[DeliveryZone]) -> list[DeliveryZone]:
        ids = [zone.id for zone in zones]
        if len(ids) != len(set(ids)):
            raise ValueError("Each delivery zone needs its own id")
        return zones


class PaymentSettings(BaseModel):
    methods: str = Field(default="", max_length=300)
    note: str = Field(default="", max_length=500)


class PolicySettings(BaseModel):
    returns: str = Field(default="", max_length=1500)
    other: str = Field(default="", max_length=1500)


class CommentSettings(BaseModel):
    auto_reply: bool = False
    private_reply: bool = True
    public_text: str = Field(
        default="Thank you for your comment! Please check your inbox 🙏", max_length=600
    )
    private_text: str = Field(
        default="Hi! Thanks for your interest. How can we help you today?", max_length=900
    )


class OrderSettings(BaseModel):
    prefix: str = Field(default="ORD-", max_length=10, pattern=r"^[A-Za-z0-9#-]*$")


class ShopSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    business: BusinessSettings = Field(default_factory=BusinessSettings)
    ai: AISettings = Field(default_factory=AISettings)
    delivery: DeliverySettings = Field(default_factory=DeliverySettings)
    payment: PaymentSettings = Field(default_factory=PaymentSettings)
    policies: PolicySettings = Field(default_factory=PolicySettings)
    comments: CommentSettings = Field(default_factory=CommentSettings)
    orders: OrderSettings = Field(default_factory=OrderSettings)


class ShopProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class MemberIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    role: Role = Role.AGENT


class MemberRoleIn(BaseModel):
    role: Role


class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    email: str
    role: Role
    created_at: datetime
