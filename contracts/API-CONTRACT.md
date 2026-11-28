# SecurePay merchant API contract (M2 draft)

Status: **v1 draft**, 13 November 2026. Proposals P1 to P3 (section 10) were confirmed by Fifa on 13 November 2026; everything else follows a decision already confirmed in the URS (Initial Version), the architecture recommendation or `docs/MVP-SCOPE.md`.

The URS lists this contract as an open item for Week 9 (M2), owned by Muhammad Adeeb: operation identifiers, error responses, the webhook retry window, the reset-link lifetime and the simulator's handling of an operation it reported as not received. This draft covers the merchant-facing part and the guest status flow. The simulator contract is in `SIMULATOR-CONTRACT.md`.

| File | What it holds |
| --- | --- |
| `contracts/API-CONTRACT.md` | This file: the rules that a schema cannot express |
| `contracts/openapi/merchant-api.v1.yaml` | OpenAPI 3.1 for `/v1/*` and the webhook |
| `contracts/openapi/guest-status.v1.yaml` | OpenAPI 3.1 for the guest status check (screen C9) |
| `contracts/schemas/*.schema.json` | Payment, Refund, Event envelope and Error objects (JSON Schema 2020-12) |
| `contracts/events/*.schema.json` | One schema per webhook event type |
| `contracts/examples/` | Example payloads, checked against the schemas by `contracts/check.py` in CI |

## 1. Conventions

- **Transport.** HTTPS only, TLS 1.2 or higher (NFR-ENC-01). Plain HTTP is refused.
- **Versioning.** The path carries the major version (`/v1`). Within v1 the gateway may add fields, endpoints, error codes and event types. Merchants must ignore fields and event types they do not know. Removing or renaming anything needs `/v2`.
- **Format.** Request and response bodies are UTF-8 JSON (`Content-Type: application/json`). Field names are `snake_case`.
- **Money.** `amount` fields are integers in cents. Currency is always `SGD`. A payment is S$0.50 to S$10,000 (50 to 1,000,000 cents), from `GATEWAY_RULES`.
- **Time.** Timestamps are RFC 3339 in UTC with a `Z` suffix, for example `2026-11-13T04:30:00Z`.
- **Identifiers.** Opaque, prefixed, 24 base62 characters after the prefix: `pay_` payment, `rfd_` refund, `evt_` event, `tok_` card token, `req_` request, `gsc_` guest status challenge. The customer-facing references are `SP-XXXX-XXXX` (payment, FR-CHK-08) and `RF-XXXX-XXXX` (refund), using digits and capital letters without `0/O/1/I`.
- **Card data.** No endpoint accepts or returns a card number or security code. A request body containing a string that looks like a full card number (13 to 19 digits passing the Luhn check, ignoring spaces and dashes) is rejected with `400 card_data_not_allowed` and the body is not logged (FR-TXN-09).

## 2. Authentication and signed replies

- **API key.** `Authorization: Bearer sp_test_<40 base62 chars>`. The gateway stores only a digest and shows the key once (FR-MER-03). The merchant is always taken from the key, never from the body. Missing, unknown or revoked key: `401 api_key_invalid`. Merchant pending or suspended: `403 merchant_not_active`.
- **Isolation.** Another merchant's payment or refund answers `404 resource_not_found`, exactly as a record that does not exist (FR-MER-08, NFR-CON-01).
- **Test and live mode (v2).** A key is either a test key (`sp_test_...`) or a live key (`sp_live_...`); a merchant has at most one active key of each. Everything a key creates belongs to its mode, and a key only sees its own mode: a payment, refund or payout of the other mode answers `404`, and `/v1/balance` is the balance of the key's mode. Each mode has its own ledger accounts, so test money never reaches a live balance. Idempotency keys are kept per mode. Every Payment, Refund, Payout, Balance and Event has `livemode` (true or false). "Live" is still simulated money in this project. Both modes share the merchant's signing secret and webhook address.
- **Signing secret.** Each merchant also gets a signing secret (`whsec_...`), shown once and stored encrypted (FR-MER-03). It signs both webhooks and API replies.
- **Signature header.** `SecurePay-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256>` where the HMAC is computed over the bytes `"<t>." + raw body`. The body carries the reference, amount, status and timestamp, so the signature covers everything FR-TXN-08 names. This is the scheme already in `gateway/apps/events/signing.py`.
- **Which replies are signed.** Every response to a request with a valid API key, including errors (FR-TXN-08, FR-TXN-10, MVP-SCOPE open point 3). `401` replies are not signed because the merchant is unknown. Merchants verify the signature and reject a timestamp more than 300 seconds from their clock (NFR-INT-01).
- **Rate limit.** 100 requests per minute per key (NFR-AVA-02) and 300 per client IP address, unless an administrator changed them (FR-ADM-12). Above that: `429 rate_limited` with `Retry-After` in seconds. Other keys are not affected by one key's limit.
- **Request ID.** Every response has `SecurePay-Request-Id: req_...`, also in error bodies. It is the correlation ID in the audit log and payment timeline (FR-TXN-16).

