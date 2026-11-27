/**
 * The merchant API reference shown on /developers/api.
 *
 * Source of truth: contracts/API-CONTRACT.md, contracts/openapi/merchant-api.v1.yaml,
 * contracts/schemas/*.schema.json and contracts/examples/. Change those first, then this file.
 * Text in backticks is shown as inline code.
 */
import type { Snippet } from "../components/CodeBlock";
import { API_BASE, GATEWAY_URL } from "./site";

export type Field = { name: string; type: string; required?: boolean; text: string };

export type Endpoint = {
  id: string;
  method: "GET" | "POST";
  path: string;
  title: string;
  summary: string[];
  pathParams?: Field[];
  body?: Field[];
  request: Snippet[];
  response: Snippet[];
  replies: [string, string][];
  errors: string[];
};

const json = (v: unknown) => JSON.stringify(v, null, 2);

// ---------------------------------------------------------------- example objects

const PAY_ID = "pay_4fJ8kQ2mZx7RtL9vNc3bH1sD";
const RFD_ID = "rfd_8Wd3Hs6KqP1zLm4Tx9Bn2Vc7";
const PO_ID = "po_3Hn8KqT2vX7mR1cLz9WbY4pE";

const LINE_ITEMS = [
  { name: "Single-origin beans", quantity: 2, unit_amount: 1800 },
  { name: "Pour-over filter", quantity: 1, unit_amount: 690 },
];

const basePayment = {
  id: PAY_ID,
  object: "payment",
  reference: "SP-7KQ4-M2XD",
  order_reference: "order_1042",
  status: "created",
  amount: 4290,
  currency: "SGD",
  line_items: LINE_ITEMS,
  captured_amount: 0,
  refundable_amount: 0,
  refunded_amount: 0,
  refund_reserved_amount: 0,
  card: null as null | object,
  screening: null as null | object,
  outcome: null,
  pending_operation: null as null | object,
  sequence: 1,
  return_url: "https://shop.example/orders/1042",
  checkout_url: `${GATEWAY_URL}/pay/cs_...` as string | null,
  checkout_expires_at: "2026-11-13T05:30:00Z",
  authorisation_expires_at: null as string | null,
  created_at: "2026-11-13T04:30:00Z",
  updated_at: "2026-11-13T04:30:00Z",
  authorised_at: null as string | null,
  captured_at: null as string | null,
  cancelled_at: null as string | null,
};

const card = { token: "tok_...", brand: "visa", last4: "4242" };

const authorisedPayment = {
  ...basePayment,
  status: "authorised",
  card,
  screening: { decision: "allow" },
  sequence: 3,
  checkout_url: null,
  authorisation_expires_at: "2026-11-20T04:31:12Z",
  updated_at: "2026-11-13T04:31:12Z",
  authorised_at: "2026-11-13T04:31:12Z",
};

const capturedPayment = {
  ...authorisedPayment,
  status: "captured",
  captured_amount: 4290,
  refundable_amount: 4290,
  sequence: 4,
  authorisation_expires_at: null,
  updated_at: "2026-11-14T02:00:01Z",
  captured_at: "2026-11-14T02:00:01Z",
};

const refund = {
  id: RFD_ID,
  object: "refund",
  reference: "RF-9TQ2-K4WZ",
  payment_id: PAY_ID,
  payment_reference: "SP-7KQ4-M2XD",
  amount: 690,
  currency: "SGD",
  status: "completed",
  reason: "Filter returned",
  failure_reason: null,
  created_at: "2026-11-14T03:10:00Z",
  updated_at: "2026-11-14T03:10:01Z",
  completed_at: "2026-11-14T03:10:01Z",
};

const balance = {
  object: "balance",
  currency: "SGD",
  available: 10422,
  reserve: 1200,
  next_release: { amount: 1200, at: "2026-12-04T12:00:00Z", payment_id: "pay_7Qk2mVx9Lr4TzN8bW1cY6pHd" },
};

