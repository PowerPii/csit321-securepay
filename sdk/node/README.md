# securepaysg

Server-side TypeScript client for the [SecurePay](https://www.securepaysg.me) merchant API: create payments, capture, cancel, refund, check status, and verify signed webhooks.

- No dependencies. Node.js 20.19 or later (uses the built-in `fetch` and `node:crypto`).
- Types generated from the API's OpenAPI 3.1 file, so field names and statuses match the API.
- Sends an `Idempotency-Key` on every `POST` (the one you saved with your order, see below) and retries timeouts, `429` and `5xx` replies with that same key, so a retry never charges twice. When a call ends without a usable reply, the error keeps that key and says the outcome is unknown.
- Checks the `SecurePay-Signature` on every reply and every webhook when you give it your signing secret.

> SecurePay is a university final-year project (CSIT321, group FYP-26-S4-36). It is not a licensed payment service. The live gateway at `gateway.securepaysg.me` is a demo: every payment is simulated and no real money moves. Never send real card data.

## Install

```bash
npm install securepaysg
```

## Get your keys

Register a merchant account on the [dashboard](https://gateway.securepaysg.me/dashboard/register). Once an administrator approves it, open **API keys** to get:

- a secret API key, `sp_test_...` for test mode or `sp_live_...` for live mode
- a webhook signing secret, `whsec_...`

Both are shown once. Keep them in your server's environment, never in browser code.

## Take a payment

Save an idempotency key with the order **before** you call the gateway, and send that saved key every time. If the reply is lost or your server restarts mid-call, the same key gets you the original payment back instead of a second one.

```ts
import { randomUUID } from "node:crypto";
import { SecurePay } from "securepaysg";

const securepay = new SecurePay({
  apiKey: process.env.SECUREPAY_API_KEY!,
  signingSecret: process.env.SECUREPAY_SIGNING_SECRET, // checks every reply's signature
});

// 1. Create the order and its key in your database first (one row, one write).
const order = await db.orders.insert({
  id: "ORD-10042",
  amount: 4590,
  payment_key: randomUUID(), // reused for every retry of THIS order's payment
});

// 2. Create the payment with the saved key.
const payment = await securepay.payments.create(
  {
    amount: order.amount, // cents, SGD (50 to 1,000,000)
    currency: "SGD",
    order_reference: order.id,
    return_url: `https://shop.example.com/orders/${order.id}/return`,
    customer_email: "alex@example.com", // optional
    line_items: [                       // optional; must add up to amount
      { name: "Wireless mouse", quantity: 1, unit_amount: 2990 },
      { name: "Cable tie (pack)", quantity: 2, unit_amount: 800 },
    ],
  },
  { idempotencyKey: order.payment_key },
);

// 3. Remember the payment, then send the customer to the hosted checkout (valid for 1 hour).
await db.orders.update(order.id, { payment_id: payment.id });
res.redirect(payment.checkout_url!);
```

The customer's return to `return_url` is navigation only. Fulfil the order when a verified `payment.authorised` webhook arrives, or after checking the status yourself:

```ts
const current = await securepay.payments.retrieve(order.payment_id);
if (current.status === "authorised") {
  await securepay.payments.capture(current.id, {}, { idempotencyKey: order.capture_key }); // all of it
  // or: capture(current.id, { amount: 4000 }, { idempotencyKey: order.capture_key }) to capture less
}
```

## Refund

Each refund is its own operation, so it gets its own key, saved with your refund record before the call. Two partial refunds are two records and two keys; a retry of one of them reuses only that one's key.

```ts
const refund = await db.refunds.insert({ order_id: order.id, amount: 1500, key: randomUUID() });
const result = await securepay.refunds.create(
  order.payment_id,
  { amount: refund.amount, reason: "One item returned" },
  { idempotencyKey: refund.key },
);
await db.refunds.update(refund.id, { gateway_refund_id: result.id, status: result.status });
```

## When the reply is lost

The SDK retries a timeout, network failure, `429` or `5xx` by itself, always with the same key. If it still has no usable answer, it throws an error with `outcomeUnknown: true`: the gateway may or may not have done the work. Do not create a new key. Send the same request again with the saved key (also on `err.idempotencyKey`), now or after a restart:

```ts
import { SecurePayError } from "securepaysg";

