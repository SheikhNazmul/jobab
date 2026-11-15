"""One-way product sync from a WooCommerce store (read only).

Uses the store's REST API with a read-only key the merchant creates in
WooCommerce > Settings > Advanced > REST API. The key is used for this one
sync and is not saved.
"""

import html
import re
import uuid
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UpstreamError
from app.core.http import assert_public_url, http_client
from app.modules.catalog.schemas import ProductIn
from app.modules.catalog.service import upsert_from_source

MAX_PAGES = 20  # 2,000 products
TAGS = re.compile(r"<[^>]+>")


def _plain(text: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAGS.sub(" ", text or ""))).strip()


async def _get(base: str, path: str, auth: tuple[str, str], params: dict[str, Any]) -> list[dict]:
    try:
        response = await http_client().get(
            f"{base}/wp-json/wc/v3/{path}", params=params, auth=auth, timeout=30
        )
    except httpx.HTTPError as exc:
        raise UpstreamError("The store did not answer. Check the address and try again.") from exc
    if response.status_code in (401, 403):
        raise UpstreamError("The store refused the key. Check the consumer key and secret.")
    if response.status_code >= 300:
        raise UpstreamError(f"The store answered with an error ({response.status_code}).")
    data = response.json()
    return data if isinstance(data, list) else []


def _variant_name(variation: dict[str, Any]) -> str:
    options = [str(a.get("option", "")).strip() for a in variation.get("attributes") or []]
    return " / ".join(o for o in options if o) or f"Option {variation.get('id')}"


def _to_product(item: dict[str, Any], variations: list[dict[str, Any]]) -> ProductIn:
    price = item.get("price") or item.get("regular_price") or "0"
    regular = item.get("regular_price") or None
    images = item.get("images") or []
    return ProductIn(
        name=_plain(item.get("name"))[:255] or "Untitled",
        price=price,
        compare_price=regular if item.get("on_sale") and regular else None,
        stock=max(int(item.get("stock_quantity") or 0), 0),
        # Without stock management, WooCommerce only says in or out of stock.
        track_stock=bool(item.get("manage_stock")) or item.get("stock_status") == "outofstock",
        category=_plain((item.get("categories") or [{}])[0].get("name"))[:120],
        description=_plain(item.get("short_description") or item.get("description"))[:5000],
        tags=", ".join(_plain(t.get("name")) for t in item.get("tags") or [])[:1000],
        image_url=(images[0].get("src") or "")[:500] if images else "",
        variants=[
            {
                "name": _variant_name(v)[:120],
                "sku": str(v.get("sku") or "")[:80],
                "price": float(v["price"]) if v.get("price") else None,
                "stock": max(int(v["stock_quantity"]), 0)
                if v.get("stock_quantity") is not None
                else None,
            }
            for v in variations[:60]
        ],
    )


async def sync_woocommerce(
    session: AsyncSession, shop_id: uuid.UUID, url: str, key: str, secret: str
) -> dict[str, int]:
    base = (await assert_public_url(url.strip())).rstrip("/")
    auth = (key, secret)
    created = updated = skipped = 0
    for page in range(1, MAX_PAGES + 1):
        items = await _get(
            base, "products", auth, {"per_page": 100, "page": page, "status": "publish"}
        )
        if not items:
            break
        for item in items:
            variations: list[dict[str, Any]] = []
            if item.get("type") == "variable":
                variations = await _get(
                    base, f"products/{item['id']}/variations", auth, {"per_page": 100}
                )
            try:
                data = _to_product(item, variations)
            except (ValidationError, ValueError, TypeError):
                skipped += 1
                continue
            if await upsert_from_source(session, shop_id, "woocommerce", str(item["id"]), data):
                created += 1
            else:
                updated += 1
    return {"created": created, "updated": updated, "skipped": skipped}