const payout = {
  id: PO_ID,
  object: "payout",
  amount: 5000,
  currency: "SGD",
  status: "paid",
  merchant_id: "mer_9Xc2Lm7Qp4Rt1Vb8Nz6Hk3Js",
  created_at: "2026-11-27T13:00:00Z",
};

const auth = `-H "Authorization: Bearer $SECUREPAY_API_KEY"`;
const idem = `-H "Idempotency-Key: $(uuidgen)"`;

// ---------------------------------------------------------------- endpoints

const PAYMENT_ID_PARAM: Field = { name: "payment_id", type: "string", required: true, text: "The payment's `id`, starting `pay_`." };

export const ENDPOINTS: Endpoint[] = [
  {
    id: "create-payment",
    method: "POST",
    path: "/v1/payments",
    title: "Create a payment",
    summary: [
      "Creates a payment in status `created` and a hosted checkout session that lasts 1 hour. Send the customer's browser to the `checkout_url` in the reply.",
      "Merchant, amount and currency are fixed here and the checkout page can never change them. When the customer is done, the browser goes to `return_url?payment_id=pay_...`. That is navigation only: fulfil the order only after a verified `payment.authorised` webhook or a signed `GET`.",
    ],
    body: [
      { name: "amount", type: "integer", required: true, text: "SGD cents, 50 to 1,000,000 (S$0.50 to S$10,000). An administrator can set a lower limit for your account." },
      { name: "currency", type: "string", required: true, text: "Always `SGD`." },
      { name: "order_reference", type: "string", required: true, text: "Your own order reference, 1 to 64 printable ASCII characters. Comes back on the payment and in every event." },
      { name: "return_url", type: "string", required: true, text: "HTTPS address the customer's browser returns to, up to 2,048 characters." },
      { name: "customer_email", type: "string", text: "Prefills the checkout email. The customer can change it; receipts go to the address confirmed at checkout." },
      { name: "description", type: "string", text: "Up to 200 characters, shown on the checkout page and the receipt." },
      { name: "line_items", type: "array", text: "1 to 50 items of `{ name, quantity, unit_amount }` shown as the order summary on the checkout page. `name` is up to 100 characters, `quantity` 1 to 999, `unit_amount` in cents. The items must add up to `amount` exactly." },
    ],
    request: [
      {
        label: "curl",
        code: `curl ${API_BASE}/payments \\
  ${auth} \\
  ${idem} \\
  -H "Content-Type: application/json" \\
  -d '${json({
    amount: 4290,
    currency: "SGD",
    order_reference: "order_1042",
    return_url: "https://shop.example/orders/1042",
    customer_email: "alex@example.com",
    line_items: LINE_ITEMS,
  }).replace(/\n/g, "\n  ")}'`,
      },
    ],
    response: [{ label: "201 Created", code: json(basePayment) }],
    replies: [["201", "The new Payment, with `reference`, `checkout_url` and `checkout_expires_at`."]],
    errors: [
      "invalid_json",
      "missing_field",
      "invalid_field",
      "amount_too_small",
      "amount_too_large",
      "currency_not_supported",
      "line_items_total_mismatch",
      "return_url_not_https",
      "card_data_not_allowed",
      "idempotency_key_missing",
      "amount_above_merchant_limit",
    ],
  },
  {
    id: "get-payment",
    method: "GET",
    path: "/v1/payments/{payment_id}",
    title: "Retrieve a payment",
    summary: [
      "Returns the current state of a payment. The reply is signed, so it is proof of status you can act on: check it before you ship, and whenever you notice a gap in event `sequence` numbers.",
      "A payment that belongs to another merchant answers `404`, exactly like one that does not exist.",
    ],
    pathParams: [PAYMENT_ID_PARAM],
    request: [{ label: "curl", code: `curl ${API_BASE}/payments/${PAY_ID} \\\n  ${auth}` }],
    response: [{ label: "200 OK", code: json(authorisedPayment) }],
    replies: [["200", "The Payment."]],
    errors: ["resource_not_found"],
  },
  {
    id: "capture-payment",
    method: "POST",
    path: "/v1/payments/{payment_id}/capture",
    title: "Capture a payment",
    summary: [
      "Charges an `authorised` payment. Send `{}` to capture the full amount, or `{\"amount\": n}` to capture less. A payment is captured once: the rest of the authorisation is released and `captured_amount` records what was charged.",
      "Uncaptured authorisations are cancelled automatically 7 days after `authorised_at` (see `authorisation_expires_at`).",
      "If the bank does not answer, the reply is `202` with the payment still `authorised` and a `pending_operation`. Don't retry with a new key: a worker settles it and sends `payment.captured`, or clears `pending_operation` if the bank never got the request.",
    ],
    pathParams: [PAYMENT_ID_PARAM],
    body: [{ name: "amount", type: "integer", text: "Cents to capture, at most the payment's `amount`. Leave it out to capture everything." }],
    request: [
      {
        label: "Full",
        code: `curl -X POST ${API_BASE}/payments/${PAY_ID}/capture \\\n  ${auth} \\\n  ${idem} \\\n  -H "Content-Type: application/json" \\\n  -d '{}'`,
      },
      {
        label: "Partial",
        code: `curl -X POST ${API_BASE}/payments/${PAY_ID}/capture \\\n  ${auth} \\\n  ${idem} \\\n  -H "Content-Type: application/json" \\\n  -d '{ "amount": 3600 }'`,
      },
    ],
    response: [
      { label: "200 OK", code: json(capturedPayment) },
      {
        label: "202 No answer yet",
        code: json({
          ...authorisedPayment,
          pending_operation: { type: "capture", status: "unresolved", requested_at: "2026-11-14T02:00:00Z" },
          updated_at: "2026-11-14T02:00:05Z",
        }),
      },
    ],
    replies: [
      ["200", "The Payment, now `captured`."],
      ["202", "Sent, but the bank has not answered. The Payment is still `authorised`, with `pending_operation`."],
    ],
    errors: [
      "payment_not_authorised",
      "payment_already_captured",
      "payment_already_cancelled",
      "authorisation_expired",
      "operation_in_progress",
      "processor_rejected",
      "operation_not_processed",
      "capture_exceeds_authorised",
    ],
  },
  {
    id: "cancel-payment",
    method: "POST",
    path: "/v1/payments/{payment_id}/cancel",
    title: "Cancel a payment",
    summary: [
      "Releases an `authorised` payment without charging the customer. A captured payment cannot be cancelled; refund it instead.",
      "No answer from the bank works exactly as for capture: `202` with a `pending_operation`, settled later.",
    ],
    pathParams: [PAYMENT_ID_PARAM],
    request: [{ label: "curl", code: `curl -X POST ${API_BASE}/payments/${PAY_ID}/cancel \\\n  ${auth} \\\n  ${idem}` }],
    response: [
      {
        label: "200 OK",
        code: json({
          ...authorisedPayment,
          status: "cancelled",
          sequence: 4,
          authorisation_expires_at: null,
          updated_at: "2026-11-13T06:02:40Z",
          cancelled_at: "2026-11-13T06:02:40Z",
          outcome: { reason: "merchant_cancelled", message: "You cancelled this authorisation. Nothing was charged." },
        }),
      },
    ],
    replies: [
      ["200", "The Payment, now `cancelled`."],
      ["202", "Sent, but the bank has not answered. The Payment is still `authorised`, with `pending_operation`."],
    ],
    errors: [
      "payment_not_authorised",
      "payment_already_captured",
      "payment_already_cancelled",
      "authorisation_expired",
      "operation_in_progress",
      "processor_rejected",
      "operation_not_processed",
    ],
  },
  {
    id: "create-refund",
    method: "POST",
    path: "/v1/payments/{payment_id}/refunds",
    title: "Refund a payment",
    summary: [
      "Refunds all or part of a `captured` payment. You can refund several times, as long as the total stays within `refundable_amount`. The gateway reserves the amount in the same database transaction that checks it, so two refunds sent at once can never go over.",
      "The reply is the Refund with status `completed`, `failed` or `unresolved`. An `unresolved` refund keeps its reservation until the reconciliation worker settles it, and blocks new refunds of that payment until then.",
    ],
    pathParams: [PAYMENT_ID_PARAM],
    body: [
      { name: "amount", type: "integer", required: true, text: "Cents to refund, at most the payment's `refundable_amount`." },
      { name: "reason", type: "string", text: "Up to 200 characters for your own records. Must not contain card data." },
    ],
    request: [
      {
        label: "curl",
        code: `curl -X POST ${API_BASE}/payments/${PAY_ID}/refunds \\\n  ${auth} \\\n  ${idem} \\\n  -H "Content-Type: application/json" \\\n  -d '{ "amount": 690, "reason": "Filter returned" }'`,
      },
    ],
    response: [{ label: "201 Created", code: json(refund) }],
    replies: [["201", "The Refund."]],
    errors: ["payment_not_captured", "operation_in_progress", "refund_exceeds_refundable", "card_data_not_allowed"],
  },
  {
    id: "list-refunds",
    method: "GET",
    path: "/v1/payments/{payment_id}/refunds",
    title: "List a payment's refunds",
    summary: ["Lists the refunds of one payment, oldest first, with the amount that can still be refunded."],
    pathParams: [PAYMENT_ID_PARAM],
    request: [{ label: "curl", code: `curl ${API_BASE}/payments/${PAY_ID}/refunds \\\n  ${auth}` }],
    response: [{ label: "200 OK", code: json({ object: "list", payment_id: PAY_ID, refundable_amount: 3600, data: [refund] }) }],
    replies: [["200", "A list object with `payment_id`, `refundable_amount` and `data`."]],
    errors: ["resource_not_found"],
  },
  {
    id: "get-refund",
    method: "GET",
    path: "/v1/refunds/{refund_id}",
    title: "Retrieve a refund",
    summary: ["Returns one refund, with a signed reply."],
    pathParams: [{ name: "refund_id", type: "string", required: true, text: "The refund's `id`, starting `rfd_`." }],
    request: [{ label: "curl", code: `curl ${API_BASE}/refunds/${RFD_ID} \\\n  ${auth}` }],
    response: [{ label: "200 OK", code: json(refund) }],
    replies: [["200", "The Refund."]],
    errors: ["resource_not_found"],
  },
  {
    id: "get-balance",
    method: "GET",
    path: "/v1/balance",
    title: "Retrieve your balance",
    summary: [
      "Returns what the ledger holds for you, in SGD cents. `available` is what you can pay out now. `reserve` is the part of each capture held back for 7 days, and `next_release` says when the next part of it moves to `available`.",
      "Every captured payment is posted to the ledger as the amount less the fee (2.9% + S$0.30), with 10% of the rest kept in reserve. Refunds come out of `available`.",
    ],
    request: [{ label: "curl", code: `curl ${API_BASE}/balance \\\n  ${auth}` }],
    response: [{ label: "200 OK", code: json(balance) }],
    replies: [["200", "The Balance. `next_release` is null when nothing is held."]],
    errors: [],
  },
  {
    id: "create-payout",
    method: "POST",
    path: "/v1/payouts",
    title: "Create a payout",
    summary: [
      "Pays out part or all of your `available` balance. The amount is taken from the balance in the same database transaction that checks it, so two payouts sent at once can never overdraw it.",
    ],
    body: [{ name: "amount", type: "integer", required: true, text: "SGD cents, at least 100 (S$1.00) and no more than `available`." }],
    request: [
      {
        label: "curl",
        code: `curl -X POST ${API_BASE}/payouts \\\n  ${auth} \\\n  ${idem} \\\n  -H "Content-Type: application/json" \\\n  -d '{ "amount": 5000 }'`,
      },
    ],
    response: [{ label: "201 Created", code: json(payout) }],
    replies: [["201", "The Payout."]],
    errors: ["amount_too_small", "insufficient_balance"],
  },
  {
    id: "list-payouts",
    method: "GET",
    path: "/v1/payouts",
    title: "List payouts",
    summary: ["Lists your payouts, newest first. `limit` is 1 to 100, default 10."],
    request: [{ label: "curl", code: `curl "${API_BASE}/payouts?limit=10" \\\n  ${auth}` }],
    response: [{ label: "200 OK", code: json({ object: "list", has_more: false, data: [payout] }) }],
    replies: [["200", "A list object with `has_more` and `data`."]],
    errors: ["invalid_field"],
  },
  {
    id: "get-payout",
    method: "GET",
    path: "/v1/payouts/{payout_id}",
    title: "Retrieve a payout",
    summary: ["Returns one payout, with a signed reply."],
    pathParams: [{ name: "payout_id", type: "string", required: true, text: "The payout's `id`, starting `po_`." }],
    request: [{ label: "curl", code: `curl ${API_BASE}/payouts/${PO_ID} \\\n  ${auth}` }],
    response: [{ label: "200 OK", code: json(payout) }],
    replies: [["200", "The Payout."]],
    errors: ["resource_not_found"],
  },
];