## 3. Payment lifecycle through the API

Statuses mirror `gateway/apps/payments/states.py` (URS Figure 2.2.2.1).

| Status | Meaning for the merchant | Allowed calls |
| --- | --- | --- |
| `created` | Waiting for the customer at the hosted checkout (1 hour) | GET |
| `screening`, `verification_required`, `processing` | Customer is paying; lasts seconds or, with verification, until the code is entered (each code lasts 10 minutes; the attempt is `blocked` if the 1-hour checkout session ends first) | GET |
| `authorised` | Bank said yes; not yet charged. Auto-cancelled 7 days after `authorised_at` | GET, capture, cancel |
| `captured` | Customer charged. Stays `captured` after refunds | GET, refunds |
| `unresolved` | Simulator did not answer; reconciliation decides. **Do not ship** | GET |
| `declined`, `blocked`, `cancelled`, `expired` | Final, no charge. `outcome.reason` says why | GET |

**Create** (`POST /v1/payments`, UC-53). Body: `amount`, `currency`, `order_reference`, `return_url` (HTTPS), optional `customer_email`, `description` and `line_items`. `line_items` is a list of `{name, quantity, unit_amount}` (1 to 50 items, amounts in cents) that the hosted checkout shows as the order summary (wireframe C3); the items must add up to `amount` exactly, otherwise `400 line_items_total_mismatch`. Added 13 November 2026 at Fifa's request; optional, so existing merchants are unaffected. The gateway fixes merchant, amount and currency here; checkout can never change them (FR-CHK-03). Reply `201` with the Payment, including `reference`, `checkout_url` and `checkout_expires_at`. The merchant sends the customer to `checkout_url`.

**Return to the shop.** After checkout the browser goes to `return_url?payment_id=pay_...`. This is navigation only. The shop fulfils only after a verified `payment.authorised` webhook or a signed `GET /v1/payments/{id}` (UC-27, C6 in the URS).

**Status query** (`GET /v1/payments/{id}`, UC-27). Signed reply with the current Payment.

**Capture** (`POST /v1/payments/{id}/capture`, UC-24). Send `{}` to capture the full amount, or `{"amount": n}` to capture less, as Stripe and Adyen allow. A payment is captured once: the uncaptured rest of the authorisation is released, and `captured_amount` records what was charged. More than the authorised amount: `422 capture_exceeds_authorised`. From any status other than `authorised` the reply is `409` with `payment_already_captured`, `payment_already_cancelled`, `authorisation_expired` or `payment_not_authorised` (FR-TXN-05).

**Cancel** (`POST /v1/payments/{id}/cancel`, UC-24). Same rules. On a captured payment the reply is `409 payment_already_captured`; refund instead.

**No answer from the simulator on capture or cancel.** The reply is `202` with the payment still `authorised` and `pending_operation: {type, status: "unresolved"}`. Reconciliation (UC-28) then settles it with the original operation ID: on success the payment becomes `captured` or `cancelled` and the event is sent; if the simulator has no record, the operation is retired and `pending_operation` is cleared, so the merchant may try again with a new Idempotency-Key. While an operation is pending, another capture or cancel gets `409 operation_in_progress`. The 7-day auto-cancel waits for a pending capture to settle.

**The processor refuses a capture or cancel the gateway allowed.** The gateway checks the rules first, so this means the gateway's and the simulator's records disagree (`SIMULATOR-CONTRACT.md` section 7). The reply is `409 processor_rejected`; the payment is unchanged and stays `authorised`, no event is sent, and the disagreement is logged and audited for staff. Do not retry with a new key; check the payment with `GET` and contact support. Added 13 November 2026, confirmed by Fifa (decision P4).

