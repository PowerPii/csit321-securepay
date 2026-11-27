"""Ledger self-checks, shared by `manage.py ledger_check` and the tests."""
from django.db.models import Count, F, Q, Sum

from apps.ledger.models import JournalEntry, JournalLine, Payout


def problems() -> list[str]:
    from apps.payments.models import Payment
    from apps.refunds.models import Refund

    found = []
    unbalanced = (JournalLine.objects.values("entry__public_id", "currency")
                  .annotate(total=Sum("amount")).exclude(total=0))
    for row in unbalanced:
        found.append(f"{row['entry__public_id']} does not balance in {row['currency']} "
                     f"({row['total']:+d})")
    short = (JournalEntry.objects.annotate(n=Count("lines")).filter(n__lt=2)
             .values_list("public_id", flat=True))
    found += [f"{pid} has fewer than two lines" for pid in short]
    mixed = (JournalLine.objects.exclude(account__livemode=F("entry__livemode"))
             .values_list("entry__public_id", flat=True).distinct())
    found += [f"{pid} mixes test and live accounts" for pid in mixed]
    wrong_mode = (JournalEntry.objects.filter(payment__isnull=False)
                  .exclude(livemode=F("payment__livemode"))
                  .values_list("public_id", flat=True))
    found += [f"{pid} is not in its payment's mode" for pid in wrong_mode]

    missing_captures = (Payment.objects.filter(captured_amount__gt=0)
                        .exclude(journal_entries__kind=JournalEntry.Kind.CAPTURE)
                        .values_list("public_id", flat=True))
    found += [f"{pid} was captured but has no ledger entry" for pid in missing_captures]
    missing_refunds = (Refund.objects.filter(status=Refund.Status.COMPLETED)
                       .exclude(journal_entries__kind=JournalEntry.Kind.REFUND)
                       .values_list("public_id", flat=True))
    found += [f"{rid} was completed but has no ledger entry" for rid in missing_refunds]

    # The gross posted must match what the payment says was captured (in SGD, v2 FX).
    gross = (JournalEntry.objects.filter(kind=JournalEntry.Kind.CAPTURE)
             .annotate(gross=Sum("lines__amount", filter=Q(lines__memo="gross")))
             .exclude(gross=F("payment__captured_settlement_amount"))
             .values_list("public_id", "gross", "payment__public_id",
                          "payment__captured_settlement_amount"))
    found += [f"{pid} posts {g} but {pay} captured {cap}" for pid, g, pay, cap in gross]

    missing_payouts = (Payout.objects.exclude(journal_entries__kind=JournalEntry.Kind.PAYOUT)
                       .values_list("public_id", flat=True))
    found += [f"{pid} was paid but has no ledger entry" for pid in missing_payouts]

    # A reserve release moves exactly the reserve its capture held.
    held = dict(JournalLine.objects.filter(entry__kind=JournalEntry.Kind.CAPTURE, memo="reserve")
                .values_list("entry__public_id", "amount"))
    released = (JournalLine.objects.filter(entry__kind=JournalEntry.Kind.RESERVE_RELEASE,
                                           account__kind="merchant_reserve")
                .values_list("entry__public_id", "entry__source_id", "amount"))
    for pid, capture_id, amount in released:
        if held.get(capture_id) is None or amount != -held[capture_id]:
            found.append(f"{pid} releases {amount} but {capture_id} held "
                         f"{-(held.get(capture_id) or 0)}")
    return found