// ---------------------------------------------------------------- objects

export const PAYMENT_FIELDS: Field[] = [
  { name: "id", type: "string", text: "Identifier used in API paths, `pay_` and 24 characters." },
  { name: "object", type: "string", text: "Always `payment`." },
  { name: "reference", type: "string", text: "Customer-facing reference `SP-XXXX-XXXX`, printed on receipts and used for the guest status check." },
  { name: "order_reference", type: "string", text: "Your order reference, as sent." },
  { name: "status", type: "enum", text: "`created`, `screening`, `verification_required`, `processing`, `authorised`, `captured`, `declined`, `blocked`, `unresolved`, `cancelled` or `expired`." },
  { name: "amount", type: "integer", text: "SGD cents, fixed at creation." },
  { name: "currency", type: "string", text: "`SGD`." },
  { name: "line_items", type: "array | null", text: "The items you sent, if any." },
  { name: "captured_amount", type: "integer", text: "What was actually charged. `0` until capture; less than `amount` after a partial capture." },
  { name: "refundable_amount", type: "integer", text: "`captured_amount` less completed and reserved refunds." },
  { name: "refunded_amount", type: "integer", text: "Sum of completed refunds." },
  { name: "refund_reserved_amount", type: "integer", text: "Sum of refunds still in flight or unresolved." },
  { name: "card", type: "object | null", text: "`{ token, brand, last4 }` once the customer has entered a card. The full number never leaves the card vault." },
  { name: "screening", type: "object | null", text: "`{ decision }`: `allow`, `verify`, `block`, or `not_screened` when screening was down and the attempt was blocked. Scores stay in the dashboard." },
  { name: "outcome", type: "object | null", text: "`{ reason, message }` when the payment did not go ahead. Reasons: `fraud_rule`, `fraud_model`, `verification_failed`, `verification_expired`, `screening_unavailable`, `amount_limit`, `card_declined`, `not_processed`, `checkout_expired`, `merchant_cancelled`, `authorisation_expired`." },
  { name: "pending_operation", type: "object | null", text: "`{ type, status: \"unresolved\", requested_at }` while a capture or cancel waits for the bank." },
  { name: "sequence", type: "integer", text: "Goes up by 1 on every change you are notified about, refunds included. Equals the `sequence` of the latest event." },
  { name: "return_url", type: "string", text: "As sent." },
  { name: "checkout_url", type: "string | null", text: "Hosted checkout address while the payment is `created` and the session is open; `null` after." },
  { name: "checkout_expires_at", type: "timestamp", text: "`created_at` plus 1 hour." },
  { name: "authorisation_expires_at", type: "timestamp | null", text: "When an uncaptured authorisation is cancelled automatically: `authorised_at` plus 7 days." },
  { name: "created_at, updated_at", type: "timestamp", text: "RFC 3339 in UTC." },
  { name: "authorised_at, captured_at, cancelled_at", type: "timestamp | null", text: "Set when the payment reaches that status." },
];

