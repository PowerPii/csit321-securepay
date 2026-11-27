# Multi-currency and FX (v2, Sprint 2)

A merchant can charge a shopper in one of eight currencies. The gateway converts each payment to
Singapore dollars at a rate it locks when the payment is created. Everything after that point
works in SGD: the simulator and card network, the ledger, the balance and payouts.

## Currencies

| Code | Name | Minor unit | Example `amount` |
| --- | --- | --- | --- |
| SGD | Singapore dollar | 2 | `4590` = S$45.90 |
| USD | US dollar | 2 | `1000` = USD 10.00 |
| EUR | Euro | 2 | `1000` = EUR 10.00 |
| MYR | Malaysian ringgit | 2 | `5000` = MYR 50.00 |
| THB | Thai baht | 2 | `119200` = THB 1,192.00 |
| IDR | Indonesian rupiah | 0 | `150000` = IDR 150,000 |
| PHP | Philippine peso | 2 | `100000` = PHP 1,000.00 |
| JPY | Japanese yen | 0 | `5000` = JPY 5,000 |

`amount` is always a whole number in the currency's smallest unit. JPY and IDR have no minor
unit, so `5000` JPY is five thousand yen. Any other code gets `422 currency_not_supported`. The
list lives in `gateway/apps/fx/currencies.py`.

## Rates

The gateway keeps its own daily **rate sheet**. It does not call an outside rate service.
Each rate is SGD per one whole unit of the foreign currency, stored as a decimal string
(for example THB `0.038500`).

- The first sheet is the reference sheet in `gateway/apps/fx/reference_rates.json`. It is
  published automatically the first time a rate is needed.
- Staff publish a new sheet with a command. Each publish writes an `fx.rates_published` audit entry:

  ```bash
  python manage.py publish_fx_rates --set THB=0.0390 --set JPY=0.0087 --source "MAS 28 Nov"
  python manage.py publish_fx_rates --file rates.json
  ```

  `--set` changes only the currencies it names and keeps the rest of the current sheet.
- Merchants read the current sheet with `GET /v1/fx_rates`. See
  `contracts/schemas/fx-rates.schema.json` and `contracts/examples/api/get-fx-rates.200.json`.

## Locking the rate

When a payment is created, the gateway saves three things with it:

- the current rate (`fx_rate`);
- the sheet it came from (`fx_rate_sheet`);
- the fee in basis points (`fx_fee_bps`).

A rate sheet published later never changes that payment. The SGD amount is
`round_half_up(amount / 10^minor × rate, 2)`, in cents. Every Payment and Refund response
includes a `settlement` object:

```json
"settlement": {
  "currency": "SGD",
  "amount": 4589,
  "captured_amount": 4589,
  "fx": {"rate": "0.0385", "rate_sheet": "fxr_…", "fee_percent": "1"}
}
```

`fx` is `null` for SGD payments.

The S$0.50 to S$10,000 payment limits and each merchant's own limit are checked on the SGD
amount, so the same limits apply whatever the currency.

## Capture, cancel and refund

- **Partial capture.** The capture amount is in the payment currency. It is converted at the
  locked rate, and its SGD value is stored in `captured_settlement_amount`.
- **Refunds** are converted so the rounding adds up. A refund's SGD value is the SGD value of
  everything refunded up to and including it, less the SGD value of everything refunded
  before it. Refunding a payment in parts therefore never returns more SGD than was captured.
  For example, three refunds of THB 333.33, 333.34 and 333.33 out of a THB 1,000 capture
  (S$38.50) come to S$12.83, S$12.84 and S$12.83.
- **Simulator / card network.** Every message carries the SGD amount with currency `SGD`.
  A later slice could add the presentment amount as well (DE4/DE49 with DE5/DE50).

## Fees and the ledger

The books stay in SGD. A capture in a foreign currency posts one extra line:

| Line | Account | Amount |
| --- | --- | --- |
| gross | `network_clearing` | + captured SGD |
| fee | `fee_revenue` | − (2.9% + S$0.30) |
| fx_fee | `fee_revenue` | − 1% of captured SGD (`GATEWAY_RULES["FX_FEE_BPS"] = 100`) |
| reserve | `merchant_reserve` | − 10% of captured SGD |
| net | `merchant_available` | − the rest |

Fifa set the 1% FX fee on 28 Nov 2026. An SGD payment has no `fx_fee` line. The ledger check
(`python manage.py ledger_check`) compares each payment's gross line to
`captured_settlement_amount`.

## Not covered

- The payment's integrity hash does not include the FX fields. Adding them would change the
  hash of every existing row.
- There is no live rate feed. Rates change only when staff publish a sheet.
