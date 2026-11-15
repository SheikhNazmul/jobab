"""Products: create, search, CSV import and optional meaning-based search."""

import csv
import io
import logging
import re
import uuid
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.utils import BANGLA_DIGITS, clean_text, money, money_number, slugify
from app.db.session import SessionLocal
from app.modules.agent import llm
from app.modules.catalog.models import Bundle, Product
from app.modules.catalog.schemas import ProductIn

log = logging.getLogger("app.catalog")
settings = get_settings()

MAX_IMPORT_ROWS = 2000
# Words customers and the AI put around a product name that say nothing about
# which product is meant.
STOPWORDS = frozenset(
    "a an the of for and or with in on to is are do you have has any price cost how much "
    "ki er ta ti koto dam daam ache ase nai lagbe chai dite den din apu vai bhai bhaiya "
    "কি কী এর টা টি কত দাম আছে নাই লাগবে চাই".split()
)


def tokenize(query: str) -> list[str]:
    words = re.split(r"[\s,.;:!?/()\[\]{}\"'|+*=<>&-]+", query.lower())
    tokens = [w for w in words if len(w) >= 2 and w not in STOPWORDS]
    return list(dict.fromkeys(tokens))[:8]


def _score(product: Product, tokens: list[str]) -> int:
    name = product.name.lower()
    tags = product.tags.lower()
    group = f"{product.category} {product.brand}".lower()
    variants = " ".join(str(v.get("name", "")) for v in product.variants).lower()
    description = product.description.lower()
    total = matched = 0
    for token in tokens:
        if token in name:
            points = 6 if re.search(rf"(?<![^\W_]){re.escape(token)}", name) else 4
        elif token in tags:
            points = 4
        elif token in group:
            points = 3
        elif token in variants:
            points = 2
        elif token in description:
            points = 1
        else:
            points = 0
        matched += points > 0
        total += points
    if len(tokens) > 1 and matched == len(tokens):
        total += 3
    return total


def _base_query(shop_id: uuid.UUID, max_price: Decimal | None, category: str | None):
    stmt = select(Product).where(Product.shop_id == shop_id, Product.is_active.is_(True))
    if max_price is not None:
        stmt = stmt.where(Product.price <= max_price)
    if category:
        stmt = stmt.where(Product.category.icontains(category, autoescape=True))
    return stmt


async def _lexical(session, shop_id, tokens, limit, max_price, category) -> list[Product]:
    if not tokens:
        return []
    columns = (Product.name, Product.tags, Product.category, Product.brand, Product.description)
    matches = [col.icontains(tok, autoescape=True) for tok in tokens for col in columns]
    rows = (
        await session.scalars(
            _base_query(shop_id, max_price, category).where(or_(*matches)).limit(300)
        )
    ).all()
    ranked = sorted(
        ((_score(p, tokens), p) for p in rows), key=lambda r: (-r[0], not r[1].in_stock, r[1].name)
    )
    return [p for points, p in ranked if points > 0][:limit]


async def _semantic(session, shop_id, query, limit, max_price, category) -> list[Product]:
    if not settings.EMBEDDING_MODEL:
        return []
    try:
        [vector] = await llm.embed([query[:500]])
    except llm.LLMError:
        return []  # search still works on words alone
    distance = Product.embedding.cosine_distance(vector)
    rows = await session.execute(
        _base_query(shop_id, max_price, category)
        .add_columns(distance.label("distance"))
        .where(
            Product.embedding.is_not(None),
            Product.embedding_model == settings.EMBEDDING_MODEL,
        )
        .order_by(distance)
        .limit(limit)
    )
    return [p for p, d in rows if d is not None and d <= settings.EMBEDDING_MAX_DISTANCE]


async def search_products(
    session: AsyncSession,
    shop_id: uuid.UUID,
    query: str,
    *,
    limit: int = 6,
    max_price: Decimal | None = None,
    category: str | None = None,
) -> list[Product]:
    """Word matches first, then products that are close in meaning."""
    found = await _lexical(session, shop_id, tokenize(query), limit, max_price, category)
    if len(found) < limit:
        seen = {p.id for p in found}
        for product in await _semantic(session, shop_id, query, limit, max_price, category):
            if product.id not in seen and len(found) < limit:
                found.append(product)
    return found


