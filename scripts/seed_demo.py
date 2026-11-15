"""Create a demo shop with products, so you can sign in and try the dashboard
and the chat straight away.

    python -m scripts.seed_demo

Prints the login details and the website-chat widget key at the end. Safe to
run more than once: it skips the demo shop if it already exists.
"""

import asyncio
from decimal import Decimal

from sqlalchemy import select

from app.db.session import SessionLocal, engine
from app.modules.catalog.schemas import ProductIn
from app.modules.catalog.service import create_product
from app.modules.channels.models import Channel, ChannelType
from app.modules.shops.schemas import ShopSettings
from app.modules.shops.service import create_shop
from app.modules.users.models import User
from app.modules.users.service import create_user

DEMO_EMAIL = "demo@jobab.shop"
DEMO_PASSWORD = "demo-password-123"  # noqa: S105 - a seed login, not a secret

PRODUCTS = [
    ("Cotton Shirt", "Men", "850", "100", "shirt, casual, cotton, শার্ট",
     "Soft full-sleeve cotton shirt. Everyday wear.",
     [{"name": "M", "stock": 40}, {"name": "L", "stock": 35}, {"name": "XL", "stock": 25}]),
    ("Printed Three Piece", "Women", "1250", "60", "kameez, three piece, dress, থ্রি পিস, জামা",
     "Printed unstitched three piece. Soft cotton.", []),
    ("Leather Wallet", "Accessories", "650", "80", "wallet, leather, মানিব্যাগ",
     "Genuine leather wallet.", []),
    ("Canvas Tote Bag", "Accessories", "480", "50", "bag, tote, ব্যাগ", "Sturdy canvas tote.", []),
    ("Kids T-Shirt", "Kids", "350", "120", "t-shirt, kids, বাচ্চাদের টিশার্ট",
     "Cotton t-shirt for kids, 2 to 8 years.",
     [{"name": "2-3y", "stock": 40}, {"name": "4-5y", "stock": 45}, {"name": "6-8y", "stock": 35}]),
    ("Panjabi", "Men", "1650", "40", "panjabi, eid, পাঞ্জাবি", "Cotton panjabi, full sleeve.", []),
]


async def main() -> None:
    async with SessionLocal() as session:
        if await session.scalar(select(User).where(User.email == DEMO_EMAIL)):
            print(f"Demo shop already exists. Sign in with {DEMO_EMAIL} / {DEMO_PASSWORD}")
            return

        user = await create_user(session, "Demo Owner", DEMO_EMAIL, DEMO_PASSWORD)
        shop = await create_shop(session, "Rup Kotha Fashion", user)

        settings = ShopSettings()
        settings.business.description = (
            "Rup Kotha Fashion sells men's, women's and kids clothing online, delivered across "
            "Bangladesh. Cash on delivery and bKash."
        )
        settings.business.hours = "Every day, 10am to 10pm"
        settings.business.phone = "01700000000"
        settings.payment.methods = "Cash on delivery, bKash"
        settings.delivery.zones = [
            {"id": "inside", "name": "Inside Dhaka", "charge": 70, "eta": "1 to 2 days"},
            {"id": "outside", "name": "Outside Dhaka", "charge": 130, "eta": "3 to 4 days"},
        ]
        settings.delivery.free_over = 3000
        settings.ai.instructions = (
            "Be warm and brief. Greet with Assalamu alaikum when the customer does. Always "
            "confirm the size and area before quoting a total."
        )
        shop.settings = settings.model_dump(mode="json")
        await session.flush()

        for name, cat, price, stock, tags, desc, variants in PRODUCTS:
            await create_product(session, shop.id, ProductIn(
                name=name, category=cat, price=Decimal(price), stock=int(stock),
                tags=tags, description=desc,
                variants=[{"name": v["name"], "stock": v["stock"]} for v in variants],
            ))

        widget = await session.scalar(
            select(Channel).where(Channel.shop_id == shop.id, Channel.type == ChannelType.WEBCHAT)
        )
        await session.commit()

        print("\nDemo shop created.")
        print(f"  Sign in at /app with:  {DEMO_EMAIL}  /  {DEMO_PASSWORD}")
        print(f"  Products added:        {len(PRODUCTS)}")
        print(f"  Website chat key:      {widget.external_id}")
        print("  Try the chat at:       /widget/frame?k=" + widget.external_id)
        print("\nSet LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env for the AI to reply.\n")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
