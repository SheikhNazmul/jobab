"""The tools the assistant may call, and the code that runs them.

Each tool is a small, safe operation against this shop's own data. The model
chooses which to call and with what arguments; the code here decides what
actually happens. An order, for example, can only be created through
place_order, which refuses unless a matching quote was made and the customer's
details are present.
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.utils import money_number, normalize_phone
from app.modules.catalog import service as catalog
from app.modules.crm import service as crm
from app.modules.inbox.models import Contact, Conversation
from app.modules.orders import service as orders
from app.modules.orders.service import LineRequest, QuoteError
from app.modules.shops.models import Shop
from app.modules.shops.schemas import ShopSettings

log = logging.getLogger("app.agent.tools")


@dataclass
class AgentOutcome:
    """Side effects the runtime acts on after the model is done: things to
    show the team, and whether to stop the AI and bring in a person."""

    handoff: bool = False
    handoff_reason: str = ""
    order_id: uuid.UUID | None = None
    lead_ids: list[uuid.UUID] = field(default_factory=list)
    complaint_ids: list[uuid.UUID] = field(default_factory=list)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


@dataclass
class ToolContext:
    session: AsyncSession
    shop: Shop
    settings: ShopSettings
    conversation: Conversation
    contact: Contact
    outcome: AgentOutcome
    channel_type: str = ""


def tool_schemas(settings: ShopSettings) -> list[dict[str, Any]]:
    """The function definitions sent to the model."""
    tools = [
        _fn("search_products",
            "Search this shop's catalogue for products matching what the customer "
            "wants. Returns name, price, stock and variants.",
            {
                "query": _str("What the customer is looking for, in their words."),
                "max_price": {"type": "number", "description": "Optional budget ceiling."},
                "category": _str("Optional category to narrow the search."),
            }, ["query"]),
        _fn("product_details",
            "Full details for one product by its id, including description and specs.",
            {"product_id": _str("The product_id from a search result.")}, ["product_id"]),
        _fn("list_categories",
            "List the product categories this shop sells, with how many items each has. "
            "Use when the customer is browsing rather than naming a product.",
            {}, []),
        _fn("delivery_and_payment",
            "The shop's delivery zones and charges, and accepted payment methods.",
            {}, []),
        _fn("check_order_status",
            "Look up a customer's recent orders by phone number or order number.",
            {
                "phone": _str("Customer phone number."),
                "order_number": _str("An order number like the shop uses."),
            }, []),
        _fn("make_quote",
            "Price a basket before ordering: the items, the subtotal, any bundle "
            "saving, delivery and total. Always show this to the customer and get a "
            "clear yes before placing the order. Returns a quote_id to pass to "
            "place_order. Pass bundle_id to price a shop bundle at its set price.",
            {
                "items": {
                    "type": "array",
                    "description": "The products the customer wants.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": _str("From a search result."),
                            "variant": _str("Variant name, if the product has variants."),
                            "quantity": {"type": "integer", "minimum": 1},
                        },
                        "required": ["product_id", "quantity"],
                    },
                },
                "bundle_id": _str("A bundle_id from get_bundle_offer, to order that "
                                  "bundle at its set price. The items come from the "
                                  "bundle, so you may omit items."),
                "delivery_zone_id": _str("The zone id from delivery_and_payment, if known."),
            }, []),
        _fn("place_order",
            "Place the order the customer has confirmed. Needs the quote_id from "
            "make_quote and the customer's name, phone and full delivery address. "
            "Only call this after the customer has clearly said yes.",
            {
                "quote_id": _str("The quote_id from make_quote."),
                "customer_name": _str("The customer's name."),
                "phone": _str("The customer's phone number."),
                "address": _str("The full delivery address."),
                "payment_method": _str("How they will pay, e.g. cash on delivery."),
                "note": _str("Any note for the order, such as a landmark or size."),
            }, ["quote_id", "customer_name", "phone", "address"]),
        _fn("save_customer_lead",
            "Save a customer who is interested but has not ordered yet, so the team "
            "can follow up. Use when they ask you to keep their number or will "
            "decide later.",
            {
                "name": _str("Customer name, if given."),
                "phone": _str("Customer phone, if given."),
                "interest": _str("What they were interested in."),
            }, ["interest"]),
        _fn("report_complaint",
            "Record a complaint or a problem with an order and bring in a person. "
            "Use for refunds, damaged items, wrong deliveries or an upset customer.",
            {"summary": _str("A short, neutral summary of the problem.")}, ["summary"]),
        _fn("handoff_to_human",
            "Stop answering and hand the chat to a team member. Use when you cannot "
            "help from the shop's facts and tools, or the customer asks for a human.",
            {"reason": _str("Why a person is needed, in a few words.")}, ["reason"]),
    ]
    if settings.ai.upsell_mode != "off":
        tools.extend([
            _fn("suggest_related",
                "Find a few products that go well with one the customer is interested "
                "in, to offer as an add-on. Use sparingly, only when they show buying "
                "interest. Returns names and prices to suggest.",
                {"product_id": _str("The product_id the customer is interested in.")},
                ["product_id"]),
            _fn("get_bundle_offer",
                "Get any bundle or combo the shop has set that includes this product, "
                "with its real bundle price and saving. The price and saving come only "
                "from here; never invent a discount. Pass the returned bundle_id to "
                "make_quote to order it.",
                {"product_id": _str("The product_id the customer is interested in.")},
                ["product_id"]),
        ])
    return tools


async def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Runs one tool call and returns a JSON-able result for the model. Never
    raises to the caller: a tool problem becomes a result the model can read."""
    try:
        handler = _HANDLERS.get(name)
        if handler is None:
            return {"error": f"Unknown tool {name}."}
        return await handler(ctx, args)
    except QuoteError as exc:
        return {"ok": False, "error": exc.message}
    except AppError as exc:
        return {"ok": False, "error": exc.message}
    except Exception:  # noqa: BLE001 - defend the reply loop from any tool bug
        log.exception("Tool %s failed", name)
        return {"ok": False, "error": "That did not work. Tell the customer you will check."}