async def browse_products(
    session: AsyncSession, shop_id: uuid.UUID, category: str | None = None, limit: int = 12
) -> tuple[list[tuple[str, int]], list[Product]]:
    """Categories with counts, plus a first page of products."""
    counts = await session.execute(
        select(Product.category, func.count())
        .where(Product.shop_id == shop_id, Product.is_active.is_(True))
        .group_by(Product.category)
        .order_by(func.count().desc())
        .limit(30)
    )
    products = (
        await session.scalars(
            _base_query(shop_id, None, category).order_by(Product.name).limit(limit)
        )
    ).all()
    return [(name, count) for name, count in counts], list(products)


async def get_product(
    session: AsyncSession, shop_id: uuid.UUID, product_id: uuid.UUID, *, lock: bool = False
) -> Product | None:
    stmt = select(Product).where(Product.id == product_id, Product.shop_id == shop_id)
    if lock:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


def find_variant(product: Product, name: str | None) -> dict[str, Any] | None:
    if not name:
        return None
    wanted = re.sub(r"\s+", " ", name).strip().lower()
    for variant in product.variants:
        if re.sub(r"\s+", " ", str(variant.get("name", ""))).strip().lower() == wanted:
            return variant
    return None


def variant_in_stock(product: Product, variant: dict[str, Any]) -> bool:
    stock = variant.get("stock")
    return True if stock is None or not product.track_stock else stock > 0


def to_tool_dict(product: Product, *, detail: bool = False) -> dict[str, Any]:
    """What the AI is shown about a product. Prices come only from here."""
    data: dict[str, Any] = {
        "product_id": str(product.id),
        "name": product.name,
        "price": money_number(product.price),
        "in_stock": product.in_stock,
        "category": product.category,
        "has_photo": bool(product.image_url),
    }
    if product.compare_price and product.compare_price > product.price:
        data["regular_price"] = money_number(product.compare_price)
    if product.brand:
        data["brand"] = product.brand
    if product.track_stock:
        data["stock"] = product.stock
    if product.variants:
        data["variants"] = [
            {
                "name": v.get("name"),
                "price": v.get("price") if v.get("price") is not None
                else money_number(product.price),
                "in_stock": variant_in_stock(product, v),
            }
            for v in product.variants
        ]
    if detail:
        data["description"] = product.description[:1500]
        if product.specs:
            data["specs"] = product.specs
    return data


# --- related products and bundles --------------------------------------------------


async def related_products(
    session: AsyncSession, shop_id: uuid.UUID, product: Product, *, limit: int = 4
) -> list[Product]:
    """Products to suggest alongside one the customer likes: closest in meaning
    when embeddings are on, otherwise the same category. Active and in stock
    only, and never the product itself."""
    sellable = or_(Product.track_stock.is_(False), Product.stock > 0)
    base = select(Product).where(
        Product.shop_id == shop_id,
        Product.is_active.is_(True),
        Product.id != product.id,
        sellable,
    )
    if (
        settings.EMBEDDING_MODEL
        and product.embedding is not None
        and product.embedding_model == settings.EMBEDDING_MODEL
    ):
        distance = Product.embedding.cosine_distance(product.embedding)
        rows = await session.execute(
            base.add_columns(distance.label("distance"))
            .where(
                Product.embedding.is_not(None),
                Product.embedding_model == settings.EMBEDDING_MODEL,
            )
            .order_by(distance)
            .limit(limit)
        )
        found = [p for p, d in rows if d is not None]
        if found:
            return found
    if product.category:
        rows = await session.scalars(
            base.where(Product.category == product.category)
            .order_by(Product.stock.desc(), Product.name)
            .limit(limit)
        )
        found = list(rows)
        if found:
            return found
    return list(await session.scalars(base.order_by(Product.name).limit(limit)))


async def get_bundle(
    session: AsyncSession, shop_id: uuid.UUID, bundle_id: uuid.UUID
) -> Bundle | None:
    return await session.scalar(
        select(Bundle).where(Bundle.id == bundle_id, Bundle.shop_id == shop_id)
    )


