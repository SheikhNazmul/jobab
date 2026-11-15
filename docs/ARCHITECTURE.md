# Architecture

One FastAPI process serves everything and runs a background worker. This is the
path a customer message takes.

## A message becomes a reply and an order

```
Customer (Messenger / IG / WhatsApp)                Website visitor
        |                                                   |
   Meta webhook  --POST /webhooks/meta-->          POST /api/widget/{key}/message
        |  (signature checked, stored raw)                  |
        v                                                   v
   webhook_events (durable queue)                    channels/ingest.ingest()
        |                                                   |
   webhooks/worker.drain_once()  ----------------->  stores the inbound Message,
        |  parses with the channel adapter                 updates the conversation,
        v                                                  tells the dashboard (SSE)
   channels/ingest.ingest()                                 |
        |                                                   |
        +----------------> agent/dispatch.schedule_reply() <+
                                   |  (debounce: coalesces a burst)
                                   v
                           agent/runtime.generate_reply()
                             - is the AI on, not muted, under the cap?  (else stop)
                             - spend one credit (atomic)                 (else hand off)
                             - build prompt from shop settings + history
                             - loop: model -> tools -> model ...
                                   tools: search / quote / place_order / lead /
                                          complaint / handoff  (agent/tools.py)
                             - record the reply Message
                                   |
                                   v
                           agent/dispatch delivers it:
                             webchat -> SSE to the visitor's tab
                             meta/whatsapp -> Send API, update delivery status
```

## Why it is split this way

- **Store the webhook before doing anything.** Meta wants a fast `200`, and a
  crash must not lose a customer's message. The raw event is saved, then a
  worker processes it. A repeat delivery is recognised and skipped at the
  message level (`messages.external_id` is unique per conversation).
- **Debounce replies.** Customers send bursts of short messages. The dispatch
  layer waits a couple of seconds after the last one and answers them together.
- **Generate, commit, then deliver.** `generate_reply` records the reply and
  spends the credit in one transaction. Only after that commits is the text
  handed to the channel. A failed send never double-charges a credit or loses
  the record.
- **Guardrails live in services, not the prompt.** Pricing and the
  confirm-before-order rule are in `orders/service.py`, so they hold regardless
  of what the model does. See `SECURITY.md` part 2.

## Data model (one tenant = one shop)

`shops` is the tenant. `users` join to shops through `memberships` with a role
(owner / manager / agent). Everything else (`channels`, `contacts`,
`conversations`, `messages`, `products`, `orders`, `order_quotes`, `leads`,
`complaints`, `comment_logs`, `usage_events`, `audit_logs`) carries `shop_id`
and is always queried by the shop from the session.

## Live updates

Server-Sent Events (`app/core/events.py`). The dashboard subscribes to
`shop:{id}` for new messages, orders, hand-offs, leads and complaints. A website
visitor's tab subscribes to `conversation:{id}` for its own replies, with a
polling fallback for networks that drop long connections.

## Storage

Postgres is the only datastore. Uploaded product images go to `UPLOAD_DIR` on
disk (a mounted volume in Docker). There is no Redis or message broker; the
webhook queue is a Postgres table.
