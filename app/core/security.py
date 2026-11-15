"""Passwords, session tokens, secrets at rest and webhook signatures."""

import asyncio
import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings
from app.core.errors import AppError

settings = get_settings()

# Argon2id with the library's recommended cost. Tests use a cheap setting so
# the suite stays fast; the algorithm and code path are the same.
_hasher = (
    PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1)
    if settings.ENVIRONMENT == "test"
    else PasswordHasher()
)
# Verified when the email is unknown, so a wrong email and a wrong password
# take the same time and cannot be told apart.
_DUMMY_HASH = _hasher.hash("not-a-real-password")

COMMON_PASSWORDS = frozenset(
    {
        "1234567890",
        "12345678910",
        "0123456789",
        "0987654321",
        "1q2w3e4r5t",
        "qwertyuiop",
        "password12",
        "password123",
        "password1234",
        "iloveyou123",
        "bangladesh",
        "bangladesh1",
        "bangladesh123",
        "administrator",
        "letmein1234",
        "welcome123",
        "abcdefghij",
        "aaaaaaaaaa",
        "1111111111",
        "0000000000",
    }
)


def validate_new_password(password: str, email: str = "") -> None:
    """Length matters more than symbols: 10+ characters, not a known-bad one."""
    if len(password) < 10:
        raise AppError("Use a password with at least 10 characters.", code="weak_password")
    if len(password) > 128:
        raise AppError("Use a password with at most 128 characters.", code="weak_password")
    lowered = password.lower()
    local_part = email.split("@")[0].lower()
    if lowered in COMMON_PASSWORDS or len(set(lowered)) < 4:
        raise AppError("That password is too easy to guess. Choose another.", code="weak_password")
    if len(local_part) >= 4 and local_part in lowered:
        raise AppError("Do not use your email inside your password.", code="weak_password")


def _hash(password: str) -> str:
    return _hasher.hash(password)


def _verify(password: str, hashed: str | None) -> bool:
    try:
        return _hasher.verify(hashed or _DUMMY_HASH, password) and hashed is not None
    except (Argon2Error, InvalidHashError):
        return False


async def hash_password(password: str) -> str:
    # Hashing is slow on purpose; keep it off the event loop.
    return await asyncio.to_thread(_hash, password)


async def verify_password(password: str, hashed: str | None) -> bool:
    return await asyncio.to_thread(_verify, password, hashed)


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# --- secrets at rest -----------------------------------------------------------


def _fernet() -> Fernet:
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"sales-agent/secrets-at-rest/v1",
    ).derive(settings.SECRET_KEY.get_secret_value().encode())
    return Fernet(base64.urlsafe_b64encode(key))


_cipher = _fernet()


def encrypt_secret(plain: str) -> str:
    return _cipher.encrypt(plain.encode()).decode() if plain else ""


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _cipher.decrypt(token.encode()).decode()
    except InvalidToken as exc:
        # Happens when SECRET_KEY changed after the value was stored.
        raise AppError(
            "A saved access token can no longer be read. Reconnect the channel.",
            code="secret_unreadable",
            status_code=409,
        ) from exc


def mask_secret(plain: str) -> str:
    """Enough to recognise a token on a settings screen, never the token."""
    return f"••••{plain[-4:]}" if plain else ""


# --- webhook signatures ----------------------------------------------------------


def verify_hmac_sha256(secret: str, body: bytes, header: str | None) -> bool:
    """Checks Meta's `X-Hub-Signature-256: sha256=<hex>` header."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))