export const REFUND_FIELDS: Field[] = [
  { name: "id", type: "string", text: "`rfd_` and 24 characters." },
  { name: "object", type: "string", text: "Always `refund`." },
  { name: "reference", type: "string", text: "Customer-facing reference `RF-XXXX-XXXX`." },
  { name: "payment_id, payment_reference", type: "string", text: "The payment it refunds." },
  { name: "amount", type: "integer", text: "SGD cents." },
  { name: "currency", type: "string", text: "`SGD`." },
  { name: "status", type: "enum", text: "`reserved` (held, not sent yet), `submitted`, `completed`, `failed` or `unresolved` (no answer; the amount stays reserved)." },
  { name: "reason", type: "string | null", text: "Your note, as sent." },
  { name: "failure_reason", type: "enum | null", text: "`declined_by_processor` or `not_processed`, only when `failed`." },
  { name: "created_at, updated_at, completed_at", type: "timestamp", text: "`completed_at` is `null` until completed." },
];

export const EVENT_FIELDS: Field[] = [
  { name: "id", type: "string", text: "`evt_` and 24 characters. Also in the `SecurePay-Event-Id` header. Remember it to drop duplicates." },
  { name: "object", type: "string", text: "Always `event`." },
  { name: "type", type: "string", text: "One of the event types below." },
  { name: "created_at", type: "timestamp", text: "When the change was committed, not when this attempt was sent." },
  { name: "payment_id", type: "string", text: "The payment it is about, also for refund events." },
  { name: "sequence", type: "integer", text: "The payment's `sequence` at that moment. Ignore an event whose sequence is not higher than the one you stored." },
  { name: "data.object", type: "object", text: "A full Payment or Refund snapshot." },
];

