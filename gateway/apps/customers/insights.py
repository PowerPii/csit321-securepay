"""Spending insights for the signed-in customer (account page "Spending").

Only this customer's own payments are read, the same ones the payment history lists. Money
counts as spent once the merchant has charged the card (captured), less every completed
refund. A payment that is only authorised is held, not yet spent, and is reported apart.

Each payment counts in the month of its payment date (Singapore time), the date the history
shows, and a refund lowers the month of the payment it belongs to, so a month never goes
below zero. No card details are returned.
"""
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.customers.history import visible_history
from apps.customers.models import CustomerAccount
from apps.payments.states import PaymentStatus

MONTHS = 12          # the window: this month and the 11 before it
TOP_MERCHANTS = 5    # the rest are added up as "Other merchants"
OTHER = "Other merchants"


def _zone() -> ZoneInfo:
    return ZoneInfo(getattr(settings, "DASHBOARD_TIME_ZONE", "Asia/Singapore"))


def _month_starts(now: datetime) -> list[tuple[int, int]]:
    """(year, month) for the last MONTHS months, oldest first, ending with `now`'s month."""
    year, month = now.year, now.month
    out = []
    for _ in range(MONTHS):
        out.append((year, month))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return out[::-1]


def insights(customer: CustomerAccount, now: datetime | None = None) -> dict:
    from apps.refunds.models import Refund

    zone = _zone()
    local_now = timezone.localtime(now or timezone.now(), zone)
    months = _month_starts(local_now)
    first_year, first_month = months[0]
    start = datetime(first_year, first_month, 1, tzinfo=zone)

    payments = list(visible_history(customer).filter(created_at__gte=start)
                    .only("pk", "amount", "settlement_amount", "captured_settlement_amount",
                          "captured_amount", "status", "created_at", "merchant__name"))
    charged = [p for p in payments if p.captured_amount > 0]
    refunded_by_payment = dict(
        Refund.objects.filter(payment__in=charged, status=Refund.Status.COMPLETED)
        .values("payment_id").annotate(total=Sum("settlement_amount"))
        .values_list("payment_id", "total"))

    by_month = {key: {"spent": 0, "refunded": 0, "payments": 0} for key in months}
    by_merchant: dict[str, dict] = defaultdict(lambda: {"spent": 0, "refunded": 0, "payments": 0})
    for payment in charged:
        # In SGD (v2 FX), so payments in different currencies add up.
        refunded = min(refunded_by_payment.get(payment.pk, 0),
                       payment.captured_settlement_amount)
        local = timezone.localtime(payment.created_at, zone)
        for bucket in (by_month[(local.year, local.month)], by_merchant[payment.merchant.name]):
            bucket["spent"] += payment.captured_settlement_amount
            bucket["refunded"] += refunded
            bucket["payments"] += 1

    def row(values: dict) -> dict:
        return {**values, "net": values["spent"] - values["refunded"]}

    month_rows = [{"month": f"{y:04d}-{m:02d}", **row(by_month[(y, m)])} for y, m in months]
    merchants = sorted((row(v) | {"merchant_name": name} for name, v in by_merchant.items()),
                       key=lambda r: (-r["net"], -r["payments"], r["merchant_name"]))
    if len(merchants) > TOP_MERCHANTS + 1:
        rest = merchants[TOP_MERCHANTS:]
        merchants = merchants[:TOP_MERCHANTS] + [{
            "merchant_name": OTHER, "merchant_count": len(rest),
            **{k: sum(r[k] for r in rest) for k in ("spent", "refunded", "payments", "net")}}]

    held = [p for p in payments if p.status == PaymentStatus.AUTHORISED and not p.captured_amount]
    spent = sum(r["spent"] for r in month_rows)
    refunded = sum(r["refunded"] for r in month_rows)
    return {
        "currency": "SGD",
        "time_zone": str(zone),
        "from": month_rows[0]["month"],
        "to": month_rows[-1]["month"],
        "totals": {
            "spent": spent,
            "refunded": refunded,
            "net": spent - refunded,
            "payments": len(charged),
            "merchants": len(by_merchant),
            "on_hold": sum(p.settlement_amount for p in held),
            "on_hold_payments": len(held),
        },
        "by_month": month_rows,
        "by_merchant": merchants,
    }
