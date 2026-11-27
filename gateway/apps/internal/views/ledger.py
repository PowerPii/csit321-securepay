"""Balances, ledger entries and payouts (v2 ledger, part 2).

Merchant operators see their own merchant's balances, entries and payouts, and can pay out
from the available balance. Administrators see the gateway-wide accounts, every merchant's
balances and entries (and may filter by one), read-only. Fraud analysts are refused.

* GET  balance                 balances (operator: own; administrator: gateway + merchants)
* GET  ledger/entries?kind=&merchant=&page=&page_size=   newest first, with their lines
* GET  payouts?page=&page_size=
* POST payouts {"amount": cents}   operator only; audited
"""
from django.conf import settings
from django.db.models import Sum
from rest_framework import exceptions, status
from rest_framework.response import Response

from apps.accounts.models import Role
from apps.common.errors import invalid
from apps.common.timefmt import rfc3339
from apps.common.views import body_or_empty
from apps.internal.access import StaffAPIView
from apps.internal.query_params import bounded_int
from apps.ledger import balances, checks, payouts, posting
from apps.ledger.models import JournalEntry, LedgerAccount, Payout
from apps.merchants.models import Merchant

OPERATOR_OR_ADMIN = (Role.MERCHANT_OPERATOR, Role.ADMINISTRATOR)
Kind = LedgerAccount.Kind


def _rules() -> dict:
    r = settings.GATEWAY_RULES
    return {"fee_percent_bps": r["FEE_PERCENT_BPS"], "fee_fixed": r["FEE_FIXED_CENTS"],
            "reserve_bps": r["RESERVE_BPS"], "reserve_hold_days": r["RESERVE_HOLD_DAYS"],
            "payout_minimum": r["PAYOUT_MIN_CENTS"]}


def _kind_total(kind: str, currency: str = "SGD") -> int:
    total = (LedgerAccount.objects.filter(kind=kind, currency=currency)
             .aggregate(t=Sum("lines__amount"))["t"] or 0)
    return total if kind in LedgerAccount.DEBIT_NORMAL else -total


def merchant_balance(merchant) -> dict:
    b = posting.merchant_balances(merchant)
    return {"merchant_id": merchant.public_id, "merchant_name": merchant.name,
            "available": b["available"], "reserve": b["reserve"]}


def entry_to_dict(entry: JournalEntry) -> dict:
    return {
        "id": entry.public_id,
        "object": "journal_entry",
        "kind": entry.kind,
        "description": entry.description,
        "merchant_id": entry.merchant.public_id,
        "merchant_name": entry.merchant.name,
        "payment_id": entry.payment.public_id if entry.payment_id else None,
        "payment_reference": entry.payment.reference if entry.payment_id else None,
        "refund_id": entry.refund.public_id if entry.refund_id else None,
        "payout_id": entry.payout.public_id if entry.payout_id else None,
        "occurred_at": rfc3339(entry.occurred_at),
        "posted_at": rfc3339(entry.posted_at),
        "backfilled": entry.backfilled,
        "lines": [{"account": line.account.kind, "memo": line.memo, "amount": line.amount,
                   "currency": line.currency} for line in entry.lines.all()],
    }


class BalanceView(StaffAPIView):
    roles = OPERATOR_OR_ADMIN

    def get(self, request):
        if self.staff.is_operator:
            merchant = self.staff.merchant
            return Response({"object": "balance", "scope": "merchant", "currency": "SGD",
                             **merchant_balance(merchant),
                             "next_release": balances.next_release(merchant),
                             "rules": _rules()})
        merchants = (Merchant.objects.filter(ledger_accounts__isnull=False).distinct()
                     .order_by("name"))
        problems = checks.problems()
        return Response({
            "object": "balance", "scope": "gateway", "currency": "SGD",
            "network_clearing": _kind_total(Kind.NETWORK_CLEARING),
            "fee_revenue": _kind_total(Kind.FEE_REVENUE),
            "merchants_available": _kind_total(Kind.MERCHANT_AVAILABLE),
            "merchants_reserve": _kind_total(Kind.MERCHANT_RESERVE),
            "entries": JournalEntry.objects.count(),
            "check": {"ok": not problems, "problems": problems[:20]},
            "merchants": [merchant_balance(m) for m in merchants],
            "rules": _rules(),
        })


class EntryListView(StaffAPIView):
    roles = OPERATOR_OR_ADMIN

    def get(self, request):
        params = request.query_params
        entries = JournalEntry.objects.select_related("merchant", "payment", "refund", "payout")
        if self.staff.is_operator:
            entries = entries.filter(merchant_id=self.staff.merchant_id)
        elif params.get("merchant"):
            entries = entries.filter(merchant__public_id=params["merchant"][:32])
        kind = params.get("kind") or ""
        if kind and kind not in JournalEntry.Kind.values:
            raise invalid("invalid_field", "'kind' is not a known entry kind.", param="kind")
        if kind:
            entries = entries.filter(kind=kind)
        page = bounded_int(params.get("page"), "page", 1, 1, 100_000)
        size = bounded_int(params.get("page_size"), "page_size", 25, 1, 100)
        count = entries.count()
        rows = (entries.prefetch_related("lines__account")
                .order_by("-posted_at", "-id")[(page - 1) * size: page * size])
        return Response({"object": "list", "count": count, "page": page, "page_size": size,
                         "kinds": JournalEntry.Kind.values,
                         "data": [entry_to_dict(e) for e in rows]})


class PayoutListView(StaffAPIView):
    roles = OPERATOR_OR_ADMIN

    def get(self, request):
        params = request.query_params
        rows = Payout.objects.select_related("merchant")
        if self.staff.is_operator:
            rows = rows.filter(merchant_id=self.staff.merchant_id)
        page = bounded_int(params.get("page"), "page", 1, 1, 100_000)
        size = bounded_int(params.get("page_size"), "page_size", 25, 1, 100)
        count = rows.count()
        data = [payouts.to_dict(p) for p in rows[(page - 1) * size: page * size]]
        return Response({"object": "list", "count": count, "page": page, "page_size": size,
                         "data": data})

    def post(self, request):
        if not self.staff.is_operator:
            raise exceptions.PermissionDenied("Only the merchant can pay out.",
                                              code="role_not_allowed")
        body = body_or_empty(request)
        payout = payouts.create(self.staff.merchant, body.get("amount"),
                                actor_type="staff", actor_id=self.staff.public_id,
                                request_id=getattr(request, "request_id", ""))
        return Response(payouts.to_dict(payout), status=status.HTTP_201_CREATED)