export const EVENTS: { type: string; object: string; when: string }[] = [
  { type: "payment.verification_required", object: "Payment", when: "Screening asked for verification; the customer was emailed a code." },
  { type: "payment.processing", object: "Payment", when: "Sent to the bank after screening (and verification)." },
  { type: "payment.authorised", object: "Payment", when: "The bank authorised it, also after reconciliation. Safe to fulfil once verified." },
  { type: "payment.declined", object: "Payment", when: "The bank declined it, or reconciliation found the bank never got it (`not_processed`)." },
  { type: "payment.blocked", object: "Payment", when: "Stopped by a fraud rule or the model, failed or expired verification, screening down, or the amount limit." },
  { type: "payment.unresolved", object: "Payment", when: "The bank did not answer. Don't ship; a final event follows." },
  { type: "payment.captured", object: "Payment", when: "Capture confirmed." },
  { type: "payment.cancelled", object: "Payment", when: "Your cancel confirmed, or the 7-day auto-cancel." },
  { type: "payment.expired", object: "Payment", when: "No card was submitted within the 1-hour checkout session." },
  { type: "refund.completed", object: "Refund", when: "Refund confirmed." },
  { type: "refund.failed", object: "Refund", when: "Refund declined, or the bank never got it. The reservation is released." },
  { type: "refund.unresolved", object: "Refund", when: "The bank did not answer the refund. A final event follows." },
];

