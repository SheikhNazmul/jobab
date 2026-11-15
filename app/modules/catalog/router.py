import uuid
from pathlib import Path

from fastapi import APIRouter, Query, Request
from sqlalchemy import delete, func, or_, select

from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.core.security import new_token
from app.core.tasks import spawn
from app.core.utils import client_ip
from app.db.session import SessionDep
from app.modules.catalog.models import Product
from app.modules.catalog.schemas import CsvImportIn, ProductIn, ProductOut, ProductPatch, WooSyncIn
from app.modules.catalog.service import (
    create_product,
    get_product,
    import_products,
    parse_csv,
    refresh_embeddings,
    unique_slug,
)
from app.modules.catalog.woocommerce import sync_woocommerce
from app.modules.shops.service import audit
from app.modules.users.deps import AuthDep, ManagerDep

settings = get_settings()
router = APIRouter(prefix="/api/products", tags=["products"])

# File type is decided by the first bytes of the file, never by its name or
# the Content-Type the browser claims.
IMAGE_SIGNATURES = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
}
MAX_IMAGE_BYTES = 3 * 1024 * 1024


def _image_extension(data: bytes) -> str | None:
    for signature, extension in IMAGE_SIGNATURES.items():
        if data.startswith(signature):
            return extension
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


async def _owned(session, auth, product_id: uuid.UUID) -> Product:
    product = await get_product(session, auth.shop.id, product_id)
    if product is None:
        raise NotFound("That product was not found.")
    return product


@router.get("")
async def list_products(
    auth: AuthDep,
    session: SessionDep,
    q: str = Query(default="", max_length=120),
    status: str = Query(default="all", pattern="^(all|active|inactive)$"),
    page: int = Query(default=1, ge=1, le=10_000),
    per_page: int = Query(default=30, ge=1, le=100),
):
    where = [Product.shop_id == auth.shop.id]
    if status != "all":
        where.append(Product.is_active.is_(status == "active"))
    if q.strip():
        term = q.strip()
        where.append(
            or_(
                Product.name.icontains(term, autoescape=True),
                Product.tags.icontains(term, autoescape=True),
                Product.category.icontains(term, autoescape=True),
                Product.brand.icontains(term, autoescape=True),
            )
        )
    total = await session.scalar(select(func.count()).select_from(Product).where(*where))
    rows = await session.scalars(
        select(Product)
        .where(*where)
        .order_by(Product.created_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    return {
        "total": total or 0,
        "page": page,
        "per_page": per_page,
        "items": [ProductOut.model_validate(p) for p in rows],
    }


@router.post("", response_model=ProductOut, status_code=201)
async def add_product(body: ProductIn, auth: ManagerDep, session: SessionDep):
    product = await create_product(session, auth.shop.id, body)
    spawn(refresh_embeddings(auth.shop.id), "refresh-embeddings")
    return product


@router.get("/{product_id}", response_model=ProductOut)
async def read_product(product_id: uuid.UUID, auth: AuthDep, session: SessionDep):
    return await _owned(session, auth, product_id)


@router.patch("/{product_id}", response_model=ProductOut)
async def edit_product(
    product_id: uuid.UUID, body: ProductPatch, auth: ManagerDep, session: SessionDep
):
    product = await _owned(session, auth, product_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("price") is None:
        changes.pop("price", None)
    if "name" in changes and changes["name"] != product.name:
        product.slug = await unique_slug(session, auth.shop.id, changes["name"], product.id)
    for key, value in changes.items():
        setattr(product, key, value)
    product.embedding_model = ""  # recompute the search vector for the new text
    await session.flush()
    await session.refresh(product)
    spawn(refresh_embeddings(auth.shop.id), "refresh-embeddings")
    return product


@router.delete("/{product_id}")
async def remove_product(product_id: uuid.UUID, auth: ManagerDep, session: SessionDep):
    await _owned(session, auth, product_id)
    # Past orders keep their own copy of the item, so deleting is safe.
    await session.execute(
        delete(Product).where(Product.id == product_id, Product.shop_id == auth.shop.id)
    )
    return {"ok": True}


@router.post("/import")
async def import_csv(body: CsvImportIn, auth: ManagerDep, session: SessionDep):
    rows, errors = parse_csv(body.csv)
    counts = await import_products(session, auth.shop.id, rows)
    spawn(refresh_embeddings(auth.shop.id), "refresh-embeddings")
    return {**counts, "errors": errors}


@router.put("/{product_id}/image", response_model=ProductOut)
async def upload_image(
    product_id: uuid.UUID, request: Request, auth: ManagerDep, session: SessionDep
):
    product = await _owned(session, auth, product_id)
    data = await request.body()
    extension = _image_extension(data)
    if extension is None:
        raise AppError("Upload a JPG, PNG or WebP photo.", code="bad_image")
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError("That photo is larger than 3 MB.", code="bad_image")
    folder = Path(settings.UPLOAD_DIR) / "public" / str(auth.shop.id)
    folder.mkdir(parents=True, exist_ok=True)
    # A random name: the uploader never chooses a path on our disk.
    filename = f"{new_token(12)}.{extension}"
    (folder / filename).write_bytes(data)
    product.image_url = f"/uploads/{auth.shop.id}/{filename}"
    await session.flush()
    await session.refresh(product)
    return product


@router.post("/sync/woocommerce")
async def woocommerce_sync(
    body: WooSyncIn, request: Request, auth: ManagerDep, session: SessionDep
):
    counts = await sync_woocommerce(
        session, auth.shop.id, body.url, body.consumer_key, body.consumer_secret
    )
    await audit(
        session,
        "catalog.woocommerce_sync",
        shop_id=auth.shop.id,
        user_id=auth.user.id,
        detail=counts,
        ip=client_ip(request),
    )
    spawn(refresh_embeddings(auth.shop.id), "refresh-embeddings")
    return counts
