"""Request-level protections that apply to every route.

- security headers on every response
- a size limit on request bodies
- a cross-site request check (CSRF) for the cookie-authenticated API
"""

import secrets
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.errors import AppError, error_body

settings = get_settings()

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# These API paths do not use the session cookie, so CSRF does not apply:
# the widget authenticates with a visitor token header, and the public form
# is open to everyone (and rate limited).
CSRF_EXEMPT_PREFIXES = ("/api/widget/", "/api/public/")

MAX_JSON_BODY = 1 * 1024 * 1024
MAX_UPLOAD_BODY = 4 * 1024 * 1024

BASE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; font-src 'self' data:; connect-src 'self'; "
    "frame-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'"
)
# The homepage is one self-contained file with inline styles and scripts.
SITE_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
# Swagger UI loads its script and styles from a CDN. Only served when API
# docs are enabled, which is off in production by default.
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
)


class BodyTooLarge(AppError):
    status_code = 413
    code = "body_too_large"


def _body_limit(path: str) -> int:
    return MAX_UPLOAD_BODY if path.endswith("/image") or path.endswith("/import") else MAX_JSON_BODY


def _security_headers(path: str) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    if settings.cookie_secure:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    if path == "/widget/frame":
        # The chat frame is meant to be embedded on the shop's own website.
        headers["Content-Security-Policy"] = BASE_CSP + "; frame-ancestors *"
    elif path == "/widget.js":
        headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    elif path == "/":
        headers["Content-Security-Policy"] = SITE_CSP
        headers["X-Frame-Options"] = "DENY"
    elif path in ("/docs", "/redoc"):
        headers["Content-Security-Policy"] = DOCS_CSP
        headers["X-Frame-Options"] = "DENY"
    else:
        headers["Content-Security-Policy"] = BASE_CSP + "; frame-ancestors 'none'"
        headers["X-Frame-Options"] = "DENY"

    if path.startswith("/api/") or path.startswith("/webhooks/"):
        headers["Cache-Control"] = "no-store"
    return headers


def _csrf_problem(headers: Headers) -> str | None:
    """Why a state-changing request looks cross-site, or None when it is fine.

    Browsers cannot add a custom header to a cross-site request without a
    CORS preflight, and this app answers no preflights. So a request that
    carries `X-CSRF: 1` came from our own pages. The Origin check is a second
    lock on the same door.
    """
    if headers.get("x-csrf") != "1":
        return "This request did not come from the dashboard."
    origin = headers.get("origin")
    if origin and origin != "null":
        origin_host = urlsplit(origin).netloc
        allowed = {headers.get("host", ""), urlsplit(settings.base_url).netloc}
        if origin_host not in allowed:
            return "Cross-site requests are not accepted."
    return None


class SecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        headers = Headers(scope=scope)
        request_id = secrets.token_hex(8)
        scope.setdefault("state", {})["request_id"] = request_id
        response_started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                out = MutableHeaders(scope=message)
                for name, value in _security_headers(path).items():
                    if name not in out:
                        out[name] = value
                out["X-Request-ID"] = request_id
            await send(message)

        async def reject(status: int, code: str, message: str) -> None:
            response = JSONResponse(error_body(code, message), status_code=status)
            await response(scope, receive, send_with_headers)

        if (
            scope["method"] in UNSAFE_METHODS
            and path.startswith("/api/")
            and not path.startswith(CSRF_EXEMPT_PREFIXES)
        ):
            problem = _csrf_problem(headers)
            if problem:
                await reject(403, "csrf_failed", problem)
                return

        limit = _body_limit(path)
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            await reject(413, "body_too_large", "That upload is too large.")
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge("That upload is too large.")
            return message

        try:
            await self.app(scope, limited_receive, send_with_headers)
        except BodyTooLarge as exc:
            if response_started:
                raise
            await reject(exc.status_code, exc.code, exc.message)
