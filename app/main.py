"""The application: middleware, routes, static files and the background worker.

Run it with `uvicorn app.main:app`. One process serves the API, the dashboard,
the marketing homepage, the chat widget and the webhook worker, which keeps a
small shop's deployment simple.
"""

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.http import close_http_client
from app.core.middleware import SecurityMiddleware
from app.core.tasks import cancel_all, drain
from app.modules.admin.router import router as admin_router
from app.modules.analytics.router import router as analytics_router
from app.modules.catalog.router import router as catalog_router
from app.modules.channels.router import router as channels_router
from app.modules.channels.widget import router as widget_router
from app.modules.crm.router import router as crm_router
from app.modules.inbox.router import router as inbox_router
from app.modules.orders.router import router as orders_router
from app.modules.public.router import router as public_router
from app.modules.shops.router import router as shops_router
from app.modules.users.router import router as users_router
from app.modules.webhooks.router import router as webhooks_router
from app.modules.webhooks.worker import worker_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
settings = get_settings()

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
UPLOAD_PUBLIC = Path(settings.UPLOAD_DIR) / "public"


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    UPLOAD_PUBLIC.mkdir(parents=True, exist_ok=True)
    stop = asyncio.Event()
    worker = asyncio.create_task(worker_loop(stop), name="webhook-worker")
    try:
        yield
    finally:
        stop.set()
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        await drain(timeout=10)
        await cancel_all()
        await close_http_client()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    lifespan=lifespan,
)

app.add_middleware(SecurityMiddleware)
install_error_handlers(app)

for router in (
    users_router, shops_router, channels_router, catalog_router, inbox_router,
    orders_router, crm_router, analytics_router, admin_router, public_router,
    widget_router, webhooks_router,
):
    app.include_router(router)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"ok": True, "app": settings.APP_NAME}


# --- static files and single-page apps --------------------------------------------

if UPLOAD_PUBLIC.exists():
    app.mount("/uploads", StaticFiles(directory=str(UPLOAD_PUBLIC)), name="uploads")

if (WEB_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets")), name="assets")


def _file(*parts: str) -> Path:
    return WEB_DIR.joinpath(*parts)


@app.get("/", include_in_schema=False)
async def homepage() -> Response:
    page = _file("index.html")
    return FileResponse(page) if page.exists() else PlainTextResponse(
        f"{settings.APP_NAME} is running. The homepage has not been built yet."
    )


@app.get("/widget.js", include_in_schema=False)
async def widget_script() -> Response:
    script = _file("widget", "widget.js")
    if not script.exists():
        return PlainTextResponse("// widget not built", media_type="application/javascript")
    return FileResponse(script, media_type="application/javascript")


@app.get("/widget/frame", include_in_schema=False)
async def widget_frame() -> Response:
    frame = _file("widget", "frame.html")
    return FileResponse(frame) if frame.exists() else PlainTextResponse("widget not built")


@app.get("/widget/frame.js", include_in_schema=False)
async def widget_frame_script() -> Response:
    script = _file("widget", "frame.js")
    if not script.exists():
        return PlainTextResponse("// widget not built", media_type="application/javascript")
    return FileResponse(script, media_type="application/javascript")


# The dashboard is a single-page app: every /app/... path serves its shell and
# the app routes on the client.
@app.get("/app", include_in_schema=False)
@app.get("/app/{path:path}", include_in_schema=False)
async def dashboard(path: str = "") -> Response:
    asset = _file("app", path)
    if path and asset.is_file() and WEB_DIR / "app" in asset.parents:
        return FileResponse(asset)
    shell = _file("app", "index.html")
    return FileResponse(shell) if shell.exists() else PlainTextResponse(
        "The dashboard has not been built yet."
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    icon = _file("favicon.svg")
    return FileResponse(icon) if icon.exists() else Response(status_code=204)
