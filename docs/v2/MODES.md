# Test mode and live mode (v2, Sprint 1)

Every merchant can hold two API keys at once: a test key (`sp_test_...`, the v1 key) and a
live key (`sp_live_...`). The key alone decides the mode. The same API call or SDK call
made with the other key lands in the other mode. In this project "live" is still simulated
money: both modes go to our own network simulator.

## What belongs to a mode

| Thing | How it gets its mode |
| --- | --- |
| Payment | `livemode` of the key that created it (`payments.Payment.livemode`) |
| Refund, events | the payment's mode (`livemode` in the Refund and in every event) |
| Payout | the key's mode, or `mode` in the dashboard request (`ledger.Payout.livemode`) |
| Ledger | every account exists once per mode, gateway-wide ones included; an entry has the mode of its accounts |
| Idempotency-Key | kept per merchant **and** mode, so the same key can be used in both |

A key only sees its own mode. A payment, refund or payout of the other mode answers
`404 resource_not_found`, exactly like a record that does not exist, and `GET /v1/balance`
is the balance of the key's mode.

## Rules that keep the modes apart

- `ApiKey`: at most one active key per merchant and mode (database constraint).
- `posting.post` refuses an entry whose accounts are not all in the entry's mode.
- `manage.py ledger_check` reports any entry that mixes modes, or whose mode differs from
  its payment's.
- Gateway totals on the dashboard's Balance page are per mode (`?mode=test|live`), so the
  identity clearing = fees + merchants' available + reserve holds in each mode on its own.

## Keys in the dashboard

`POST /internal/merchant/api-key` takes `{"mode": "test"|"live"}` (default test).
`/internal/merchant/api-key/rotate` and `/revoke` take `mode` as well. The signing secret is
shared by both modes: generating the second key keeps it (the reply has
`signing_secret: null`), and rotating either key replaces it. `/internal/merchant` shows
`api_key` (test, as in v1) and `live_api_key`.

For a seeded merchant: `manage.py create_merchant "Name" --live` prints both keys.

## Dashboard and checkout

- A **Live mode** switch in the dashboard's top bar picks the mode the Payments list and the
  Balance page (balance, ledger entries, payouts, the payout form) show. It is remembered per
  browser (localStorage) and starts on test. A search on the Payments page looks in both
  modes and marks live payments "Live", because a receipt does not say which mode it is.
- The API keys page lists the test and the live key with a Mode column, offers "Generate test
  key" and "Generate live key", and rotates or revokes each one on its own.
- `GET /internal/payments` takes `mode=test|live` (no `mode`: both), and each row has
  `livemode`.
- The hosted checkout keeps its "Test mode" pill for test payments and shows "Demo" for live
  ones (the checkout session has `livemode`), since live is still simulated money here.
