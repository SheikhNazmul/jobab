"""Calls to the AI provider.

Any provider with an OpenAI-compatible API works (OpenAI, Groq, Google's
Gemini compatibility endpoint, OpenRouter, a local server). Nothing in this
file knows about shops or customers; it only sends and receives.
"""

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.http import http_client

log = logging.getLogger("app.llm")
settings = get_settings()

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


class LLMError(Exception):
    """The provider could not be reached or refused the request."""


def _base(url: str) -> str:
    return (url or settings.LLM_BASE_URL).rstrip("/")


def _auth(key: str = "") -> dict[str, str]:
    token = key or settings.LLM_API_KEY.get_secret_value()
    return {"Authorization": f"Bearer {token}"}


async def _post(url: str, *, headers: dict[str, str], **kwargs: Any) -> dict[str, Any]:
    last_error = "no attempt made"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await http_client().post(
                url, headers=headers, timeout=settings.LLM_TIMEOUT_SECONDS, **kwargs
            )
        except httpx.HTTPError as exc:
            last_error = f"network error: {type(exc).__name__}"
        else:
            if response.status_code < 300:
                return response.json()
            # The body can echo our prompt back, so only a short slice is
            # logged and nothing is shown to end users.
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            if response.status_code not in RETRY_STATUS:
                break
        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(0.6 * attempt)
    log.warning("AI provider call failed: %s", last_error)
    raise LLMError(last_error)


async def chat(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """One chat-completion round. Returns {"message": ..., "usage": ...}."""
    if not settings.llm_configured:
        raise LLMError("The AI provider is not configured.")
    body: dict[str, Any] = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        settings.LLM_TOKEN_PARAM: settings.LLM_MAX_TOKENS,
    }
    if settings.LLM_TEMPERATURE is not None:
        body["temperature"] = settings.LLM_TEMPERATURE
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    data = await _post(f"{_base('')}/chat/completions", headers=_auth(), json=body)
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("The AI provider sent an answer in an unexpected shape.") from exc
    return {"message": message, "usage": data.get("usage") or {}}


async def embed(texts: list[str]) -> list[list[float]]:
    """Turns texts into vectors for meaning-based product search."""
    if not settings.EMBEDDING_MODEL:
        raise LLMError("No embedding model is configured.")
    data = await _post(
        f"{_base(settings.EMBEDDING_BASE_URL)}/embeddings",
        headers=_auth(settings.EMBEDDING_API_KEY.get_secret_value()),
        json={"model": settings.EMBEDDING_MODEL, "input": texts},
    )
    try:
        rows = sorted(data["data"], key=lambda row: row.get("index", 0))
        vectors = [row["embedding"] for row in rows]
    except (KeyError, TypeError) as exc:
        raise LLMError("The embedding answer had an unexpected shape.") from exc
    if len(vectors) != len(texts):
        raise LLMError("The embedding answer did not match the request.")
    return vectors


async def transcribe(audio: bytes, mime: str, filename: str = "voice.ogg") -> str:
    """Speech to text for voice notes."""
    if not settings.stt_configured:
        raise LLMError("Voice transcription is not configured.")
    data = await _post(
        f"{_base(settings.STT_BASE_URL)}/audio/transcriptions",
        headers=_auth(settings.STT_API_KEY.get_secret_value()),
        files={"file": (filename, audio, mime or "audio/ogg")},
        data={"model": settings.STT_MODEL},
    )
    return str(data.get("text") or "").strip()
