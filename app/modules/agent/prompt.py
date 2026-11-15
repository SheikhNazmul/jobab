"""Builds the instructions the assistant works from for one shop.

The prompt sets the assistant's job, the shop's own facts and the hard rules.
The rules that actually protect the shop (never invent a price, confirm before
ordering, hand off real problems) are also enforced in code around the model;
the prompt explains them so the model cooperates instead of fighting them.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.modules.shops.models import Shop
from app.modules.shops.schemas import ShopSettings

MAX_HISTORY_MESSAGES = 24


def _money(value: float | int, symbol: str) -> str:
    whole = int(value)
    return f"{symbol}{whole}" if value == whole else f"{symbol}{value}"


def build_system_prompt(shop: Shop, settings: ShopSettings) -> str:
    business = settings.business
    ai = settings.ai
    symbol = business.currency_symbol or business.currency

    try:
        today = datetime.now(ZoneInfo(business.timezone)).strftime("%A, %d %B %Y")
    except Exception:  # noqa: BLE001 - a bad timezone must not stop a reply
        today = datetime.utcnow().strftime("%A, %d %B %Y")

    lines: list[str] = []
    lines.append(
        f"You are the sales and support assistant for {shop.name}, an online shop. "
        f"You answer customers on social media and website chat, help them choose "
        f"products, and take orders. Today is {today}."
    )
    if ai.tone:
        lines.append(f"Tone: {ai.tone}")

    lines.append(
        "Language: reply in the same language the customer uses. Many customers in "
        "Bangladesh write Bangla, English, or Banglish (Bangla typed in English "
        "letters). Mirror them naturally. Keep replies short and clear, the way a "
        "good shop page would answer on Messenger or WhatsApp."
    )

    facts: list[str] = []
    if business.description:
        facts.append(f"About the shop: {business.description}")
    if business.hours:
        facts.append(f"Opening hours: {business.hours}")
    if business.phone:
        facts.append(f"Shop phone: {business.phone}")
    if settings.payment.methods:
        facts.append(f"Payment methods: {settings.payment.methods}")
    if settings.payment.note:
        facts.append(f"Payment note: {settings.payment.note}")
    if settings.delivery.zones:
        zones = "; ".join(
            f"{z.name} = {_money(z.charge, symbol)}{(' (' + z.eta + ')') if z.eta else ''}"
            for z in settings.delivery.zones
        )
        facts.append(f"Delivery charges: {zones}")
        if settings.delivery.free_over is not None:
            facts.append(
                f"Delivery is free when the order is {_money(settings.delivery.free_over, symbol)} "
                "or more."
            )
    elif business.description:
        facts.append("Delivery charges are not set here; tell the customer a person will confirm.")
    if settings.delivery.note:
        facts.append(f"Delivery note: {settings.delivery.note}")
    if settings.policies.returns:
        facts.append(f"Return policy: {settings.policies.returns}")
    if settings.policies.other:
        facts.append(f"Other policy: {settings.policies.other}")
    if facts:
        lines.append("Shop facts you may rely on:\n- " + "\n- ".join(facts))

    if ai.instructions:
        lines.append("The shop owner's own instructions:\n" + ai.instructions)

    rules = [
        "Use the tools for anything factual. Never invent a product, price, stock "
        "level, delivery charge, discount or policy. If a tool does not give you a "
        "fact, say you will check rather than guessing.",
        "Prices and stock come only from the tools. Do not agree to a different "
        "price, and do not promise something is in stock without checking.",
        "To take an order: first collect the product and quantity, the customer's "
        "name, full address and phone number. Then call make_quote and show the "
        "customer the itemised total including delivery. Only after the customer "
        "clearly confirms, call place_order. Never place an order the customer has "
        "not confirmed.",
        "If the customer is angry, wants a refund or a human, reports a problem with "
        "an order, or asks something you cannot answer from the tools and shop "
        "facts, call handoff_to_human and let them know a team member will reply.",
        f"Never offer more than {ai.max_quantity_per_item} of one item without a "
        "person approving it.",
    ]
    if ai.disclose_ai:
        rules.append("If someone asks, say honestly that you are an AI assistant for the shop.")
    rules.append(
        "Do not ask for card numbers, passwords, OTPs or any payment credentials. "
        "Cash on delivery and the shop's listed methods are handled by the team."
    )
    if ai.upsell_mode != "off":
        how = (
            "Suggest at most one relevant add-on or bundle, and only when it truly "
            "fits what they are buying."
            if ai.upsell_mode == "soft"
            else "When the customer shows buying interest, offer one relevant add-on "
            "or bundle, kept tasteful and never repeated in the same chat."
        )
        rules.append(
            "Upselling: you may use suggest_related to offer something that goes with "
            "a product, and get_bundle_offer to see any combo the shop has set. "
            + how
            + " Pitch the benefit honestly and drop it at once if they are not "
            "interested. Bundle prices and savings come only from get_bundle_offer, "
            "never invented; to order a bundle, pass its bundle_id to make_quote."
        )
    lines.append("Rules:\n- " + "\n- ".join(rules))

    return "\n\n".join(lines)


def handoff_message(settings: ShopSettings) -> str:
    return settings.ai.handoff_message or "A team member will reply to you shortly."
