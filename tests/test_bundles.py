"""Smart upsell and bundles.

The point of these tests is the guardrail: a bundle's price and saving come
from the shop's bundle row and the live catalogue, never from the model. The
AI can pitch a bundle, but it cannot invent a discount.
"""

from decimal import Decimal

from app.db.session import SessionLocal
from app.modules.agent.tools import tool_schemas
from app.modules.catalog import service as catalog
from app.modules.catalog.models import Bundle, Product
from app.modules.orders import service as orders
from app.modules.orders.service import LineRequest
from app.modules.shops.models import Shop
from app.modules.shops.schemas import ShopSettings


async def _setup(session):
    shop = Shop(name="Test Shop", settings={"business": {"currency": "BDT"}})
    session.add(shop)
    await session.flush()
    kurti = Product(shop_id=shop.id, name="Emerald Kurti", slug="emerald-kurti",
                    category="Kurti", price=Decimal("1250"), stock=10)
    hijab = Product(shop_id=shop.id, name="Matching Hijab", slug="matching-hijab",
                    category="Kurti", price=Decimal("300"), stock=10)
    panjabi = Product(shop_id=shop.id, name="Cotton Panjabi", slug="cotton-panjabi",
                      category="Panjabi", price=Decimal("1650"), stock=5)
    session.add_all([kurti, hijab, panjabi])
    await session.flush()
    return shop, kurti, hijab, panjabi


async def test_related_same_category_excludes_self():
    async with SessionLocal() as session:
        shop, kurti, hijab, _ = await _setup(session)
        related = await catalog.related_products(session, shop.id, kurti, limit=4)
        ids = {p.id for p in related}
        assert kurti.id not in ids          # never the product itself
        assert hijab.id in ids              # a same-category item
        assert related[0].category == "Kurti"


async def test_related_skips_out_of_stock():
    async with SessionLocal() as session:
        shop, kurti, hijab, _ = await _setup(session)
        hijab.stock = 0
        await session.flush()
        related = await catalog.related_products(session, shop.id, kurti, limit=4)
        assert hijab.id not in {p.id for p in related}


async def test_bundle_saving_is_computed_from_catalogue():
    async with SessionLocal() as session:
        shop, kurti, hijab, _ = await _setup(session)
        bundle = Bundle(
            shop_id=shop.id, name="Kurti + Hijab combo",
            items=[{"product_id": str(kurti.id), "quantity": 1},
                   {"product_id": str(hijab.id), "quantity": 1}],
            price=Decimal("1400"),
        )
        session.add(bundle)
        await session.flush()
        view = await catalog.bundle_to_tool_dict(session, shop.id, bundle)
        assert view["regular_total"] == 1550   # 1250 + 300, from the catalogue
        assert view["bundle_price"] == 1400     # the shop's set price
        assert view["saving"] == 150
        assert view["all_in_stock"] is True


async def test_bundle_quote_discount_comes_from_the_bundle_only():
    async with SessionLocal() as session:
        shop, kurti, hijab, _ = await _setup(session)
        bundle = Bundle(
            shop_id=shop.id, name="combo",
            items=[{"product_id": str(kurti.id), "quantity": 1},
                   {"product_id": str(hijab.id), "quantity": 1}],
            price=Decimal("1400"),
        )
        session.add(bundle)
        await session.flush()

        quoted = await orders.build_quote(
            session, shop, [], bundle_id=str(bundle.id), persist=False
        )
        assert quoted["subtotal"] == 1550
        assert quoted["discount"] == 150
        assert quoted["total"] == 1400          # bundle price, no delivery zone set
        assert quoted["bundle_id"] == str(bundle.id)

        # The same two items quoted WITHOUT the bundle: full price, no discount.
        plain = await orders.build_quote(
            session, shop,
            [LineRequest(product_id=str(kurti.id), quantity=1),
             LineRequest(product_id=str(hijab.id), quantity=1)],
            persist=False,
        )
        assert plain["discount"] == 0
        assert plain["total"] == 1550


async def test_bundle_never_charges_more_than_the_parts():
    async with SessionLocal() as session:
        shop, kurti, hijab, _ = await _setup(session)
        bundle = Bundle(
            shop_id=shop.id, name="bad combo priced too high",
            items=[{"product_id": str(kurti.id), "quantity": 1},
                   {"product_id": str(hijab.id), "quantity": 1}],
            price=Decimal("2000"),  # more than 1550
        )
        session.add(bundle)
        await session.flush()
        quoted = await orders.build_quote(
            session, shop, [], bundle_id=str(bundle.id), persist=False
        )
        assert quoted["total"] == 1550          # capped at the parts
        assert quoted["discount"] == 0


def test_upsell_tools_are_gated_by_mode():
    off = ShopSettings.model_validate({"ai": {"upsell_mode": "off"}})
    names_off = {t["function"]["name"] for t in tool_schemas(off)}
    assert "suggest_related" not in names_off
    assert "get_bundle_offer" not in names_off

    soft = ShopSettings.model_validate({"ai": {"upsell_mode": "soft"}})
    names_soft = {t["function"]["name"] for t in tool_schemas(soft)}
    assert {"suggest_related", "get_bundle_offer"} <= names_soft

    make_quote = next(t for t in tool_schemas(soft) if t["function"]["name"] == "make_quote")
    assert "bundle_id" in make_quote["function"]["parameters"]["properties"]
