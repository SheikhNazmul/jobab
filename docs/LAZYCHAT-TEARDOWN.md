# LazyChat teardown and feature map

This project was asked for as "build something that works like the LazyChat
service." This document records what LazyChat does, how each capability is
rebuilt here, and what is deliberately left out.

## Method, and what was and was not used

LazyChat was studied **only from its public surfaces**: its marketing website
(`lazychat.io`), its public help and documentation pages, and its public app
and plugin listings. No part of LazyChat's own code, design assets, branding,
copy or data was copied or decompiled. Jobab is an **independent,
feature-equivalent reimplementation of the product category** (an AI sales
agent for social commerce), built from scratch under its own name.

This is the ordinary, legitimate way to build a competing product: you can
rebuild *what a product does*; you do not copy *how it was made* or *what it is
called*. Jobab uses none of LazyChat's name, logo, wording or images.

## What LazyChat is (from its public pages)

An AI customer-support and sales assistant for online shops, aimed at
social-commerce sellers. Its public material describes:

- Answering messages across Facebook Messenger, Instagram and WhatsApp, plus a
  website chat widget.
- An AI agent that answers product questions and takes orders.
- Product catalogue management, including syncing from e-commerce platforms
  (Shopify, WooCommerce).
- A shared team inbox with human takeover.
- Automatic replies to comments on posts.
- Order management and customer records.
- Usage-based plans.

## Feature map

| LazyChat capability (public) | In Jobab | Status |
| --- | --- | --- |
| AI answers customer messages | `agent/` reply loop with tools over the shop's catalogue | **Built** |
| Facebook Messenger | `channels/adapters/meta.py`, webhook worker | **Built** (needs a Meta app + token to go live) |
| Instagram DMs | same Page webhook + Send API path | **Built** (needs connection) |
| WhatsApp | `channels/adapters/whatsapp.py` (Cloud API) | **Built** (needs connection) |
| Website chat widget | `web/widget/`, `channels/widget.py` | **Built, works out of the box** |
| Takes orders in chat | quote → confirm → order, enforced in `orders/service.py` | **Built** |
| Catalogue: manual | products CRUD | **Built** |
| Catalogue: CSV import | `catalog/service.py` `parse_csv` | **Built** |
| Catalogue: WooCommerce sync | `catalog/woocommerce.py` (read-only REST) | **Built** |
| Catalogue: Shopify sync | — | **Not built** (WooCommerce covers the same shape; Shopify is a similar adapter) |
| Shared inbox + human takeover | `inbox/`, live stream, take over / give back | **Built** |
| Comment auto-reply (public + private) | webhook worker + `meta.send_comment_reply` / `send_private_reply` | **Built** (fixed text, not AI) |
| Leads | `crm/` | **Built** |
| Complaints / escalation | `crm/` + hand-off | **Built** |
| Order management (status, courier, tracking) | `orders/router.py` | **Built** |
| Delivery zones and charges | shop settings, used by the agent | **Built** |
| Bangla / Banglish support | prompt + search tuned for it | **Built** |
| Usage plans / credits | shop credits, admin top-up, trial | **Built** (billing integration not included) |
| Dashboard | `web/app/` SPA | **Built** |
| Meaning-based product search | optional pgvector embeddings | **Built, optional** |
| Voice notes (speech to text) | optional provider call, media fetch wired | **Partial** (off unless configured) |
| Marketing homepage | `web/index.html` | **Built** |

## What is deliberately different or omitted

- **Billing / payment of the shop's subscription** is out of scope. The credit
  and plan model is here (and an admin can grant credits), but no payment
  gateway is wired. That is a business decision, not a technical gap.
- **Shopify sync** is not built; WooCommerce is. The two are near-identical
  adapters, so adding Shopify follows the same shape.
- **Comment auto-reply uses fixed, shop-set text**, not the AI, to keep tone and
  cost under control. LazyChat's exact behaviour here is not public in detail.
- **A click-through Facebook onboarding** (Facebook Login) is not built;
  connection is by pasted access token. The token handling and webhook plumbing
  it would feed are already in place.

## Honesty note

Numbers, testimonials and any specific traction claims from LazyChat's site were
**not** reproduced anywhere in Jobab. The Jobab homepage states only what the
software actually does.