async def active_bundles_for_product(
    session: AsyncSession, shop_id: uuid.UUID, product_id: uuid.UUID
) -> list[Bundle]:
    """Active bundles that contain this product, or name it as a trigger."""
    pid = str(product_id)
    rows = (
        await session.scalars(
            select(Bundle).where(Bundle.shop_id == shop_id, Bundle.is_active.is_(True))
        )
    ).all()
    return [
        b
        for b in rows
        if any(str(i.get("product_id")) == pid for i in b.items)
        or pid in [str(t) for t in (b.trigger_product_ids or [])]
    ]


async def bundle_to_tool_dict(
    session: AsyncSession, shop_id: uuid.UUID, bundle: Bundle
) -> dict[str, Any] | None:
    """Resolve a bundle against the live catalogue. The price is the shop's; the
    saving is that price versus the sum of the items' current prices. Returns
    None if an item has gone missing."""
    items: list[dict[str, Any]] = []
    regular_total = Decimal(0)
    all_in_stock = True
    for entry in bundle.items:
        try:
            pid = uuid.UUID(str(entry.get("product_id")))
        except (ValueError, TypeError):
            return None
        product = await get_product(session, shop_id, pid)
        if product is None or not product.is_active:
            return None
        qty = max(1, int(entry.get("quantity", 1) or 1))
        regular_total += money(product.price) * qty
        if not product.in_stock:
            all_in_stock = False
        items.append(
            {"name": product.name, "price": money_number(product.price), "quantity": qty}
        )
    price = money(bundle.price)
    saving = regular_total - price
    return {
        "bundle_id": str(bundle.id),
        "name": bundle.name,
        "description": bundle.description,
        "items": items,
        "regular_total": money_number(regular_total),
        "bundle_price": money_number(price),
        "saving": money_number(saving if saving > Decimal(0) else Decimal(0)),
        "all_in_stock": all_in_stock,
    }


# --- writing ---------------------------------------------------------------------


async def unique_slug(
    session: AsyncSession, shop_id: uuid.UUID, name: str, exclude_id: uuid.UUID | None = None
) -> str:
    base = slugify(name)
    stmt = select(Product.slug).where(Product.shop_id == shop_id, Product.slug.like(f"{base}%"))
    if exclude_id:
        stmt = stmt.where(Product.id != exclude_id)
    taken = set((await session.scalars(stmt)).all())
    if base not in taken:
        return base
    number = 2
    while f"{base}-{number}" in taken:
        number += 1
    return f"{base}-{number}"


def _columns(data: ProductIn) -> dict[str, Any]:
    values = data.model_dump()
    values["name"] = clean_text(values["name"], 255)
    values["variants"] = [v for v in values["variants"]]
    return values


async def create_product(
    session: AsyncSession,
    shop_id: uuid.UUID,
    data: ProductIn,
    *,
    source: str = "manual",
    source_id: str | None = None,
) -> Product:
    product = Product(
        shop_id=shop_id,
        slug=await unique_slug(session, shop_id, data.name),
        source=source,
        source_id=source_id,
        **_columns(data),
    )
    session.add(product)
    await session.flush()
    return product


async def upsert_from_source(
    session: AsyncSession, shop_id: uuid.UUID, source: str, source_id: str, data: ProductIn
) -> bool:
    """Creates or updates a product that came from a connected store.
    Returns True when a new product was created."""
    existing = await session.scalar(
        select(Product).where(
            Product.shop_id == shop_id, Product.source == source, Product.source_id == source_id
        )
    )
    if existing is None:
        await create_product(session, shop_id, data, source=source, source_id=source_id)
        return True
    for key, value in _columns(data).items():
        setattr(existing, key, value)
    existing.embedding_model = ""  # text changed; recompute on next refresh
    return False


# --- CSV import --------------------------------------------------------------------

CSV_HELP = (
    "The first row must name the columns. Needed: name, price. Optional: stock, category, "
    "brand, description, tags, image_url, track_stock, variants "
    "(written as Red / M:1250:4 | Blue / L::2, meaning name:price:stock)."
)


