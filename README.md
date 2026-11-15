# Jobab

An AI sales assistant for social-commerce shops. It answers customers on
Facebook Messenger, Instagram, WhatsApp and website chat, in Bangla and
English, quotes from the shop's own catalogue, and takes orders. A person on
the team steps in only when it matters.

It is a feature-equivalent, independently built alternative to LazyChat, under
its own brand. See [`docs/LAZYCHAT-TEARDOWN.md`](docs/LAZYCHAT-TEARDOWN.md) for
how its features map to this build and what is and is not included.

---

## What it does

- **One inbox for every channel.** Messenger, Instagram, WhatsApp and a website
  chat widget land in one place. The team sees every thread and can take over
  any chat from the AI at any time.
- **An AI that sells, grounded in the shop.** It searches the shop's catalogue,
  answers price/size/stock/delivery questions, and walks the customer to a
  finished order. Prices and stock come only from the catalogue, never from the
  chat.
- **Orders, safely.** The AI shows an itemised total and places an order only
  after the customer confirms. Big orders can be held for a person to review.
- **Leads and complaints.** It saves interested customers for follow-up, and
  hands any complaint or refund straight to a person.
- **Comment auto-reply.** Public comments on Facebook and Instagram posts get a
  reply and, optionally, a private message.
- **A dashboard.** Inbox, orders, products (manual, CSV import, or WooCommerce
  sync), leads, complaints, channels, settings, team and a platform-admin view.
- **A marketing homepage** and an embeddable chat widget.

## How it is built

- **Python 3.12, FastAPI, SQLAlchemy (async), Postgres + pgvector, Alembic.**
  Follows the module layout of the course scaffold it was grown from.
- **One process** serves the API, the dashboard, the homepage, the chat widget
  and the background webhook worker, so a small shop's deployment stays simple.
- **The AI provider is any OpenAI-compatible API** (OpenAI, Groq, OpenRouter,
  Gemini's compatibility endpoint, or a local server). Nothing is hard-wired to
  one vendor.
- **No frontend build step.** The dashboard and homepage are plain HTML, CSS and
  JavaScript served as static files.

```
app/
  core/        config, security, errors, middleware, HTTP (SSRF guard), rate limit, SSE
  db/          async engine, base model, migrations wiring
  modules/
    users/     accounts, sessions, roles, auth
    shops/     the tenant: settings, members, credits, audit log
    catalog/   products, search (word + optional vector), CSV, WooCommerce sync
    channels/  Messenger/Instagram/WhatsApp/webchat adapters, ingest, widget API
    inbox/     contacts, conversations, messages, human takeover, live stream
    agent/     the reply loop: prompt, tools, runtime, dispatch (debounce + deliver)
    orders/    quotes and orders (the confirm-before-order rule lives here)
    crm/       leads, complaints, comment logs
    analytics/ dashboard overview
    webhooks/  Meta webhook (signature-checked) and the background worker
    admin/     platform operator view
web/           homepage, dashboard SPA, chat widget, self-hosted fonts
scripts/       entrypoint, init-db, demo seed
tests/         end-to-end order flow and security checks
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how a message becomes a
reply and an order.

---

## Run it with Docker (simplest)

You need Docker and an OpenAI-compatible API key for the AI to reply.

```bash
cp .env.example .env
# edit .env: set SECRET_KEY, and LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
docker compose up --build
```

- Homepage: <http://localhost:8002>
- Dashboard: <http://localhost:8002/app>
- API docs (development only): <http://localhost:8002/docs>

Migrations run automatically on start. To load a demo shop with products:

```bash
docker compose exec backend python -m scripts.seed_demo
```

It prints a login (`demo@jobab.shop` / `demo-password-123`) and a website-chat
key you can try at `/widget/frame?k=...`.

## Run it locally (without Docker)

Needs Python 3.12+, [uv](https://docs.astral.sh/uv/), and a Postgres with the
`pgvector` extension.

```bash
uv sync
createdb sales_agent && psql sales_agent -f scripts/init-db.sql
cp .env.example .env            # set DB_HOST=127.0.0.1 and your AI keys
uv run alembic upgrade head
uv run python -m scripts.seed_demo      # optional demo data
uv run uvicorn app.main:app --port 8002 --reload
```

## Configuration

Every setting is an environment variable, documented in
[`.env.example`](.env.example). The essentials:

| Variable | What it is |
| --- | --- |
| `SECRET_KEY` | Random 32+ char string. Encrypts channel tokens. Required in production. |
| `PUBLIC_BASE_URL` | The https address the app is served on. |
| `DB_*` | Postgres connection. |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Your AI provider. |
| `META_APP_SECRET`, `META_VERIFY_TOKEN` | For connecting Messenger/Instagram/WhatsApp. |
| `ADMIN_EMAIL` | The account that sees the platform-admin screens. |

The app **refuses to start in production** with a development secret key, debug
on, a non-https base URL, a default database password, or the SSRF guard
disabled. That check is in `app/core/config.py`.

## Connecting channels and going live

The website chat works out of the box. Connecting Messenger, Instagram and
WhatsApp needs a Meta app, webhook subscription and access tokens. The full
steps, plus the WhatsApp and Messenger messaging-window and pricing rules you
must know, are in [`docs/GO-LIVE.md`](docs/GO-LIVE.md).

## Tests

```bash
uv run pytest        # needs a Postgres; see tests/conftest.py
```

Covers an end-to-end order placed by the AI over website chat, and the security
behaviours (tenant isolation, CSRF, webhook signatures, the no-quote-no-order
rule, production-config safety).

## Security

Security and safety are documented in [`SECURITY.md`](SECURITY.md): how tenant
isolation, secrets, the webhook signature gate, the AI guardrails and the
privacy choices work, and how to report an issue.

## License

Provided to the repository owner for their own use. No warranty.
