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

Accounts are created the first time a posting needs them (one per kind, merchant and currency).

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

## Next (Sprint 1, ledger part 2)

Merchant balance API and dashboard view, payouts from `merchant_available`, reserve release
after a holding period, and extending the audit hash chain to ledger entries.
