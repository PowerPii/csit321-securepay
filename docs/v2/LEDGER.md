# Double-entry ledger (v2, Sprint 1)

The `ledger` app (`gateway/apps/ledger`) keeps the books. Every time money moves, the gateway
posts one **journal entry** with two or more **lines**. A line's amount is signed: a debit is
positive, a credit is negative, and the lines of an entry always sum to zero.

## Accounts

| Account | Owner | Normal side | Means |
| --- | --- | --- | --- |
| `network_clearing` | gateway | debit | Money the card network owes us for captures, less refunds |
| `fee_revenue` | gateway | credit | Our processing fees |
| `merchant_available` | each merchant | credit | Owed to the merchant now |
| `merchant_reserve` | each merchant | credit | Owed to the merchant, held back as a rolling reserve |

Accounts are created the first time a posting needs them (one per kind, merchant, currency
and mode). Test and live mode keep separate books: see [MODES.md](MODES.md).

## What posts what

**Capture** (in `apps/payments/settlement.py`, same transaction as the `payment.captured` outbox row):

| Line | Account | Amount |
| --- | --- | --- |
| gross | `network_clearing` | + captured amount |
| fee | `fee_revenue` | − fee |
| reserve | `merchant_reserve` | − reserve |
| net | `merchant_available` | − the rest |

Fee and reserve come from `GATEWAY_RULES` (confirmed by Fifa, 27 Nov 2026): fee 2.9% + S$0.30
(`FEE_PERCENT_BPS` 290, `FEE_FIXED_CENTS` 30), reserve 10% of the gross (`RESERVE_BPS` 1000).
The fee rounds half up, the reserve rounds down, and the net takes the remainder, so the three
always add up to the gross. Example, S$120.00: fee S$3.78, reserve S$12.00, net S$104.22.

**Completed refund** (in `apps/refunds/service.py`, same transaction as `refund.completed`):
`merchant_available` + amount, `network_clearing` − amount. The fee is not given back.

A declined, failed or unresolved capture or refund posts nothing. If posting fails, the whole
state change rolls back with it; the simulator's answer is then settled later by
reconciliation, which posts the entry, as for a lost answer.

## Rules the database enforces (migration `ledger/0002`)

- **Balanced.** At COMMIT, every entry written in the transaction must have at least two lines
  summing to zero in each currency (a deferred constraint trigger). Code that skips
  `posting.py` still cannot commit an unbalanced entry.
- **Append-only.** UPDATE and DELETE on entries and lines are refused, by the models and by
  triggers. A mistake is fixed with a new reversing entry.
- **Once per cause.** One entry per `(kind, source_id)`: a repeated or late answer cannot post twice.
- Lines are never zero; merchant accounts always have a merchant and gateway accounts never do.

## Commands

```bash
python manage.py ledger_check              # exit 1 if any entry is unbalanced, or a capture/refund has no entry
python manage.py ledger_backfill --dry-run # count v1 captures and refunds with no entry yet
python manage.py ledger_backfill           # post them (marked backfilled, dated when the money moved)
```

Run `ledger_backfill` once on each database that holds payments from before the ledger
(staging now, production when v2 ships), then `ledger_check`. Both are safe to run again.
`ledger_check` also checks that every payout has its entry and every reserve release moves
exactly the reserve its capture held.

## Reserve release (part 2)

Each capture's reserve is held for `RESERVE_HOLD_DAYS` (7 days, confirmed by Fifa
27 Nov 2026; real processors hold 90 to 180 days, the chargeback window, so 7 is our
scaled-down value that shows a release within a sprint), then the worker moves it to the merchant's available balance (`apps/ledger/jobs.py`,
job `release_reserves`):

| Line | Account | Amount |
| --- | --- | --- |
| release | `merchant_reserve` | + the capture's reserve |
| release | `merchant_available` | − the same |

The entry's `source_id` is the capture entry's `jrn_...`, so each reserve is released once.

## Payouts (part 2)

A merchant operator pays out from the available balance on the dashboard's Balance page
(`POST /internal/payouts {"amount": cents}`). `apps/ledger/payouts.py` locks the merchant's
available account, checks the balance and the minimum (`PAYOUT_MIN_CENTS`, S$1.00), creates
the `Payout` (`po_...`) and posts, all in one transaction, and writes `payout.paid` to the
audit log. Two payouts at once cannot together take more than the balance.

| Line | Account | Amount |
| --- | --- | --- |
| payout | `merchant_available` | + amount |
| payout | `network_clearing` | − amount |

The bank transfer itself is simulated: a payout is `paid` at once. Paying out of
`network_clearing` keeps one simple identity, checked on the administrator's Balance page:
**clearing = fee revenue + every merchant's available + reserve**. A separate bank account,
filled by the acquirer's settlement files, comes with the "settlement files" Could.

## Staff API and dashboard (part 2)

| Endpoint | Merchant operator | Administrator | Fraud analyst |
| --- | --- | --- | --- |
| `GET /internal/balance` | own available, reserve, next release, fee rules | gateway accounts, every merchant, ledger check | 403 |
| `GET /internal/ledger/entries?kind=&merchant=&page=` | own entries with lines | all, filter by merchant | 403 |
| `GET /internal/payouts` | own | all | 403 |
| `POST /internal/payouts` | yes | 403 (read-only) | 403 |

Dashboard: **Balance** (under Payments). Operators see three tiles (available, reserve, next
release), the payout form, the entries (click one for its debit and credit lines) and their
payouts. Administrators see clearing, fees and what merchants are owed, whether the books
balance, every merchant's balances and all entries.

## Next

The public merchant API (`GET /v1/balance`, `/v1/payouts`) is done (PR #97), and so are
separate test and live books ([MODES.md](MODES.md)). Still to do: extending the audit hash
chain to ledger entries.
