"""Outbound HTTP: one shared client and a guard against SSRF.

SSRF (server-side request forgery) is when someone gets our server to fetch a
URL of their choosing, for example an internal address. Two places take URLs
from outside: media links inside platform webhooks and the store address a
merchant types for a WooCommerce sync. Both go through `assert_public_url`.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings
from app.core.errors import AppError

settings = get_settings()

# Hosts Meta serves message attachments from.
META_MEDIA_HOSTS = (
    ".fbcdn.net",
    ".fbsbx.com",
    ".facebook.com",
    ".cdninstagram.com",
    ".instagram.com",
    ".whatsapp.net",
    ".whatsapp.com",
)

_client: httpx.AsyncClient | None = None
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Tests swap the network for a fake by setting a transport here."""
    global _client, _transport
    _transport = transport
    _client = None


def http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            # Never follow redirects: a public URL must not bounce us to a
            # private address after it passed the check below.
            follow_redirects=False,
            transport=_transport,
            headers={"User-Agent": "sales-agent/0.1"},
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _host_allowed(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s.lstrip(".") or host.endswith(s) for s in suffixes)


async def assert_public_url(url: str, *, host_suffixes: tuple[str, ...] | None = None) -> str:
    """Raises unless the URL is https and points at a public internet address."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise AppError("Enter a full web address that starts with https://", code="bad_url")
    if parts.username or parts.password:
        raise AppError(
            "Web addresses with a username or password are not accepted.", code="bad_url"
        )
    if settings.ALLOW_PRIVATE_URLS:
        return url
    if parts.scheme != "https":
        raise AppError("Only https:// addresses are accepted.", code="bad_url")
    if host_suffixes and not _host_allowed(host, host_suffixes):
        raise AppError("That address is not on the allowed list.", code="bad_url")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, parts.port or 443, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise AppError("That address could not be found.", code="bad_url") from exc
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise AppError("That address is not reachable from the internet.", code="bad_url")
    return url