export const WEBHOOK_HEADERS: [string, string][] = [
  ["Content-Type", "`application/json`"],
  ["SecurePay-Signature", "`t=<unix seconds>,v1=<hex HMAC-SHA256>`, fresh for every attempt"],
  ["SecurePay-Event-Id", "Same as the body's `id`"],
  ["SecurePay-Event-Type", "Same as the body's `type`"],
  ["SecurePay-Delivery-Attempt", "1, 2, 3 …"],
  ["User-Agent", "`SecurePay-Webhooks/1.0`"],
];

export const RETRY_SCHEDULE: [string, string][] = [
  ["1", "1 min"],
  ["2", "5 min"],
  ["3", "15 min"],
  ["4", "30 min"],
  ["5", "1 h"],
  ["6", "2 h"],
  ["7", "4 h"],
  ["8", "8 h"],
  ["9 to 12", "12 h each"],
  ["Last", "at 72 h"],
];

// ---------------------------------------------------------------- errors

export type ApiError = { http: string; type: string; code: string; when: string };

export const ERRORS: ApiError[] = [
  { http: "400", type: "invalid_request", code: "invalid_json", when: "The body is not valid JSON." },
  { http: "400", type: "invalid_request", code: "missing_field", when: "A required field is missing; `param` names it." },
  { http: "400", type: "invalid_request", code: "invalid_field", when: "A field has the wrong type or format; `param` names it." },
  { http: "400", type: "invalid_request", code: "amount_too_small", when: "Below S$0.50 (50 cents) for a payment, or S$1.00 (100 cents) for a payout." },
  { http: "400", type: "invalid_request", code: "amount_too_large", when: "Above S$10,000 (1,000,000 cents)." },
  { http: "400", type: "invalid_request", code: "currency_not_supported", when: "Currency other than SGD." },
  { http: "400", type: "invalid_request", code: "line_items_total_mismatch", when: "`line_items` don't add up to `amount`." },
  { http: "400", type: "invalid_request", code: "return_url_not_https", when: "`return_url` is not HTTPS." },
  { http: "400", type: "invalid_request", code: "card_data_not_allowed", when: "The body contains what looks like a card number. The body is not logged." },
  { http: "400", type: "invalid_request", code: "idempotency_key_missing", when: "A POST without an `Idempotency-Key`." },
  { http: "400", type: "invalid_request", code: "idempotency_key_invalid", when: "The key is not 8 to 255 printable ASCII characters." },
  { http: "401", type: "authentication", code: "api_key_invalid", when: "Missing, unknown or revoked API key. Not signed." },
  { http: "403", type: "permission", code: "merchant_not_active", when: "Your merchant account is pending, rejected or suspended." },
  { http: "404", type: "not_found", code: "resource_not_found", when: "No such record for your account, including another merchant's." },
  { http: "409", type: "conflict", code: "idempotency_key_reused", when: "Same key, different request. Nothing ran." },
  { http: "409", type: "conflict", code: "idempotency_request_in_progress", when: "Same key while the first request is still running. Has `Retry-After: 1`." },
  { http: "409", type: "conflict", code: "payment_not_authorised", when: "Capture or cancel on a payment that is not `authorised`." },
  { http: "409", type: "conflict", code: "payment_already_captured", when: "Capture or cancel on a captured payment. Refund instead." },
  { http: "409", type: "conflict", code: "payment_already_cancelled", when: "Capture or cancel on a cancelled payment." },
  { http: "409", type: "conflict", code: "authorisation_expired", when: "The 7-day authorisation ran out." },
  { http: "409", type: "conflict", code: "operation_in_progress", when: "A capture or cancel is still unresolved, or an earlier refund of this payment is." },
  { http: "409", type: "conflict", code: "processor_rejected", when: "The bank refused a capture or cancel the gateway allowed. Payment unchanged; check it and contact support." },
  { http: "409", type: "conflict", code: "operation_not_processed", when: "Retry of a capture or cancel the bank never received. Try again with a new key." },
  { http: "409", type: "conflict", code: "payment_not_captured", when: "Refund on a payment that is not captured." },
  { http: "422", type: "business_rule", code: "amount_above_merchant_limit", when: "Above the limit an administrator set for your account." },
  { http: "422", type: "business_rule", code: "capture_exceeds_authorised", when: "Capture larger than the authorised amount." },
  { http: "422", type: "business_rule", code: "refund_exceeds_refundable", when: "Refund larger than what is left. No refund is created." },
  { http: "422", type: "business_rule", code: "insufficient_balance", when: "Payout larger than your `available` balance. No payout is created." },
  { http: "429", type: "rate_limit", code: "rate_limited", when: "Over 100 requests a minute for this key. Has `Retry-After`." },
  { http: "500", type: "server", code: "internal_error", when: "Unexpected failure. Retry with the same `Idempotency-Key`." },
  { http: "503", type: "server", code: "service_unavailable", when: "Gateway restarting or database down. Has `Retry-After`; retry with the same key." },
];

export const ERROR_EXAMPLE: Snippet[] = [
  {
    label: "409 Conflict",
    code: json({
      error: {
        type: "conflict",
        code: "idempotency_key_reused",
        message: "This Idempotency-Key was used with a different request.",
        param: null,
        request_id: "req_...",
      },
    }),
  },
];
