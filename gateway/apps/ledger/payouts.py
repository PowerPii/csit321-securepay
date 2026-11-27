"""Payouts: a merchant operator sends (part of) the available balance to the bank."""
from django.conf import settings
from django.db import transaction

from apps.audit.models import record as audit
from apps.common.errors import ApiError, invalid
from apps.common.ids import new_id
from apps.ledger import posting
from apps.ledger.models import LedgerAccount, Payout


def _dollars(cents: int) -> str:
    return f"S${cents / 100:,.2f}"


def create(merchant, amount, *, actor_type: str, actor_id: str, request_id: str = "",
           currency: str = "SGD", livemode: bool = False) -> Payout:
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise invalid("invalid_field", "amount must be a whole number of cents.", param="amount")
    minimum = settings.GATEWAY_RULES["PAYOUT_MIN_CENTS"]
    if amount < minimum:
        raise invalid("amount_too_small", f"The smallest payout is {_dollars(minimum)}.",
                      param="amount")
    with transaction.atomic():
        available = posting.account(LedgerAccount.Kind.MERCHANT_AVAILABLE, merchant, currency,
                                    livemode=livemode)
        # The lock makes balance check + posting one step: concurrent payouts queue here.
        LedgerAccount.objects.select_for_update().get(pk=available.pk)
        balance = available.balance()
        if amount > balance:
            raise ApiError(422, "insufficient_balance",
                           f"Only {_dollars(max(balance, 0))} is available to pay out.",
                           param="amount")
        payout = Payout(public_id=new_id("po"), merchant=merchant, livemode=livemode,
                        amount=amount, currency=currency, requested_by=actor_id)
        payout.save(force_insert=True)
        entry = posting.post_payout(payout)
        audit("payout.paid", actor_type=actor_type, actor_id=actor_id,
              merchant_id=merchant.public_id, target_type="payout",
              target_id=payout.public_id, request_id=request_id,
              details={"amount": amount, "currency": currency, "livemode": livemode,
                       "journal_entry": entry.public_id})
    return payout


def to_dict(payout: Payout) -> dict:
    from apps.common.timefmt import rfc3339

    return {"id": payout.public_id, "object": "payout", "livemode": payout.livemode,
            "amount": payout.amount,
            "currency": payout.currency, "status": payout.status,
            "merchant_id": payout.merchant.public_id, "created_at": rfc3339(payout.created_at)}
