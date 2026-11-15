# ---------- Stage 1: build the virtualenv ----------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Only the manifests first, so the dependency layer caches until they change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim

RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --chown=app:app app/ ./app/
COPY --chown=app:app web/ ./web/
COPY --chown=app:app alembic/ ./alembic/
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app scripts/entrypoint.sh ./scripts/entrypoint.sh
RUN chmod +x ./scripts/entrypoint.sh && mkdir -p /app/data/uploads && chown -R app:app /app/data

USER app
EXPOSE 8000
CMD ["./scripts/entrypoint.sh"]
