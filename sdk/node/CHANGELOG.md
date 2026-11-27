# Changelog

## Unreleased (v2)

- `balance.retrieve()`: what you can pay out now (`available`), what is held in reserve, and the next reserve release (`GET /v1/balance`).
- `payouts.create({ amount })`, `payouts.list({ limit })` and `payouts.retrieve(payoutId)` (`/v1/payouts`). A payout above the available balance is a `422 insufficient_balance`.
- Types: `Balance`, `Payout`, `PayoutList`, `CreatePayoutParams`.
- Test and live mode: live keys start `sp_live_`. The same calls work with either key; the key decides the mode. `client.livemode` says which one you have, and every Payment, Refund, Payout, Balance and event has `livemode`.
- Multi-currency: `currency` can be SGD, USD, EUR, MYR, THB, IDR, PHP or JPY (amounts in that currency's smallest unit; JPY and IDR have none). Every Payment and Refund has `settlement` with the SGD amount, and for other currencies the locked rate and the 1% FX fee. `fxRates.retrieve()` returns today's rate sheet (`GET /v1/fx_rates`). Types: `FxRates`, `Currency`.

## 0.2.0 (2026-11-19)

Makes a first integration recoverable after a lost reply or a merchant restart (Codex completion audit, 19 Nov 2026).

- Every error now has `idempotencyKey` (the key every attempt of that `POST` used) and `outcomeUnknown` (`true` when the gateway may have done the work: no answer, a `5xx` or `409 idempotency_request_in_progress` after the retries, or a reply that failed the signature check). `SecurePayConnectionError` also has `attempts`, and its message names the key to retry with.
- README and examples: save one key with the order before `payments.create`, one key per refund, retry with the saved key after a restart, and the 24-hour key window with a reconciliation path after it.
- `idempotencyKey` is checked before sending (8 to 255 printable ASCII characters, the gateway's rule).
- **Breaking:** `baseUrl` must be `https://`. Plain `http://` is accepted only for `localhost`, `*.localhost`, `127.0.0.1` or `[::1]`, and only with the new option `allowInsecureHttp: true`. A URL with a user name, password, query string or fragment is refused.
- **Breaking:** `timeoutMs` (1 to 600,000), `maxRetries` (whole number, 0 to 10) and the webhook `toleranceSeconds` (1 to 3,600) must be finite and within those limits; anything else throws a `RangeError`. A `toleranceSeconds` of 0 or `NaN` used to turn the freshness check off.
- `author` in package.json is now an object, so npm shows the name correctly.

## 0.1.0 (2026-11-19)

First release.

- `SecurePay` client: `payments.create`, `retrieve`, `capture`, `cancel`; `refunds.create`, `list`, `retrieve`.
- Automatic `Idempotency-Key` on every `POST`; retries for timeouts, network failures, `429`, `5xx` and `409 idempotency_request_in_progress`, always with the same key.
- Signature checks on every reply when `signingSecret` is set.
- Webhooks: `constructEvent`, `verifySignature`, `signPayload`.
- Types generated from the merchant API's OpenAPI 3.1 file.
