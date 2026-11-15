"""End-to-end: a website-chat customer is answered by the AI and an order is
placed, using the real database and the real tool pipeline.
"""

import asyncio

import httpx

from tests.conftest import signup

ZONES = {
    "business": {"currency": "BDT", "currency_symbol": "৳"},
    "delivery": {"zones": [{"id": "inside", "name": "Inside Dhaka", "charge": 70}]},
    "payment": {"methods": "Cash on delivery"},
    "ai": {"enabled": True},
}


async def _poll_ai_reply(client: httpx.AsyncClient, key: str, token: str) -> list[dict]:
    for _ in range(40):
        await asyncio.sleep(0.25)
        res = await client.get(f"/api/widget/{key}/messages", params={"after_seq": 0},
                               headers={"X-Visitor-Token": token})
        messages = res.json()["messages"]
        if any(m["direction"] == "out" for m in messages):
            return messages
    return []


async def test_ai_takes_an_order(client: httpx.AsyncClient, fake_llm) -> None:
    await signup(client)

    # The shop sets a delivery zone and adds a product.
    res = await client.put("/api/shop/settings", json=ZONES)
    assert res.status_code == 200, res.text
    res = await client.post("/api/products", json={
        "name": "Cotton Shirt", "price": 850, "stock": 10, "category": "Men",
        "tags": "shirt casual", "track_stock": True,
    })
    assert res.status_code == 201, res.text

    code = await client.get("/api/channels/widget-code")
    key = code.json()["key"]

    # A visitor opens the chat and asks for a shirt.
    session = (await client.post(f"/api/widget/{key}/session", json={})).json()
    token = session["visitor_token"]
    send = await client.post(f"/api/widget/{key}/message", json={"text": "I want a shirt"},
                             headers={"X-Visitor-Token": token})
    assert send.status_code == 200, send.text

    messages = await _poll_ai_reply(client, key, token)
    assert messages, "the AI never replied"
    ai_texts = [m["text"] for m in messages if m["sender"] == "ai"]
    assert ai_texts and "confirmed" in ai_texts[-1].lower()

    # An order exists, priced from the catalogue: 2 x 850 + 70 delivery.
    orders = (await client.get("/api/orders")).json()["items"]
    assert len(orders) == 1, orders
    order = orders[0]
    assert order["total"] == 2 * 850 + 70
    assert order["created_by"] == "ai"
    assert order["items"][0]["name"] == "Cotton Shirt"
    assert order["phone"] == "01712345678"

    # Stock was reduced and a credit was spent.
    me = (await client.get("/api/auth/me")).json()
    assert me["shop"]["credits_used"] >= 1
    product = (await client.get("/api/products")).json()["items"][0]
    assert product["stock"] == 8