**A retried capture or cancel the processor never received.** If a request with the same Idempotency-Key is retried after reconciliation found that the simulator has no record of the original call, the reply is `409 operation_not_processed`: nothing changed, and the merchant may try again with a new Idempotency-Key.

**Refunds** (`POST /v1/payments/{id}/refunds`, UC-23). Body: `amount` (cents) and optional `reason`. Only on `captured` payments (`409 payment_not_captured` otherwise). The gateway locks the payment row, checks `completed + reserved + amount <= captured_amount` and reserves the amount in the same transaction (FR-TXN-02, FR-TXN-03). Too much: `422 refund_exceeds_refundable`, and no refund object is created (the attempt still appears as "rejected" in the dashboard history, FR-TXN-14). Reply `201` with the Refund, whose status is `completed`, `failed` or `unresolved`. An unresolved refund keeps its reservation until reconciliation; while it is unresolved, a new refund of the same payment gets `409 operation_in_progress` (`SIMULATOR-CONTRACT.md` section 3 rule 5; confirmed by Fifa 13 November 2026). Refunds that are merely in flight do not block each other: the reservation keeps them within the captured amount. `GET /v1/payments/{id}/refunds` lists them with the current `refundable_amount`; `GET /v1/refunds/{id}` reads one.

**Balance and payouts (v2 ledger, `docs/v2/LEDGER.md`).** `GET /v1/balance` returns the Balance: `available` (can be paid out), `reserve` (10% of each capture, held for 7 days, then released to `available`) and `next_release`. `POST /v1/payouts` with an `Idempotency-Key` and `{"amount": cents}` pays out from `available`: the gateway locks the merchant's available account, checks the amount (`400 amount_too_small` below S$1.00, `422 insufficient_balance` above the balance) and posts the ledger entry in the same transaction. Reply `201` with the Payout, `paid` at once (the bank transfer is simulated). `GET /v1/payouts?limit=` lists the newest payouts (`has_more` for older ones); `GET /v1/payouts/{id}` reads one. Another merchant's payout answers `404`.

**Issued cards (v2 issuing, `docs/v2/ISSUING.md`).** `POST /v1/issuing/cards` with an `Idempotency-Key` and `{"spending_limit": cents, "single_use": bool, "description": text}` makes a virtual Visa card at SecurePay Bank, the network's issuer (`NETWORK-CONTRACT.md` section 9). `spending_limit` is S$1 to S$10,000 over the card's life. Reply `201` with the IssuedCard (`schemas/issued-card.schema.json`), whose `number` and `cvc` appear in this reply only: neither the gateway nor the bank keeps them, so the reply is sent with `Cache-Control: no-store` and is not stored for idempotent replay. The same key again answers `200` with the same card and no number (`Idempotent-Replayed: true`); with a different body, `409 idempotency_key_reused`. If the bank does not answer, no card is made: `503 issuer_unavailable`, retry with the same key. The bank decides every payment on the card: over the limit, used (single use) or cancelled is a decline, which the merchant whose shop took the payment sees as `card_declined` like any other. `GET /v1/issuing/cards?limit=` lists them; `GET /v1/issuing/cards/{id}` reads one with the bank's `spent`, `remaining` and latest `authorisations`; `POST /v1/issuing/cards/{id}/cancel` cancels it. Another merchant's card, or one of the other mode, answers `404`.

## 4. Errors

Every error has the body in `schemas/error.schema.json`:

```json
{ "error": { "type": "conflict", "code": "idempotency_key_reused", "message": "This Idempotency-Key was used with a different request.", "param": null, "request_id": "req_..." } }
```

Merchants branch on `code`, never on `message`.

