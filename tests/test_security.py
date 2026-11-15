"""Security and safety behaviours, as runnable checks.

These back the claims in SECURITY.md: tenant isolation, the CSRF lock on the
cookie API, the webhook signature gate, and the rule that an order cannot be
placed without a fresh confirmed quote.
"""

import hashlib
import hmac
import json
import uuid

import httpx

from app.modules.orders.service import QuoteError, place_order_from_quote
from tests.conftest import signup


async def test_one_shop_cannot_read_another_shops_products(client: httpx.AsyncClient) -> None:
    await signup(client, "a@test.shop")
    made = await client.post("/api/products", json={"name": "Shop A Shirt", "price": 500})
    product_id = made.json()["id"]
    await client.post("/api/auth/logout")

    await signup(client, "b@test.shop")
    # B is signed in now; A's product must be invisible and unreachable.
    listing = (await client.get("/api/products")).json()
    assert listing["total"] == 0
    denied = await client.get(f"/api/products/{product_id}")
    assert denied.status_code == 404


async def test_cookie_api_refuses_requests_without_the_csrf_header(
    client: httpx.AsyncClient,
) -> None:
    await signup(client)
    # Same client, but strip the header the dashboard always sends.
    res = await client.post("/api/products", json={"name": "X", "price": 10},
                            headers={"X-CSRF": ""})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "csrf_failed"


async def test_webhook_rejects_a_bad_signature(client: httpx.AsyncClient) -> None:
    body = json.dumps({"object": "page", "entry": []}).encode()
    res = await client.post("/webhooks/meta", content=body,
                            headers={"X-Hub-Signature-256": "sha256=deadbeef",
                                     "Content-Type": "application/json"})
    assert res.status_code == 403


async def test_webhook_accepts_a_correct_signature(client: httpx.AsyncClient, monkeypatch) -> None:
    from app.core.config import get_settings

    secret = get_settings().META_APP_SECRET.get_secret_value() or "test-app-secret"
    monkeypatch.setattr(get_settings().META_APP_SECRET, "get_secret_value", lambda: secret)
    body = json.dumps({"object": "page", "entry": []}).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    res = await client.post("/webhooks/meta", content=body,
                            headers={"X-Hub-Signature-256": signature,
                                     "Content-Type": "application/json"})
    assert res.status_code == 200


async def test_an_order_needs_a_real_quote(client: httpx.AsyncClient) -> None:
    """place_order with a made-up quote id is refused: the model cannot order
    without a quote the customer saw and confirmed."""
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.modules.shops.models import Shop

    await signup(client)
    async with SessionLocal() as session:
        shop = (await session.scalars(select(Shop))).first()
        try:
            await place_order_from_quote(
                session, shop, str(uuid.uuid4()),
                conversation_id=uuid.uuid4(), contact_id=None, channel_type="webchat",
                customer_name="X", phone="01712345678", address="Dhaka",
            )
            raised = False
        except QuoteError:
            raised = True
        assert raised, "placing an order without a valid quote should fail"


async def test_production_config_rejects_dev_secret() -> None:
    """The app refuses to boot in production with the development secret key."""
    from pydantic import ValidationError

    from app.core.config import Settings

    try:
        Settings(ENVIRONMENT="production", PUBLIC_BASE_URL="https://x.example",
                 DB_PASSWORD="realpw")  # dev SECRET_KEY left in place
        raised = False
    except ValidationError:
        raised = True
    assert raised
