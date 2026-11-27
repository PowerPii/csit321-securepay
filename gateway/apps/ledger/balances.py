"""A merchant's balance as the dashboard and the merchant API show it."""
from apps.common.timefmt import rfc3339
from apps.ledger import jobs, posting
from apps.ledger.models import JournalEntry


def next_release(merchant) -> dict | None:
    """The oldest capture whose reserve is still held: how much and when it is released."""
    released = JournalEntry.objects.filter(kind=JournalEntry.Kind.RESERVE_RELEASE,
                                           merchant=merchant).values("source_id")
    entry = (JournalEntry.objects.filter(kind=JournalEntry.Kind.CAPTURE, merchant=merchant,
                                         lines__memo="reserve")
             .exclude(public_id__in=released).select_related("payment")
             .order_by("occurred_at", "id").first())
    if entry is None:
        return None
    line = posting.reserve_line(entry)
    return {"amount": -line.amount, "at": rfc3339(jobs.reserve_release_at(entry)),
            "payment_id": entry.payment.public_id if entry.payment_id else None}


def balance_to_dict(merchant, currency: str = "SGD") -> dict:
    """The Balance object (contracts/schemas/balance.schema.json)."""
    b = posting.merchant_balances(merchant, currency)
    return {"object": "balance", "currency": currency, "available": b["available"],
            "reserve": b["reserve"], "next_release": next_release(merchant)}