def _number(cell: str) -> str:
    return re.sub(r"[^\d.]", "", (cell or "").translate(BANGLA_DIGITS))


def _parse_variants(cell: str) -> list[dict[str, Any]]:
    variants = []
    for part in (cell or "").split("|"):
        if not part.strip():
            continue
        name, _, rest = part.partition(":")
        price, _, stock = rest.partition(":")
        variants.append(
            {
                "name": name.strip(),
                "price": float(_number(price)) if _number(price) else None,
                "stock": int(float(_number(stock))) if _number(stock) else None,
            }
        )
    return variants


def parse_csv(text: str) -> tuple[list[ProductIn], list[dict[str, Any]]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    if not {"name", "price"} <= headers:
        raise AppError(CSV_HELP, code="bad_csv")

    products: list[ProductIn] = []
    errors: list[dict[str, Any]] = []
    for line, raw in enumerate(reader, start=2):
        if len(products) >= MAX_IMPORT_ROWS:
            errors.append(
                {"row": line, "message": f"Only the first {MAX_IMPORT_ROWS} rows were read."}
            )
            break
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        if not any(row.values()):
            continue
        try:
            products.append(
                ProductIn(
                    name=row.get("name", ""),
                    price=_number(row.get("price", "")) or "0",
                    stock=int(float(_number(row.get("stock", "")) or 0)),
                    category=row.get("category", ""),
                    brand=row.get("brand", ""),
                    description=row.get("description", ""),
                    tags=row.get("tags", ""),
                    image_url=row.get("image_url", ""),
                    track_stock=row.get("track_stock", "yes").lower() not in {"no", "false", "0"},
                    variants=_parse_variants(row.get("variants", "")),
                )
            )
        except (ValidationError, ValueError) as exc:
            reason = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
            if len(errors) < 20:
                errors.append({"row": line, "message": reason})
    return products, errors


async def import_products(
    session: AsyncSession, shop_id: uuid.UUID, rows: list[ProductIn]
) -> dict[str, int]:
    """Adds new products and updates ones whose name is already in the catalog."""
    existing = {
        p.slug: p
        for p in (await session.scalars(select(Product).where(Product.shop_id == shop_id))).all()
    }
    created = updated = 0
    for data in rows:
        slug = slugify(data.name)
        product = existing.get(slug)
        if product is None:
            product = Product(shop_id=shop_id, slug=slug, source="csv", **_columns(data))
            session.add(product)
            existing[slug] = product
            created += 1
        else:
            for key, value in _columns(data).items():
                setattr(product, key, value)
            product.embedding_model = ""
            updated += 1
    await session.flush()
    return {"created": created, "updated": updated}


# --- embeddings ----------------------------------------------------------------------


def embedding_text(product: Product) -> str:
    parts = [product.name, product.brand, product.category, product.tags, product.description[:800]]
    return " | ".join(p for p in parts if p)


async def refresh_embeddings(shop_id: uuid.UUID) -> int:
    """Computes vectors for products that have none for the current model.
    Runs in the background after catalog changes. Failures are logged and
    leave word search working as before."""
    if not settings.EMBEDDING_MODEL:
        return 0
    done = 0
    async with SessionLocal() as session:
        while True:
            batch = (
                await session.scalars(
                    select(Product)
                    .where(
                        Product.shop_id == shop_id,
                        Product.is_active.is_(True),
                        Product.embedding_model != settings.EMBEDDING_MODEL,
                    )
                    .limit(64)
                )
            ).all()
            if not batch:
                break
            try:
                vectors = await llm.embed([embedding_text(p) for p in batch])
            except llm.LLMError as exc:
                log.warning("Embedding refresh stopped for shop %s: %s", shop_id, exc)
                break
            for product, vector in zip(batch, vectors, strict=True):
                await session.execute(
                    update(Product)
                    .where(Product.id == product.id)
                    .values(embedding=vector, embedding_model=settings.EMBEDDING_MODEL)
                )
            await session.commit()
            done += len(batch)
    return done