# --- handlers ----------------------------------------------------------------------


async def _search_products(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from decimal import Decimal

    query = str(args.get("query", "")).strip()
    if not query:
        return {"results": []}
    max_price = args.get("max_price")
    results = await catalog.search_products(
        ctx.session,
        ctx.shop.id,
        query,
        limit=6,
        max_price=Decimal(str(max_price)) if max_price not in (None, "") else None,
        category=str(args.get("category") or "") or None,
    )
    return {"results": [catalog.to_tool_dict(p) for p in results],
            "count": len(results)}


async def _product_details(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    product = await _load_product(ctx, args.get("product_id"))
    if product is None:
        return {"error": "No product with that id."}
    return {"product": catalog.to_tool_dict(product, detail=True)}


async def _list_categories(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    cats, _ = await catalog.browse_products(ctx.session, ctx.shop.id, limit=0)
    return {"categories": [{"name": name or "Other", "count": count} for name, count in cats]}


async def _delivery_and_payment(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    d = ctx.settings.delivery
    return {
        "delivery_zones": [
            {"id": z.id, "name": z.name, "charge": z.charge, "eta": z.eta} for z in d.zones
        ],
        "free_delivery_over": d.free_over,
        "delivery_note": d.note,
        "payment_methods": ctx.settings.payment.methods,
        "payment_note": ctx.settings.payment.note,
        "zones_configured": bool(d.zones),
    }


async def _check_order_status(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    found = await orders.find_orders_for_customer(
        ctx.session, ctx.shop.id,
        phone=str(args.get("phone") or ""), number=str(args.get("order_number") or ""),
    )
    return {
        "orders": [
            {
                "number": o.number,
                "status": o.status.value,
                "total": money_number(o.total),
                "items": [f"{i['quantity']}x {i['name']}" for i in o.items],
                "placed_on": o.created_at.date().isoformat(),
                "courier": o.courier,
                "tracking_code": o.tracking_code,
            }
            for o in found
        ]
    }


async def _make_quote(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    bundle_id = str(args.get("bundle_id") or "") or None
    raw_items = args.get("items") or []
    lines = [
        LineRequest(
            product_id=str(item.get("product_id", "")),
            variant=str(item.get("variant")) if item.get("variant") else None,
            quantity=int(item.get("quantity", 1) or 1),
        )
        for item in raw_items
        if item.get("product_id")
    ]
    if not lines and not bundle_id:
        return {"ok": False, "error": "No valid products to quote."}
    quote = await orders.build_quote(
        ctx.session, ctx.shop, lines,
        zone_id=str(args.get("delivery_zone_id") or "") or None,
        conversation_id=ctx.conversation.id,
        bundle_id=bundle_id,
    )
    quote["ok"] = True
    if not quote["delivery_known"]:
        quote["note"] = (
            "Delivery charge for this area is not set. Tell the customer the team "
            "will confirm it; it is not included in the total yet."
        )
    return quote


async def _place_order(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("customer_name") or ctx.contact.name or "").strip()
    phone = normalize_phone(str(args.get("phone") or ctx.contact.phone or "")) or str(
        args.get("phone") or ""
    ).strip()
    address = str(args.get("address") or ctx.contact.address or "").strip()
    if not name or not phone or not address:
        return {"ok": False,
                "error": "Still need the customer's name, phone and full address first."}

    order = await orders.place_order_from_quote(
        ctx.session, ctx.shop, str(args.get("quote_id", "")),
        conversation_id=ctx.conversation.id,
        contact_id=ctx.contact.id,
        channel_type=ctx.channel_type,
        customer_name=name, phone=phone, address=address,
        payment_method=str(args.get("payment_method") or ""),
        note=str(args.get("note") or ""),
        created_by="ai",
    )
    # Remember the customer's details on the contact for next time.
    ctx.contact.name = ctx.contact.name or name[:160]
    ctx.contact.phone = ctx.contact.phone or phone[:32]
    ctx.contact.address = address[:1000]

    ctx.outcome.order_id = order.id
    ctx.outcome.events.append(("order", orders.order_dict(order)))
    result = {
        "ok": True,
        "order_number": order.number,
        "total": money_number(order.total),
        "status": order.status.value,
    }
    if order.needs_review:
        result["note"] = (
            "This order is saved but held for the team to review before it is "
            "confirmed. Tell the customer the team will confirm shortly."
        )
    return result


async def _save_customer_lead(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    lead = await crm.save_lead(
        ctx.session, ctx.shop.id,
        name=str(args.get("name") or ctx.contact.name or ""),
        phone=normalize_phone(str(args.get("phone") or ctx.contact.phone or "")) or str(
            args.get("phone") or ""
        ),
        interest=str(args.get("interest") or ""),
        conversation_id=ctx.conversation.id, contact_id=ctx.contact.id,
    )
    ctx.outcome.lead_ids.append(lead.id)
    ctx.outcome.events.append(("lead", crm.lead_dict(lead)))
    return {"ok": True}


async def _report_complaint(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    complaint = await crm.open_complaint(
        ctx.session, ctx.shop.id, str(args.get("summary") or "Complaint"),
        conversation_id=ctx.conversation.id, contact_id=ctx.contact.id,
    )
    ctx.outcome.complaint_ids.append(complaint.id)
    ctx.outcome.events.append(("complaint", crm.complaint_dict(complaint)))
    ctx.outcome.handoff = True
    ctx.outcome.handoff_reason = "Complaint"
    return {"ok": True, "handed_off": True}


async def _handoff(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ctx.outcome.handoff = True
    ctx.outcome.handoff_reason = str(args.get("reason") or "Customer needs a person")[:255]
    return {"ok": True, "handed_off": True}


async def _suggest_related(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    product = await _load_product(ctx, args.get("product_id"))
    if product is None:
        return {"error": "No product with that id."}
    related = await catalog.related_products(ctx.session, ctx.shop.id, product, limit=4)
    return {"related": [catalog.to_tool_dict(p) for p in related], "count": len(related)}


async def _get_bundle_offer(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    product = await _load_product(ctx, args.get("product_id"))
    if product is None:
        return {"error": "No product with that id."}
    bundles = await catalog.active_bundles_for_product(ctx.session, ctx.shop.id, product.id)
    offers = []
    for bundle in bundles:
        data = await catalog.bundle_to_tool_dict(ctx.session, ctx.shop.id, bundle)
        if data is not None and data["all_in_stock"]:
            offers.append(data)
    return {"bundles": offers, "count": len(offers)}


# --- helpers -----------------------------------------------------------------------


async def _load_product(ctx: ToolContext, product_id: Any):
    try:
        return await catalog.get_product(ctx.session, ctx.shop.id, uuid.UUID(str(product_id)))
    except (ValueError, TypeError):
        return None


def _fn(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def _str(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


_HANDLERS = {
    "search_products": _search_products,
    "product_details": _product_details,
    "list_categories": _list_categories,
    "delivery_and_payment": _delivery_and_payment,
    "check_order_status": _check_order_status,
    "make_quote": _make_quote,
    "place_order": _place_order,
    "save_customer_lead": _save_customer_lead,
    "report_complaint": _report_complaint,
    "handoff_to_human": _handoff,
    "suggest_related": _suggest_related,
    "get_bundle_offer": _get_bundle_offer,
}