try {
  await securepay.payments.create(params, { idempotencyKey: order.payment_key });
} catch (err) {
  if (err instanceof SecurePayError && err.outcomeUnknown) {
    // Leave the order as "payment pending". Later (a job, or the next request), run the
    // same call with order.payment_key: you get the original payment, with
    // lastResponse.idempotentReplayed === true if it had been created.
  } else throw err;
}
```

`outcomeUnknown` is `true` for `SecurePayConnectionError` (no answer), for `SecurePayAPIError` with a `5xx` or `409 idempotency_request_in_progress` after the retries, and for a reply that failed the signature check. Any other `4xx` means nothing happened.

If you call without `idempotencyKey`, the SDK makes a random key for that call only. Its retries are still safe, but the key is lost with the process, and a later call is a new payment.

### Keys last 24 hours: reconcile after that

The gateway keeps a key for **24 hours from its first use** and replays the first result for that whole time. After that, the same key starts a new operation. So for an operation that is still unclear after 24 hours, look it up before sending anything:

- **A refund:** `refunds.list(paymentId)` returns every refund of the payment and the amount still refundable. If your refund is there, record it; if not, create it again with a new key.
- **A payment whose ID you never got:** search the order reference on the dashboard's **Payments** page and check your webhook log for its events. An unpaid payment expires when its checkout link does (1 hour), so an old one can never be charged; create a new payment only if the lookup shows none was paid.
- **A capture or cancel:** `payments.retrieve(paymentId)` shows the current status and any `pending_operation`.

`scripts/recovery-demo.mjs` in the repository runs this end to end against a gateway: lost create reply, merchant restart, same payment recovered; verification-required checkout; lost partial-refund reply, restart, same refund recovered; one signed webhook received twice.

Test card `4242 4242 4242 4242` with any future expiry and any 3-digit code is approved. Other test cards are listed in the [guides](https://www.securepaysg.me/developers#test-cards).

## All methods

| Method | Endpoint |
| --- | --- |
| `payments.create(params, options?)` | `POST /v1/payments` |
| `payments.retrieve(paymentId)` | `GET /v1/payments/{id}` |
| `payments.capture(paymentId, { amount? }?, options?)` | `POST /v1/payments/{id}/capture` |
| `payments.cancel(paymentId, options?)` | `POST /v1/payments/{id}/cancel` |
| `refunds.create(paymentId, { amount, reason? }, options?)` | `POST /v1/payments/{id}/refunds` |
| `refunds.list(paymentId)` | `GET /v1/payments/{id}/refunds` |
| `refunds.retrieve(refundId)` | `GET /v1/refunds/{id}` |
| `balance.retrieve()` | `GET /v1/balance` |
| `payouts.create({ amount }, options?)` | `POST /v1/payouts` |
| `payouts.list({ limit? }?)` | `GET /v1/payouts` |
| `payouts.retrieve(payoutId)` | `GET /v1/payouts/{id}` |
| `issuing.cards.create({ spending_limit, single_use?, description? }, options?)` | `POST /v1/issuing/cards` (v2): the only reply with `number` and `cvc` |
| `issuing.cards.list({ limit? }?)` | `GET /v1/issuing/cards` |
| `issuing.cards.retrieve(cardId)` | `GET /v1/issuing/cards/{id}`, with the bank's `spent` and `authorisations` |
| `issuing.cards.cancel(cardId)` | `POST /v1/issuing/cards/{id}/cancel` |
| `webhooks.constructEvent(rawBody, signatureHeader)` | verifies a webhook |

Every method returns the object from the API, plus a non-enumerable `lastResponse` with `status`, `requestId`, `idempotentReplayed`, `signatureVerified` and the `idempotencyKey` sent. Every error has `idempotencyKey` (for a `POST`) and `outcomeUnknown`.

A capture or cancel can answer `202` when the simulated bank does not reply in time: `lastResponse.status` is `202`, `pending_operation` is set, and the final `payment.captured` or `payment.cancelled` webhook follows. Do not retry it with a new idempotency key.

## Webhooks

The signature covers the exact bytes sent, so give `constructEvent` the raw body.

```ts
import express from "express";
import { SecurePay, SecurePaySignatureError } from "securepaysg";

