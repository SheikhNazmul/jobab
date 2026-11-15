"""Test harness.

Runs against a real Postgres (the models use pgvector, JSONB and Identity
columns, so SQLite would not be a fair test). The database is created fresh for
each test and the AI provider is replaced by a scripted fake, so a test needs
no network and no API key.
"""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import text

from app.core.http import set_transport
from app.db.base import Base
from app.db.session import engine
from app.main import app


@pytest.fixture(autouse=True)
async def _schema() -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("DROP SCHEMA IF EXISTS sales_agent_test CASCADE"))
        await conn.execute(text("CREATE SCHEMA sales_agent_test"))
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


class FakeLLM:
    """A scripted OpenAI-compatible endpoint that drives a full order.

    It reads the running conversation and decides the next step from what has
    already happened, so the real tool pipeline (search -> quote -> place) is
    exercised end to end.
    """

    def __init__(self) -> None:
        self.calls = 0

    def _called(self, messages: list[dict], name: str) -> dict | None:
        for m in messages:
            if m.get("role") == "tool":
                continue
        for i, m in enumerate(messages):
            if m.get("role") == "assistant":
                for call in m.get("tool_calls") or []:
                    if call["function"]["name"] == name:
                        # find its result
                        for later in messages[i + 1 :]:
                            if later.get("tool_call_id") == call["id"]:
                                return json.loads(later["content"])
        return None

    def respond(self, body: dict) -> dict:
        self.calls += 1
        messages = body["messages"]
        search = self._called(messages, "search_products")
        quote = self._called(messages, "make_quote")
        placed = self._called(messages, "place_order")

        if search is None:
            return self._tool("search_products", {"query": "shirt"})
        if not search.get("results"):
            return self._text("Sorry, I could not find that item.")
        product_id = search["results"][0]["product_id"]
        if quote is None:
            return self._tool("make_quote", {
                "items": [{"product_id": product_id, "quantity": 2}],
                "delivery_zone_id": "inside",
            })
        if placed is None:
            return self._tool("place_order", {
                "quote_id": quote["quote_id"],
                "customer_name": "Rahim",
                "phone": "01712345678",
                "address": "House 1, Road 2, Dhanmondi, Dhaka",
            })
        return self._text(f"Your order {placed.get('order_number')} is confirmed! "
                          "Cash on delivery. Thank you.")

    def _tool(self, name: str, args: dict) -> dict:
        return {
            "choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{
                    "id": f"call_{self.calls}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }],
            }}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10},
        }

    def _text(self, text_: str) -> dict:
        return {
            "choices": [{"message": {"role": "assistant", "content": text_}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 12},
        }


@pytest.fixture
def fake_llm() -> FakeLLM:
    llm = FakeLLM()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json=llm.respond(json.loads(request.content)))
        return httpx.Response(404, json={"error": "not faked"})

    set_transport(httpx.MockTransport(handler))
    yield llm
    set_transport(None)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with LifespanManager(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-CSRF": "1"},
    ) as http:
        yield http


async def signup(client: httpx.AsyncClient, email: str = "owner@test.shop") -> dict:
    res = await client.post("/api/auth/signup", json={
        "name": "Shop Owner", "email": email, "password": "a-good-password-123",
        "shop_name": "Test Shop",
    })
    assert res.status_code == 201, res.text
    return res.json()
