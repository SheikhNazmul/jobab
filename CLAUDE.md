# CLAUDE.md — Jobab

Project context for Claude Code. Read this first; follow it over guesses.

## What this is

Jobab — a multi-tenant AI sales assistant for social-commerce shops. It answers
customers on Messenger, Instagram, WhatsApp and website chat, quotes from the
shop's own catalogue, and takes orders. One FastAPI process serves the API, the
dashboard, the homepage, the chat widget and a background webhook worker.

Stack: Python 3.12, FastAPI, async SQLAlchemy, PostgreSQL 16 + pgvector,
Alembic, `uv`, pytest, ruff. No frontend build step (plain HTML/CSS/JS in `web/`).

## Commands

- Install deps: `uv sync`
- Env: `cp .env.example .env`, then set `SECRET_KEY`, the `DB_*` vars, and
  `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` (any OpenAI-compatible provider).
- Run everything (simplest): `docker compose up --build`
- Run the app against a local Postgres: `uv run uvicorn app.main:app --reload`
- Migrate the database: `uv run alembic upgrade head`
- Tests: `uv run pytest -q` — needs a real PostgreSQL (the models use pgvector,
  JSONB and real constraints; SQLite is not a fair test). Set `DEBUG=false`
  while testing so SQL echo does not slow the end-to-end tests.
- Lint and format: `uv run ruff check .` and `uv run ruff format .`

Run `ruff check .` and `pytest -q` before you commit. Branch for any change.

## Layout and conventions

- `app/modules/<name>/` is one feature module, each with the same shape:
  `models.py` → `schemas.py` → `router.py` → `service.py`. Business logic lives
  in `service.py`; routers stay thin.
- Register **every** SQLAlchemy model in `app/db/all_models.py`. Alembic
  autogenerate and the test schema (`Base.metadata.create_all`) both depend on
  it — a model missing there silently will not get a table.
- Any schema change needs an Alembic migration in `alembic/versions/`
  (`uv run alembic revision -m "..."`, then edit it). Tests build the schema from
  the models, production runs the migrations, so keep the two in step.
- `app/core/` holds config, security, crypto, rate limiting, the SSRF guard and
  errors. Per-tenant settings are `ShopSettings` (pydantic), stored as JSONB on
  `Shop.settings`.
- Agent tools: add a schema + handler in `app/modules/agent/tools.py` and
  register it in `_HANDLERS`; the system prompt is in `app/modules/agent/prompt.py`.

## Hard rules — do not break these

These are enforced in code, not just asked for in the prompt. Keep them that way.

- **Tenant isolation.** Every query is filtered by `shop_id`. Never return or
  write another shop's data.
- **Prices and stock come only from the catalogue**, never from the chat or the
  model. Discounts come only from shop-defined records (e.g. a `Bundle`); the AI
  can pitch a bundle but cannot invent a price or a discount.
- **Confirm before ordering.** An order is placed only through a valid, unused
  `quote_id` made earlier in the same conversation (`orders/service.py`). Do not
  add a path that places an order without a confirmed quote.
- **Secrets** (provider keys, channel tokens) are encrypted at rest; `SECRET_KEY`
  lives only in the environment. Never log or commit secrets.
- **Webhooks** are verified by signature before they are trusted.
- The app has a **production config gate** that refuses to start with a weak
  secret or debug left on. Do not weaken it.

## Current state

Working and tested: website chat end to end, the AI order flow with its
guardrails, Messenger/Instagram/WhatsApp adapters (need Meta tokens to go live),
credits, security core, and smart upsell + bundles (`catalog.Bundle`,
`suggest_related` / `get_bundle_offer` tools, bundle pricing in
`orders.build_quote`). See `docs/ARCHITECTURE.md` and `docs/GO-LIVE.md`.
