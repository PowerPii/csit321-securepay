"""Post journal entries for captures and refunds.

Call these inside the transaction that changes the payment or refund, with the payment row
locked, next to the outbox event (CONTRIBUTING rule 3). If posting fails, the state change
and the event roll back with it, so the books and the payments can never disagree.
"""
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.ids import new_id
from apps.ledger import fees
from apps.ledger.models import JournalEntry, JournalLine, LedgerAccount

Kind = LedgerAccount.Kind


def account(kind: str, merchant=None, currency: str = "SGD") -> LedgerAccount:
    """The account of this kind (for this merchant), created the first time it is needed."""
    merchant_id = merchant.pk if kind in LedgerAccount.MERCHANT_KINDS else None
    found = LedgerAccount.objects.filter(kind=kind, merchant_id=merchant_id,
                                         currency=currency).first()
    if found:
        return found
    try:
        with transaction.atomic():
            return LedgerAccount.objects.create(kind=kind, merchant_id=merchant_id,
                                                currency=currency)
    except IntegrityError:  # another transaction created it first
        return LedgerAccount.objects.get(kind=kind, merchant_id=merchant_id, currency=currency)


def post(*, kind: str, source_id: str, merchant, lines, payment=None, refund=None,
         payout=None, description: str = "", occurred_at=None,
         backfilled: bool = False) -> JournalEntry:
    """Write one entry and its lines. `lines` is [(account, signed amount, memo)].

    Returns the existing entry if this source was already posted, so a repeated call is safe.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("ledger entries must be posted inside the state change's transaction")
    lines = [(acc, amount, memo) for acc, amount, memo in lines if amount != 0]
    if len(lines) < 2:
        raise ValueError("a journal entry needs at least two non-zero lines")
    totals: dict[str, int] = {}
    for acc, amount, _ in lines:
        totals[acc.currency] = totals.get(acc.currency, 0) + amount
    if any(totals.values()):
        raise ValueError(f"journal entry does not balance: {totals}")

    existing = JournalEntry.objects.filter(kind=kind, source_id=source_id).first()
    if existing:
        return existing
    entry = JournalEntry(public_id=new_id("jrn"), kind=kind, source_id=source_id,
                         payment=payment, refund=refund, payout=payout, merchant=merchant,
                         description=description, occurred_at=occurred_at or timezone.now(),
                         backfilled=backfilled)
    entry.save(force_insert=True)
    JournalLine.objects.bulk_create(
        JournalLine(entry=entry, account=acc, amount=amount, currency=acc.currency, memo=memo)
        for acc, amount, memo in lines)
    return entry


def post_capture(payment, *, backfilled: bool = False) -> JournalEntry:
    """Gross in from the network; fee to us; reserve and net to the merchant.

    Dr network_clearing  gross
    Cr fee_revenue       fee
    Cr merchant_reserve  reserve
    Cr merchant_available net
    """
    s = fees.split(payment.captured_amount)
    m, cur = payment.merchant, payment.currency
    return post(
        kind=JournalEntry.Kind.CAPTURE, source_id=payment.public_id, payment=payment,
        merchant=m, description=f"Capture {payment.reference}",
        occurred_at=payment.captured_at, backfilled=backfilled,
        lines=[
            (account(Kind.NETWORK_CLEARING, currency=cur), s.gross, "gross"),
            (account(Kind.FEE_REVENUE, currency=cur), -s.fee, "fee"),
            (account(Kind.MERCHANT_RESERVE, m, cur), -s.reserve, "reserve"),
            (account(Kind.MERCHANT_AVAILABLE, m, cur), -s.net, "net"),
        ])


def post_refund(refund, *, backfilled: bool = False) -> JournalEntry:
    """The merchant pays the refund back out through the network. The fee is not returned.

    Dr merchant_available amount
    Cr network_clearing   amount
    """
    payment = refund.payment
    m, cur = payment.merchant, payment.currency
    return post(
        kind=JournalEntry.Kind.REFUND, source_id=refund.public_id, payment=payment,
        refund=refund, merchant=m, description=f"Refund {refund.reference}",
        occurred_at=refund.completed_at, backfilled=backfilled,
        lines=[
            (account(Kind.MERCHANT_AVAILABLE, m, cur), refund.amount, "refund"),
            (account(Kind.NETWORK_CLEARING, currency=cur), -refund.amount, "refund"),
        ])


def reserve_line(capture_entry: JournalEntry):
    return capture_entry.lines.filter(memo="reserve").select_related("account").first()


def post_reserve_release(capture_entry: JournalEntry) -> JournalEntry | None:
    """Move one capture's reserve to the merchant's available balance (after the hold).

    Dr merchant_reserve   reserve
    Cr merchant_available reserve

    Returns None when the capture held no reserve.
    """
    line = reserve_line(capture_entry)
    if line is None:
        return None
    amount = -line.amount  # the reserve line is a credit
    m, cur = capture_entry.merchant, line.currency
    return post(
        kind=JournalEntry.Kind.RESERVE_RELEASE, source_id=capture_entry.public_id,
        payment=capture_entry.payment, merchant=m,
        description=f"Reserve release for {capture_entry.description.removeprefix('Capture ')}",
        lines=[
            (account(Kind.MERCHANT_RESERVE, m, cur), amount, "release"),
            (account(Kind.MERCHANT_AVAILABLE, m, cur), -amount, "release"),
        ])


def post_payout(payout) -> JournalEntry:
    """Dr merchant_available amount, Cr network_clearing amount.

    The network's settlement funds pay the merchant directly in this slice; a separate bank
    account (and settlement files from the acquirer) come with the "settlement files" Could.
    """
    m, cur = payout.merchant, payout.currency
    return post(
        kind=JournalEntry.Kind.PAYOUT, source_id=payout.public_id, payout=payout, merchant=m,
        description=f"Payout {payout.public_id}", occurred_at=payout.created_at,
        lines=[
            (account(Kind.MERCHANT_AVAILABLE, m, cur), payout.amount, "payout"),
            (account(Kind.NETWORK_CLEARING, currency=cur), -payout.amount, "payout"),
        ])


def merchant_balances(merchant, currency: str = "SGD") -> dict:
    """{'available': cents, 'reserve': cents} for one merchant, from the ledger alone."""
    out = {}
    for kind, name in ((Kind.MERCHANT_AVAILABLE, "available"),
                       (Kind.MERCHANT_RESERVE, "reserve")):
        acc = LedgerAccount.objects.filter(kind=kind, merchant=merchant,
                                           currency=currency).first()
        out[name] = acc.balance() if acc else 0
    return out
