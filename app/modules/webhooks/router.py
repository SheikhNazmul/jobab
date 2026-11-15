"""The Meta webhook: one URL for Messenger, Instagram and WhatsApp.

GET verifies the subscription with the token the shop set. POST receives
events; every POST is checked against the app secret before it is stored, so
only Meta can post here. Work is done later by the worker, so the reply here is
immediate.
"""

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.core.security import constant_time_equals, verify_hmac_sha256
from app.db.session import SessionDep
from app.modules.webhooks.worker import store_event

log = logging.getLogger("app.webhooks")
settings = get_settings()
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

KNOWN_OBJECTS = {"page", "instagram", "whatsapp_business_account"}


@router.get("/meta")
async def verify(request: Request) -> Response:
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")
    expected = settings.META_VERIFY_TOKEN.get_secret_value()
    if mode == "subscribe" and expected and constant_time_equals(token, expected):
        return PlainTextResponse(challenge)
    return PlainTextResponse("Verification failed", status_code=403)


@router.post("/meta")
async def receive(request: Request, session: SessionDep) -> Response:
    raw = await request.body()
    secret = settings.META_APP_SECRET.get_secret_value()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_hmac_sha256(secret, raw, signature):
        log.warning("Rejected webhook with bad or missing signature")
        return Response(status_code=403)

    # The body names which product it is for ("page", "instagram", ...).
    import json

    try:
        object_type = json.loads(raw).get("object", "")
    except (json.JSONDecodeError, AttributeError):
        return Response(status_code=200)  # ignore, but do not make Meta retry
    if object_type in KNOWN_OBJECTS:
        await store_event(session, object_type, raw)
    return Response(status_code=200)
