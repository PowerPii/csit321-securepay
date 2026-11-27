"""A merchant's balance as the dashboard and the merchant API show it."""
from apps.common.timefmt import rfc3339
from apps.ledger import jobs, posting
from apps.ledger.models import JournalEntry


def next_release(merchant, livemode: bool = False) -> dict | None:
    """The oldest capture whose reserve is still held: how much and when it is released."""
    released = JournalEntry.objects.filter(kind=JournalEntry.Kind.RESERVE_RELEASE,
                                           merchant=merchant, livemode=livemode
                                           ).values("source_id")
    entry = (JournalEntry.objects.filter(kind=JournalEntry.Kind.CAPTURE, merchant=merchant,
                                         livemode=livemode, lines__memo="reserve")
             .exclude(public_id__in=released).select_related("payment")
             .order_by("occurred_at", "id").first())
    if entry is None:
        return None
    line = posting.reserve_line(entry)
    return {"amount": -line.amount, "at": rfc3339(jobs.reserve_release_at(entry)),
            "payment_id": entry.payment.public_id if entry.payment_id else None}


def balance_to_dict(merchant, currency: str = "SGD", *, livemode: bool = False) -> dict:
    """The Balance object (contracts/schemas/balance.schema.json), for one mode."""
    b = posting.merchant_balances(merchant, currency, livemode=livemode)
    return {"object": "balance", "livemode": livemode, "currency": currency,
            "available": b["available"], "reserve": b["reserve"],
            "next_release": next_release(merchant, livemode)}
