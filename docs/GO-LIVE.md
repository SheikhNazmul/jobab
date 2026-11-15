# Going live: connecting channels and the rules you must know

The website chat works as soon as the app is running. Connecting Messenger,
Instagram and WhatsApp needs a Meta app and a few steps, and there are
**messaging-window and pricing rules you must understand** before you turn the
AI loose on those channels. This page covers both.

> Meta changes its product screens and policies often. Treat the step names
> here as a guide and follow the current labels in the Meta dashboards. The
> policy facts below were current as of early 2026; re-check Meta's own pages
> before launch.

---

## 1. Website chat (works now)

In the dashboard, open **Channels → Website chat** and copy the snippet:

```html
<script src="https://YOUR_DOMAIN/widget.js" data-key="wk_xxx" defer></script>
```

Paste it before `</body>` on the shop's website. The widget talks only to your
Jobab server. Rotate the key any time from the same screen if it leaks.

---

## 2. Connect Messenger, Instagram and WhatsApp

### a. Create a Meta app

1. At <https://developers.facebook.com>, create a **Business** app.
2. Add the products you need: **Messenger**, **Instagram**, and/or
   **WhatsApp**.
3. In your Jobab `.env`, set:
   - `META_APP_SECRET` — the app secret (used to verify every webhook).
   - `META_VERIFY_TOKEN` — any random string you choose.

### b. Point Meta's webhook at Jobab

For each product's webhook settings:

- **Callback URL**: `https://YOUR_DOMAIN/webhooks/meta`
- **Verify token**: the same `META_VERIFY_TOKEN`.
- **Subscribe to fields**:
  - Messenger / Instagram: `messages`, `messaging_postbacks`, and `feed`
    (Facebook) / `comments` (Instagram) if you want comment auto-reply.
  - WhatsApp: `messages`.

Jobab answers the verification handshake automatically and checks the signature
on every event, so an unsigned request is dropped.

### c. Get the access tokens and connect

- **Messenger**: generate a **Page access token** for the shop's Page. In Jobab,
  **Channels → Connect**, choose Messenger, enter the **Page ID** and the token.
- **Instagram**: connect the Instagram professional account to the Page; use the
  Instagram account ID and token the same way.
- **WhatsApp**: from WhatsApp → API setup, copy the **phone number ID** and a
  token; enter them under WhatsApp.

Tokens are encrypted at rest and never shown again (only a `••••last4` mask).

### d. App Review

Sending to real customers needs Meta **App Review** for the messaging
permissions (`pages_messaging`, `instagram_manage_messages`,
`whatsapp_business_messaging`, and related). Note that Meta **does not allow
automating the Human Agent path** — it is for genuine human replies — so Jobab
uses it only as the hand-off window, not for AI messages.

---

## 3. The messaging-window and pricing rules (important)

These are Meta's rules, not Jobab's, and they decide what you may send and what
it costs. Jobab is built to stay inside the free, allowed paths and to **never
auto-send promotional messages outside a window**.

### Messenger and Instagram: the 24-hour window

- When a customer messages the Page/account, a **24-hour standard messaging
  window** opens and resets with each new customer message. Inside it, the AI
  may reply freely. Jobab sends its replies as `messaging_type: RESPONSE`, which
  is exactly this case.
- **Message Tags have been largely retired.** Through 2026 Meta removed the
  `ACCOUNT_UPDATE`, `CONFIRMED_EVENT_UPDATE` and `POST_PURCHASE_UPDATE` tags;
  requests still carrying them are rejected (error code 100). So you cannot rely
  on tags to message a customer after the window. The **`HUMAN_AGENT`** path
  (a 7-day window for genuine human replies) still exists and is what a person
  on your team uses to answer late; the AI does not use it.
- Practical effect: Jobab answers customers while they are in an active
  conversation. Re-engaging a cold customer later is a separate, paid path
  (marketing messages/templates) that this assistant does not do on its own.

### WhatsApp: the customer-service window and the October 2026 pricing change

- A **24-hour customer service window** opens when the customer messages the
  business and resets on each new customer message. Jobab replies with plain
  text inside this window.
- **Pricing change you must budget for:** from **1 October 2026**, WhatsApp
  **service messages** (free-form replies inside the 24-hour window) become
  **billable per message**. Each business phone number gets **1,000 free service
  messages per month**, then pays per delivered message. In-window utility
  templates also lose their free status. (Before this date, in-window service
  replies were free.) This is Meta's billing, paid to your WhatsApp provider,
  separate from Jobab's own credits.
- **Outside the window** you can only reach a customer with an **approved
  template**, which is a paid, separate path. Jobab does not auto-send templates.

### What this means for cost

You have two cost meters when WhatsApp is on:

1. **Jobab credits** — one per AI reply, for the AI provider's compute.
2. **Meta/WhatsApp message fees** — per the rules above, paid to Meta via your
   BSP or Cloud API billing.

Keep both in mind when you price the service to a shop.

---

## 4. Production checklist

Before pointing real customers at it:

- [ ] `ENVIRONMENT=production`, `DEBUG=false`.
- [ ] `SECRET_KEY` set to a fresh random 32+ character string. **(The app
      refuses to start otherwise.)**
- [ ] `PUBLIC_BASE_URL` is your real `https://` address.
- [ ] A strong `DB_PASSWORD` (not a default).
- [ ] `ALLOW_PRIVATE_URLS=false`.
- [ ] `ALLOW_SIGNUP=false` once your shops are onboarded, or keep it on if you
      run open self-serve.
- [ ] `ADMIN_EMAIL` set to your operator account.
- [ ] AI provider keys set and tested (send a website-chat message and watch a
      reply).
- [ ] Run behind a TLS-terminating reverse proxy; the app is started with
      `--proxy-headers` so client IPs are correct.
- [ ] Back up Postgres. Everything important is there.

---

## 5. Scaling past one process

The app ships to run as **one process** (API + dashboard + webhook worker),
which is right for a shop or a small operator. When you outgrow it:

- Move the rate limiter and the reply debounce to a shared store (Redis), since
  both are in-process today.
- Run the webhook worker as its own process/replica (it already claims events
  from a durable queue table, so a crash loses nothing).
- Add read replicas for the dashboard's heavier reports if needed.