| HTTP | `type` | `code` | When |
| --- | --- | --- | --- |
| 400 | invalid_request | `invalid_json`, `missing_field`, `invalid_field` | Body cannot be parsed or a field is missing or wrong |
| 400 | invalid_request | `amount_too_small`, `amount_too_large` | Outside S$0.50 to S$10,000 |
| 400 | invalid_request | `currency_not_supported` | Currency other than SGD |
| 400 | invalid_request | `line_items_total_mismatch` | `line_items` do not add up to `amount` |
| 400 | invalid_request | `return_url_not_https` | `return_url` is not HTTPS |
| 400 | invalid_request | `card_data_not_allowed` | Body contains what looks like a card number (FR-TXN-09) |
| 400 | invalid_request | `idempotency_key_missing`, `idempotency_key_invalid` | POST without a usable key |
| 401 | authentication | `api_key_invalid` | Missing, unknown or revoked key |
| 403 | permission | `merchant_not_active` | Merchant pending, rejected or suspended |
| 404 | not_found | `resource_not_found` | No such record for this merchant (also another merchant's) |
| 409 | conflict | `idempotency_key_reused` | Same key, different request (section 5) |
| 409 | conflict | `idempotency_request_in_progress` | Same key while the first request is still running; has `Retry-After` (FR-CHK-10) |
| 409 | conflict | `payment_not_authorised`, `payment_already_captured`, `payment_already_cancelled`, `authorisation_expired` | Capture or cancel from the wrong status |
| 409 | conflict | `operation_in_progress` | A capture or cancel is still unresolved, or (on a refund) an earlier refund of the payment is still unresolved (`SIMULATOR-CONTRACT.md` section 3 rule 5, confirmed 13 Nov 2026) |
| 409 | conflict | `processor_rejected` | The processor refused a capture or cancel the gateway allowed; payment unchanged (P4) |
| 409 | conflict | `operation_not_processed` | Retry of a capture or cancel the processor never received; use a new Idempotency-Key |
| 409 | conflict | `payment_not_captured` | Refund on a payment that is not captured |
| 422 | business_rule | `amount_above_limit` | Above the gateway-wide limit per payment set by an administrator (FR-ADM-07); checked before screening |
| 422 | business_rule | `amount_above_merchant_limit` | Above the merchant's own limit set by an administrator (FR-ADM-07) |
| 422 | business_rule | `capture_exceeds_authorised` | Capture amount larger than the authorised amount |
| 422 | business_rule | `refund_exceeds_refundable` | Refund larger than the remaining refundable amount |
| 422 | business_rule | `insufficient_balance` | Payout larger than the available balance (v2) |
| 400 | invalid_request | `amount_too_small` | Payout below S$1.00 (`PAYOUT_MIN_CENTS`, v2) |
| 503 | server | `issuer_unavailable` | SecurePay Bank did not answer an issued-card call; retry with the same Idempotency-Key (v2) |
| 429 | rate_limit | `rate_limited` | Over the rate limit (100 a minute per key, 300 per IP, by default); has `Retry-After` |
| 500 | server | `internal_error` | Unexpected failure; retry with the same Idempotency-Key |
| 503 | server | `service_unavailable` | Gateway restarting or database unavailable; has `Retry-After` |

## 5. Idempotency

Rules from FR-CHK-09, FR-CHK-10 and NFR-INT-03; 24-hour retention confirmed by Fifa on 20 October 2026.

1. Every `POST` under `/v1` needs an `Idempotency-Key` header: 8 to 255 printable ASCII characters. Use a new UUID v4 per logical operation and reuse it only for retries of that operation.
2. Keys are scoped to the merchant and kept for **24 hours from first use**.
3. The gateway stores a fingerprint of the request: method, path and a SHA-256 of the canonical JSON body (keys sorted). Card data never reaches the fingerprint, because such bodies are rejected first.
4. Same key, same fingerprint, first request finished: the gateway returns the stored status code and body without repeating anything, with `Idempotent-Replayed: true`. The signature header is recomputed with a fresh timestamp.
5. Same key, same fingerprint, first request still running: `409 idempotency_request_in_progress` with `Retry-After: 1`.
6. Same key, different fingerprint: `409 idempotency_key_reused`. Nothing runs.
7. What is stored: the final `2xx`, `202` and `4xx` replies of authenticated requests, except `409 idempotency_request_in_progress` and `429`. `5xx` replies are not stored, so a retry with the same key runs again. That is safe because the gateway records the operation ID before it calls the simulator and reuses it on the retry; the simulator enforces one effect per operation ID.
8. `GET` requests ignore the header.

The 24-hour key window is separate from the webhook retry window in section 7. They protect different things: a merchant's retries of its own calls, and the gateway's retries of its notifications.

## 6. Webhook events

| Event | `data.object` | Sent when |
| --- | --- | --- |
| `payment.verification_required` | Payment | Screening asked for simulated verification; the customer was emailed a code |
| `payment.processing` | Payment | Sent to the simulator after screening (and verification) |
| `payment.authorised` | Payment | Simulator authorised it (also after reconciliation) |
| `payment.declined` | Payment | Simulator declined it, or reconciliation found no record (`outcome.reason = not_processed`) |
| `payment.blocked` | Payment | Fraud rule or model, failed or expired verification, screening unavailable, amount limit |
| `payment.unresolved` | Payment | Simulator did not answer the authorisation |
| `payment.captured` | Payment | Capture confirmed |
| `payment.cancelled` | Payment | Merchant cancel confirmed, or 7-day auto-cancel |
| `payment.expired` | Payment | Checkout session ended after 1 hour without a card submission |
| `refund.completed` | Refund | Refund confirmed |
| `refund.failed` | Refund | Refund declined, or reconciliation found no record; reservation released |
| `refund.unresolved` | Refund | Simulator did not answer the refund |

`created` and `screening` are not notified: the merchant already knows about `created`, and screening is internal and lasts milliseconds. The other in-progress statuses are notified, as Stripe does with `payment_intent.requires_action` and `payment_intent.processing`, but need no action from the merchant.

Envelope (`schemas/event.schema.json`): `id`, `object: "event"`, `type`, `created_at` (when the change was committed), `payment_id`, `sequence`, `data.object` (a full snapshot). `payment.verification_required`, `payment.processing`, `payment.expired` and `refund.unresolved` are new compared with the list in the architecture document.

**Ordering.** Events can arrive out of order. `sequence` counts every notified change of a payment and its refunds, starting at 1, and equals `Payment.sequence` at that moment. Ignore an event whose `sequence` is not higher than the one you stored; if you see a gap, call `GET /v1/payments/{id}` (UC-25).

**Outbox.** The event row is written in the same database transaction as the status change. Delivery results never change a payment or refund (FR-TXN-07).

## 7. Webhook delivery and retries

**Request.** `POST` to the merchant's administrator-approved HTTPS address (FR-MER-10, FR-ADM-13). The address is resolved and checked against private ranges before **every** attempt; redirects are not followed. Headers:

| Header | Value |
| --- | --- |
| `Content-Type` | `application/json` |
| `SecurePay-Signature` | `t=...,v1=...`, computed fresh for each attempt |
| `SecurePay-Event-Id` | Same as body `id` |
| `SecurePay-Event-Type` | Same as body `type` |
| `SecurePay-Delivery-Attempt` | 1, 2, 3, ... |
| `User-Agent` | `SecurePay-Webhooks/1.0` |

The signature timestamp is set when each attempt is sent, not when the event was created. Otherwise every retry after the first five minutes would fail the merchant's replay check.

**Success.** Any `2xx` within **10 seconds**. The response body does not affect the outcome.

**Failure.** Anything else: timeout, connection or TLS error, a redirect, or any non-2xx status. All failures are retried the same way.

**Retry window: 72 hours from the first failed attempt** (P1, confirmed by Fifa 13 November 2026). This matches the three-day horizon that Stripe and PayPal document (Codex research R3, 13 November 2026). It replaces the tentative 24-hour starting point from the review. FR-TXN-07 says the window is fixed in this contract, so the URS needs no change either way.

**Schedule.** Delays after each failed attempt, with ±10% random jitter:

| Retry | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 to 12 | last |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Delay | 1 min | 5 min | 15 min | 30 min | 1 h | 2 h | 4 h | 8 h | 12 h each | at 72 h |

That is 14 attempts in all: the first, 12 retries by about 63 h 51 min, and a final attempt at the 72-hour mark. In tests the worker reads the schedule from settings and runs on an accelerated clock, so nobody waits three days.

**After the window.** The delivery is marked `undelivered`, kept (never deleted) and shown on the dashboard (UC-25). The merchant can still query the status. It can be sent again by hand from the dashboard's webhook log (see Resend below); a resend keeps the original event `id`.

**Recording.** Each attempt stores its time, attempt number, HTTP status or error class, and duration (FR-TXN-07), plus the first 300 characters of the response body as one printable line, so the merchant can see why their endpoint refused it. The dashboard's webhook log shows the attempts to the merchant's operators and to administrators.

**Resend.** From the webhook log an operator or administrator can send a delivered or undelivered event again (at most 5 times per event). It keeps its event ID, sequence and payload, so an endpoint that already processed it drops the repeat (see Merchant side below); the retry schedule and the 72-hour window start again from the next attempt. An event still being retried cannot be resent. Each resend is written to the audit log.

**Merchant side (the demo store does this, FR-SIM-06).** Verify the signature over the raw bytes before parsing; reject a timestamp more than 300 seconds off; keep seen event IDs for **at least 7 days** (longer than the 72-hour window plus room for a manual replay); ignore lower sequences; reply 2xx quickly and do slow work afterwards; confirm with `GET /v1/payments/{id}` before shipping.

## 8. Guest status with an emailed one-time code

Closes open point 1 of `docs/MVP-SCOPE.md`. Follows FR-ACC-05, FR-ACC-06 and UC-07 in the final URS, and Fifa's decision of 5 November 2026 (reference + checkout email + emailed one-time code). OpenAPI: `openapi/guest-status.v1.yaml`.

1. **Receipt link.** The receipt for a guest payment (FR-CHK-15) links to `/guest/status?ref=SP-XXXX-XXXX`. The link only fills in the reference; it is not a credential. This replaces the 30-day status token in the architecture recommendation and the `checkout/status/<token>` route in the skeleton.
2. **Ask for a code.** The guest enters the reference and checkout email. `POST /guest/status/code` always answers `202` with a `challenge_id` and the same message, whether or not they match (FR-ACC-06). The code email goes through the outbox, so timing does not reveal a match. If they match, a **6-digit code valid for 10 minutes** is emailed to the checkout address; a new request replaces the old code.
3. **Enter the code.** `POST /guest/status/verify` with `challenge_id` and `code`. Wrong code: `400 code_incorrect` with `attempts_remaining`. Expired or replaced: `400 code_expired`. **After 3 wrong codes the check for that reference and email is locked for 15 minutes** (`429 status_check_locked`, `Retry-After`). A challenge that matched nothing behaves exactly the same.
4. **See the payment.** On success the gateway sets an `sp_guest_status` cookie (HttpOnly, Secure, SameSite=Strict, path `/guest/status`) bound to that one payment for **15 minutes**, and returns a read-only view: reference, merchant name, amount, a customer-wording status, masked card, refunded amount and a timeline. No order contents, no fraud reasons. `GET /guest/status/payment` refreshes it; `GET /guest/status/receipt.pdf` downloads the receipt as a PDF while the payment was charged (409 `no_receipt` otherwise; signed-in customers get the same from `GET /customer/payments/{reference}/receipt.pdf`); `POST /guest/status/sign-out` ends it.
5. **Limits against abuse.** At most 3 code requests per reference and email per 15 minutes, and 10 per IP address per 15 minutes; above that `429 rate_limited`. Codes are stored hashed and are single use.

Values marked confirmed come from the URS: 6 digits, 10 minutes, 3 wrong codes, 15-minute lock. The 15-minute view session and the request limits in step 5 were added by this contract (P3, confirmed by Fifa 13 November 2026).

`GATEWAY_RULES` holds these as `GUEST_STATUS_*`; `GUEST_STATUS_LINK_TTL_DAYS` is removed.

## 9. Password reset link (accounts)

Not part of the merchant API, but the URS sends its lifetime to this contract (open items table). FR-ACC-07 requires time-limited, single-use links and the same message whether or not the email is registered.

**The reset link is valid for 30 minutes from issue and works once** (P2, confirmed by Fifa 13 November 2026; Codex research R3). Neither OWASP nor NIST fixes 30 minutes: OWASP asks for single use and "an appropriate period", and NIST SP 800-63B-4 allows up to 24 hours for an emailed recovery code. 30 minutes is a design judgement that sits well inside that limit.

Rules: the token is 32 random bytes, sent only in the link and stored as a SHA-256 hash; it is consumed atomically on a successful reset; a new request invalidates earlier links; expiry is checked on the server. A reset ends all sessions (FR-ACC-08). A merchant operator also confirms with their TOTP code (FR-ACC-09); a reset never removes staff MFA. The reset page sets `Referrer-Policy: no-referrer` so the token does not leak to other sites. This is separate from the 10-minute checkout verification code, which does not change.

`GATEWAY_RULES`: `PASSWORD_RESET_LINK_TTL_MINUTES = 30`.

Built in sprint 10 for staff accounts (`gateway/apps/accounts/reset.py`, staff API `/internal/auth/password-reset`, `/check`, `/confirm`; dashboard pages `/dashboard/forgot-password` and `/dashboard/reset-password`). The link carries the token in the URL fragment (`#token=...`), so it never reaches a server log either. Two details this contract left open: at most 3 links per account per 15 minutes are sent (more requests get the same reply and send nothing; `PASSWORD_RESET_REQUESTS_PER_ACCOUNT`), and a wrong TOTP code on the reset page counts towards the normal sign-in lockout. Every staff member with an authenticator confirms with it, not only merchant operators. Customer accounts (sprint 11) reuse the same flow.

Customer accounts (sprint 11, `gateway/apps/customers`) use the same rules: API `/customer/password-reset/request`, `/check`, `/confirm`; page `/account/reset#token=...` (`ACCOUNT_URL`, which defaults to `/account` next to `GUEST_STATUS_URL`). Customers have no TOTP, so there is no code step. A reset ends every customer session and clears the sign-in lockout. Staff and customer links are kept apart: a customer's link is refused by the staff reset and the other way round.

## 10. Decisions taken for this contract

P1 to P3 were confirmed by Fifa on 13 November 2026 and are in `GATEWAY_RULES`. P4 was confirmed by Fifa on 13 November 2026 after the sprint 3 build.

| # | Decision | Value | Settings |
| --- | --- | --- | --- |
| P1 | Webhook retry window, schedule in section 7 | 72 h | `WEBHOOK_RETRY_WINDOW_HOURS`, `WEBHOOK_RETRY_DELAYS_SECONDS`, `WEBHOOK_TIMEOUT_SECONDS` (replaces `WEBHOOK_MAX_ATTEMPTS`) |
| P2 | Password reset link, single use | 30 min | `PASSWORD_RESET_LINK_TTL_MINUTES` |
| P3 | Guest status: receipt link is not a credential; view session; code request limits | 15 min; 3 per pair and 10 per IP per 15 min | `GUEST_STATUS_*` |
| P4 | Processor refuses a capture or cancel the gateway allowed (records disagree) | `409 processor_rejected`, payment unchanged | none (`apps/payments/settlement.py`) |

## 11. Readings of the URS (settled 13 November 2026)

Fifa asked that each reading follow common industry practice. Checked against Stripe and Adyen:

1. **Webhooks on every status change (FR-TXN-06).** Stripe also notifies in-progress states (`payment_intent.requires_action`, `payment_intent.processing`), so this contract sends `payment.verification_required` and `payment.processing` too. `created` (the merchant just made it) and `screening` (internal, milliseconds) are not sent.
2. **Rejected refunds (FR-TXN-14).** Like Stripe, a refund above the refundable amount is an error reply and creates no refund object; "requested" and "rejected" appear only in the dashboard history.
3. **Partial capture (UC-24).** Stripe (`amount_to_capture`) and Adyen both allow capturing less than the authorised amount, so this contract does too (section 3), once per payment.
4. **Amount check (FR-CHK-03, MVP-SCOPE open point 2).** As with a Stripe PaymentIntent, the amount is fixed when the merchant creates the payment, and that record is what checkout is checked against.

## 12. Not covered yet

- **Hosted checkout and dashboard (`/checkout/*`, `/internal/*`).** Same-origin browser APIs; they will be generated from the code by drf-spectacular rather than designed here.
- **API key rotation and revocation.** Later Should items (MVP-SCOPE section 3). Merchant self-registration (sprint 10) is a dashboard flow under `/internal/register`, not part of the merchant API.

## 13. Traceability

| Requirement | Section |
| --- | --- |
| FR-MER-03, FR-MER-08, NFR-CON-01, NFR-AVA-02 | 2 |
| FR-MER-11, FR-CHK-03, FR-CHK-08, FR-TXN-01 to FR-TXN-05, FR-TXN-09, FR-TXN-10, FR-TXN-14 | 3 |
| FR-CHK-09, FR-CHK-10, NFR-INT-03 | 5 |
| FR-TXN-06, FR-TXN-07, FR-TXN-08, FR-ADM-13, NFR-INT-01 | 6, 7 |
| FR-ACC-05, FR-ACC-06, FR-CHK-15 | 8 |
| FR-ACC-07 to FR-ACC-09 | 9 |