const securepay = new SecurePay({ apiKey: process.env.SECUREPAY_API_KEY!, signingSecret: process.env.SECUREPAY_SIGNING_SECRET });
const app = express();

app.post("/webhooks/securepay", express.raw({ type: "application/json" }), (req, res) => {
  let event;
  try {
    event = securepay.webhooks.constructEvent(req.body, req.get("SecurePay-Signature"));
  } catch (err) {
    if (err instanceof SecurePaySignatureError) return res.sendStatus(400);
    throw err;
  }
  res.sendStatus(200); // reply within 10 seconds, then do the work

  switch (event.type) {
    case "payment.authorised":
      // event.data.object is a Payment
      break;
    case "refund.completed":
      // event.data.object is a Refund
      break;
  }
});
```

Then, as the [webhook guide](https://www.securepaysg.me/developers#webhooks) says: skip event IDs you have already handled, and ignore an event whose `sequence` is not higher than the one you stored for that payment, because events can arrive out of order.

Without a client, use the standalone functions:

```ts
import { constructEvent, verifySignature, signPayload } from "securepaysg";

constructEvent(rawBody, header, process.env.SECUREPAY_SIGNING_SECRET!); // throws on a bad signature
verifySignature(rawBody, header, secret);                               // true or false
signPayload(body, secret);                                              // a valid header, for your own tests
```

## Errors

```ts
import { SecurePayAPIError, SecurePayConnectionError, SecurePaySignatureError } from "securepaysg";

try {
  await securepay.refunds.create(paymentId, { amount: 1500 });
} catch (err) {
  if (err instanceof SecurePayAPIError) {
    err.status;    // 422
    err.code;      // "refund_exceeds_refundable": branch on this, never on message
    err.type;      // "business_rule"
    err.param;     // the field at fault, or null
    err.requestId; // quote it when asking for help
  } else if (err instanceof SecurePayConnectionError) {
    // no answer after the retries (network or timeout): err.outcomeUnknown is true,
    // retry later with err.idempotencyKey (see "When the reply is lost")
  } else if (err instanceof SecurePaySignatureError) {
    // a reply failed the signature check: do not trust it (outcomeUnknown is true)
  }
}
```

All error codes are in the [API reference](https://www.securepaysg.me/developers/api#error-codes).

## Options

```ts
new SecurePay({
  apiKey: "sp_test_...",             // required
  signingSecret: "whsec_...",        // recommended: verifies replies, default for webhooks
  baseUrl: "https://gateway.securepaysg.me", // default; must be https://
  allowInsecureHttp: false,          // true allows http:// to localhost / 127.0.0.1 only, for development
  timeoutMs: 30_000,                 // per attempt, 1 to 600,000
  maxRetries: 2,                     // extra attempts after a timeout, 429 or 5xx, 0 to 10
  fetch: customFetch,                // default: global fetch
});
```

Per call: `{ idempotencyKey, timeoutMs, signal }`. `idempotencyKey` is 8 to 255 printable ASCII characters without spaces (a UUID works); the SDK refuses anything else before sending. Webhook checks take `toleranceSeconds` from 1 to 3,600 (default 300).

## Types

```ts
import type { Payment, PaymentStatus, Refund, SecurePayEvent, CreatePaymentParams } from "securepaysg";
```

## Links

- Guides: https://www.securepaysg.me/developers
- API reference: https://www.securepaysg.me/developers/api
- OpenAPI file: https://www.securepaysg.me/openapi/merchant-api.v1.yaml

## License

MIT
