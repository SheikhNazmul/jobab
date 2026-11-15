"""Orders: pricing a basket (a quote) and turning a quote into an order.

The split matters for safety. The AI may call `build_quote` freely; it only
reads the catalog and does the sums. It may not write an order directly. To
place an order it must pass a `quote_id` from a quote made moments earlier in
the same chat, and the customer must have confirmed. That is how "always
confirm before ordering" is enforced in code, not only asked for in a prompt.
Prices and stock come from the catalog every time, never from the chat.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Conflict
from app.core.utils import money, money_number, utcnow
from app.modules.catalog.models import Product
from app.modules.catalog.service import find_variant, get_bundle, get_product, variant_in_stock
from app.modules.orders.models import Order, OrderQuote, OrderStatus
from app.modules.shops.models import Shop
from app.modules.shops.schemas import ShopSettings

QUOTE_TTL_MINUTES = 30
STATUS_FLOW = [
    OrderStatus.PENDING,
    OrderStatus.CONFIRMED,
    OrderStatus.PACKED,
    OrderStatus.SHIPPED,
    OrderStatus.DELIVERED,
]


class QuoteError(AppError):
    code = "quote_error"


@dataclass
class LineRequest:
    product_id: str
    variant: str | None = None
    quantity: int = 1


def _zone(settings: ShopSettings, zone_id: str | None):
    for zone in settings.delivery.zones:
        if zone.id == zone_id:
            return zone
    return None


async def build_quote(
    session: AsyncSession,
    shop: Shop,
    lines: list[LineRequest],
    *,
    zone_id: str | None = None,
    conversation_id: uuid.UUID | None = None,
    persist: bool = True,
    bundle_id: str | None = None,
) -> dict[str, Any]:
    """Prices a basket against the live catalog. When `persist` is true the
    quote is saved and its id can later place an order exactly once. With a
    bundle_id the items and the discounted price come from the shop's bundle,
    not from the caller, so the AI cannot invent a discount."""
    settings = ShopSettings.model_validate(shop.settings or {})
    max_qty = settings.ai.max_quantity_per_item

    bundle = None
    if bundle_id:
        try:
            bundle = await get_bundle(session, shop.id, uuid.UUID(str(bundle_id)))
        except ValueError:
            bundle = None
        if bundle is None or not bundle.is_active:
            raise QuoteError("That bundle offer is no longer available.")
        lines = [
            LineRequest(
                product_id=str(i.get("product_id")),
                quantity=int(i.get("quantity", 1) or 1),
            )
            for i in bundle.items
            if i.get("product_id")
        ]

    if not lines:
        raise QuoteError("Add at least one product to quote.")

    items: list[dict[str, Any]] = []
    subtotal = Decimal(0)
    for line in lines:
        try:
            product_uuid = uuid.UUID(str(line.product_id))
        except ValueError as exc:
            raise QuoteError(f"Unknown product {line.product_id}.") from exc
        product = await get_product(session, shop.id, product_uuid)
        if product is None or not product.is_active:
            raise QuoteError("One of those products is no longer available.")

        quantity = max(1, min(int(line.quantity or 1), max_qty))
        unit = money(product.price)
        variant = find_variant(product, line.variant)
        variant_name = ""
        if line.variant and variant is None:
            raise QuoteError(f"{product.name} does not come in '{line.variant}'.")
        if variant is not None:
            variant_name = str(variant.get("name", ""))
            if variant.get("price") is not None:
                unit = money(variant["price"])
            if not variant_in_stock(product, variant):
                raise QuoteError(f"{product.name} ({variant_name}) is out of stock.")
        elif not product.in_stock:
            raise QuoteError(f"{product.name} is out of stock.")

        if product.track_stock and variant is None and product.stock < quantity:
            raise QuoteError(f"Only {product.stock} of {product.name} are in stock.")

        line_total = money(unit * quantity)
        subtotal += line_total
        items.append(
            {
                "product_id": str(product.id),
                "name": product.name,
                "variant": variant_name,
                "unit_price": money_number(unit),
                "quantity": quantity,
                "total": money_number(line_total),
            }
        )

    discount = Decimal(0)
    payable = subtotal
    if bundle is not None:
        payable = money(bundle.price)
        if payable > subtotal:
            payable = subtotal  # never charge more than the parts
        discount = subtotal - payable

    zone = _zone(settings, zone_id)
    delivery = money(zone.charge) if zone else Decimal(0)
    free_over = settings.delivery.free_over
    if zone and free_over is not None and payable >= Decimal(str(free_over)):
        delivery = Decimal(0)
    total = money(payable + delivery)

    quote = {
        "items": items,
        "subtotal": money_number(subtotal),
        "discount": money_number(discount),
        "delivery_charge": money_number(delivery),
        "delivery_zone_id": zone.id if zone else "",
        "delivery_zone_name": zone.name if zone else "",
        "delivery_known": zone is not None,
        "total": money_number(total),
        "currency": settings.business.currency,
    }
    if bundle is not None:
        quote["bundle_id"] = str(bundle.id)
        quote["bundle_name"] = bundle.name
        quote["saving"] = money_number(discount)

    if persist and conversation_id is not None:
        row = OrderQuote(
            shop_id=shop.id,
            conversation_id=conversation_id,
            payload=quote,
            expires_at=utcnow() + timedelta(minutes=QUOTE_TTL_MINUTES),
        )
        session.add(row)
        await session.flush()
        quote["quote_id"] = str(row.id)
    return quote


async def _next_number(session: AsyncSession, shop: Shop, settings: ShopSettings) -> str:
    """A per-shop running number. The row lock serialises two orders racing."""
    locked = await session.scalar(
        select(Shop).where(Shop.id == shop.id).with_for_update()
    )
    number = locked.next_order_no
    locked.next_order_no = number + 1
    await session.flush()
    return f"{settings.orders.prefix}{number}"


async def place_order_from_quote(
    session: AsyncSession,
    shop: Shop,
    quote_id: str,
    *,
    conversation_id: uuid.UUID,
    contact_id: uuid.UUID | None,
    channel_type: str,
    customer_name: str,
    phone: str,
    address: str,
    payment_method: str = "",
    note: str = "",
    created_by: str = "ai",
) -> Order:
    """Turns a saved quote into an order, exactly once. Re-checks stock and
    re-prices from the catalog, so a catalog change between quote and order
    cannot be used to buy at an old price."""
    try:
        quote_uuid = uuid.UUID(str(quote_id))
    except ValueError as exc:
        raise QuoteError("Please confirm the order details first.") from exc

    quote = await session.scalar(
        select(OrderQuote)
        .where(
            OrderQuote.id == quote_uuid,
            OrderQuote.shop_id == shop.id,
            OrderQuote.conversation_id == conversation_id,
        )
        .with_for_update()
    )
    if quote is None:
        raise QuoteError("Please confirm the order details first.")
    if quote.used:
        raise Conflict("That order was already placed.")
    if quote.expires_at <= utcnow():
        raise QuoteError("The quote expired. Make a fresh summary and confirm again.")

    settings = ShopSettings.model_validate(shop.settings or {})
    lines = [
        LineRequest(product_id=item["product_id"], variant=item["variant"] or None,
                    quantity=item["quantity"])
        for item in quote.payload.get("items", [])
    ]
    priced = await build_quote(
        session,
        shop,
        lines,
        zone_id=quote.payload.get("delivery_zone_id") or None,
        persist=False,
        bundle_id=quote.payload.get("bundle_id") or None,
    )

    review_over = settings.ai.review_orders_over
    needs_review = (
        created_by == "ai"
        and review_over is not None
        and Decimal(str(priced["total"])) > Decimal(str(review_over))
    )

    order = Order(
        shop_id=shop.id,
        number=await _next_number(session, shop, settings),
        conversation_id=conversation_id,
        contact_id=contact_id,
        channel_type=channel_type,
        customer_name=customer_name[:160],
        phone=phone[:32],
        address=address[:1000],
        zone_id=priced["delivery_zone_id"],
        zone_name=priced["delivery_zone_name"],
        items=priced["items"],
        subtotal=money(priced["subtotal"]),
        delivery_charge=money(priced["delivery_charge"]),
        delivery_confirmed=priced["delivery_known"],
        discount=money(priced.get("discount", 0) or 0),
        total=money(priced["total"]),
        status=OrderStatus.PENDING,
        payment_method=payment_method[:80] or (settings.payment.methods.split(",")[0].strip()[:80]),
        note=note[:1000],
        created_by=created_by[:10],
        needs_review=needs_review,
    )
    session.add(order)

    # Decrement tracked stock for the simple (non-variant) lines.
    for item in priced["items"]:
        if not item["variant"]:
            await session.execute(
                update(Product)
                .where(
                    Product.id == uuid.UUID(item["product_id"]),
                    Product.track_stock.is_(True),
                    Product.stock >= item["quantity"],
                )
                .values(stock=Product.stock - item["quantity"])
            )
    quote.used = True
    await session.flush()
    return order


async def create_manual_order(
    session: AsyncSession,
    shop: Shop,
    *,
    lines: list[LineRequest],
    zone_id: str | None,
    customer_name: str,
    phone: str,
    address: str,
    payment_method: str = "",
    note: str = "",
) -> Order:
    """An order typed in by a team member on the dashboard. Prices from the
    catalogue, reduces stock, but is never held for review."""
    settings = ShopSettings.model_validate(shop.settings or {})
    priced = await build_quote(session, shop, lines, zone_id=zone_id, persist=False)
    order = Order(
        shop_id=shop.id,
        number=await _next_number(session, shop, settings),
        channel_type="manual",
        customer_name=customer_name[:160],
        phone=phone[:32],
        address=address[:1000],
        zone_id=priced["delivery_zone_id"],
        zone_name=priced["delivery_zone_name"],
        items=priced["items"],
        subtotal=money(priced["subtotal"]),
        delivery_charge=money(priced["delivery_charge"]),
        delivery_confirmed=priced["delivery_known"],
        total=money(priced["total"]),
        status=OrderStatus.CONFIRMED,
        payment_method=payment_method[:80],
        note=note[:1000],
        created_by="agent",
    )
    session.add(order)
    for item in priced["items"]:
        if not item["variant"]:
            await session.execute(
                update(Product)
                .where(Product.id == uuid.UUID(item["product_id"]), Product.track_stock.is_(True),
                       Product.stock >= item["quantity"])
                .values(stock=Product.stock - item["quantity"])
            )
    await session.flush()
    return order


async def update_status(
    session: AsyncSession, shop_id: uuid.UUID, order: Order, status: OrderStatus
) -> Order:
    """Changes an order's status and puts stock back when it is cancelled or
    returned after having reduced stock."""
    was_live = order.status not in (OrderStatus.CANCELLED, OrderStatus.RETURNED)
    now_dead = status in (OrderStatus.CANCELLED, OrderStatus.RETURNED)
    if was_live and now_dead:
        for item in order.items:
            if not item.get("variant"):
                await session.execute(
                    update(Product)
                    .where(
                        Product.id == uuid.UUID(item["product_id"]),
                        Product.track_stock.is_(True),
                    )
                    .values(stock=Product.stock + int(item.get("quantity", 0)))
                )
    order.status = status
    await session.flush()
    return order


async def get_order(session: AsyncSession, shop_id: uuid.UUID, order_id: uuid.UUID) -> Order | None:
    return await session.scalar(
        select(Order).where(Order.id == order_id, Order.shop_id == shop_id)
    )


async def find_orders_for_customer(
    session: AsyncSession, shop_id: uuid.UUID, *, phone: str = "", number: str = ""
) -> list[Order]:
    stmt = select(Order).where(Order.shop_id == shop_id)
    if number:
        stmt = stmt.where(func.upper(Order.number) == number.strip().upper())
    elif phone:
        digits = "".join(c for c in phone if c.isdigit())[-10:]
        if not digits:
            return []
        stmt = stmt.where(Order.phone.like(f"%{digits}%"))
    else:
        return []
    rows = await session.scalars(stmt.order_by(Order.created_at.desc()).limit(5))
    return list(rows)


def order_dict(order: Order) -> dict[str, Any]:
    return {
        "id": str(order.id),
        "number": order.number,
        "status": order.status.value,
        "customer_name": order.customer_name,
        "phone": order.phone,
        "address": order.address,
        "zone_name": order.zone_name,
        "channel_type": order.channel_type,
        "items": order.items,
        "subtotal": money_number(order.subtotal),
        "delivery_charge": money_number(order.delivery_charge),
        "delivery_confirmed": order.delivery_confirmed,
        "discount": money_number(order.discount),
        "total": money_number(order.total),
        "payment_method": order.payment_method,
        "note": order.note,
        "courier": order.courier,
        "tracking_code": order.tracking_code,
        "created_by": order.created_by,
        "needs_review": order.needs_review,
        "conversation_id": str(order.conversation_id) if order.conversation_id else None,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }
