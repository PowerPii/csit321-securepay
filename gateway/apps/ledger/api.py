"""Merchant API: balance and payouts (API-CONTRACT.md section 11, v2 ledger).

* GET  /v1/balance            available, reserve and the next reserve release
* GET  /v1/payouts?limit=   the newest `limit` (default 10, at most 100)
* POST /v1/payouts {"amount": cents}   needs an Idempotency-Key; 201 with the Payout
* GET  /v1/payouts/{payout_id}

Another merchant's payout answers 404, exactly like one that does not exist.
"""
from rest_framework import status
from rest_framework.response import Response

from apps.audit.models import AuditEvent
from apps.common.errors import not_found
from apps.internal.query_params import bounded_int
from apps.ledger import balances, payouts
from apps.ledger.models import Payout
from apps.merchants.api import MerchantAPIView
from apps.payments.idempotency import run_idempotent


class BalanceView(MerchantAPIView):
    def get(self, request):
        return Response(balances.balance_to_dict(request.user))


class PayoutListView(MerchantAPIView):
    def get(self, request):
        params = request.query_params
        limit = bounded_int(params.get("limit"), "limit", 10, 1, 100)
        rows = Payout.objects.filter(merchant=request.user).select_related("merchant")
        page = list(rows.order_by("-created_at", "-id")[:limit + 1])
        return Response({"object": "list", "has_more": len(page) > limit,
                         "data": [payouts.to_dict(p) for p in page[:limit]]})

    def post(self, request):
        merchant, api_key = request.user, request.auth

        def run(body):
            payout = payouts.create(merchant, body.get("amount"),
                                    actor_type=AuditEvent.ActorType.MERCHANT_API,
                                    actor_id=api_key.public_id, request_id=request.request_id)
            return Response(payouts.to_dict(payout), status=status.HTTP_201_CREATED)

        return run_idempotent(request, run)


class PayoutDetailView(MerchantAPIView):
    def get(self, request, payout_id: str):
        payout = (Payout.objects.select_related("merchant")
                  .filter(public_id=payout_id[:32], merchant=request.user).first())
        if payout is None:
            raise not_found()
        return Response(payouts.to_dict(payout))
