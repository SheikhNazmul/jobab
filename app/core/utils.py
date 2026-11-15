"""Small helpers shared by several modules."""

import re
import unicodedata
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import Request

BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def utcnow() -> datetime:
    return datetime.now(UTC)


def client_ip(request: Request) -> str:
    """The caller's address. Behind a reverse proxy, run uvicorn with
    --proxy-headers so this is the real client and not the proxy."""
    return request.client.host if request.client else ""


def clean_text(value: str | None, max_length: int) -> str:
    """Strips control characters and trims to a safe length."""
    text = CONTROL_CHARS.sub("", value or "").strip()
    return text[:max_length]


def slugify(value: str) -> str:
    """Lowercase words joined by dashes. Works for Bangla as well as English:
    letters, digits and combining vowel signs are kept, the rest is dropped."""
    text = unicodedata.normalize("NFKC", value).strip().lower()
    kept = []
    for char in text:
        if unicodedata.category(char)[0] in "LMN":
            kept.append(char)
        elif char in " -_/\t":
            kept.append("-")
    slug = re.sub(r"-+", "-", "".join(kept)).strip("-")
    return slug[:200] or "item"


def money(value: Decimal | int | float | str | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def money_number(value: Decimal | None) -> int | float | None:
    """A JSON-friendly number: 850 instead of 850.00, 849.5 stays 849.5."""
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def normalize_phone(raw: str, country: str = "BD") -> str | None:
    """Returns the phone in a standard form, or None when it is not valid.

    Customers type numbers many ways: Bangla digits, spaces, dashes, +880.
    For Bangladesh the result is always 11 digits starting with 01.
    """
    digits = re.sub(r"\D", "", (raw or "").translate(BANGLA_DIGITS))
    if country.upper() == "BD":
        if digits.startswith("880"):
            digits = "0" + digits[3:]
        elif len(digits) == 10 and digits.startswith("1"):
            digits = "0" + digits
        return digits if re.fullmatch(r"01[3-9]\d{8}", digits) else None
    return digits if 7 <= len(digits) <= 15 else None
