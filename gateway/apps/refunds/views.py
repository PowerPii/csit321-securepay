"""Merchant refund endpoints (API-CONTRACT.md section 3, UC-23, UC-55, FR-TXN-14)."""
from rest_framework import status
from rest_framework.response import Response

from apps.audit.models import AuditEvent
from apps.common.errors import not_found
from apps.merchants.api import MerchantAPIView
from apps.payments.idempotency import run_idempotent
from apps.payments.models import Payment
from apps.payments.settlement import Actor
from apps.refunds import service
from apps.refunds.models import Refund
from apps.refunds.serializers import refund_to_dict


class PaymentRefundsView(MerchantAPIView):
    def post(self, request, payment_id: str):
        """201 with the Refund: completed or failed if the simulator answered, unresolved
        (amount still reserved) if it did not. Runs non-atomically so the reservation and
        the operation are committed before the simulator is called."""
        merchant, api_key = request.user, request.auth
        key = request.headers.get("Idempotency-Key", "")

        def run(body):
            actor = Actor(AuditEvent.ActorType.MERCHANT_API, api_key.public_id,
                          request.request_id)
            refund, created = service.start(payment_id, merchant, body, actor, request_key=key,
                                            livemode=api_key.livemode)
            refund = service.submit(refund, created, request.request_id)
            return Response(refund_to_dict(refund), status=status.HTTP_201_CREATED)

        return run_idempotent(request, run, atomic=False)

    def get(self, request, payment_id: str):
        payment = Payment.objects.filter(public_id=payment_id, merchant=request.user,
                                         livemode=request.auth.livemode).first()
        if payment is None:
            raise not_found()
        refunds = Refund.objects.filter(payment=payment).select_related("payment")
        return Response({
            "object": "list",
            "payment_id": payment.public_id,
            "refundable_amount": service.refundable_amount(payment),
            "data": [refund_to_dict(r) for r in refunds],
        })


class RefundDetailView(MerchantAPIView):
    def get(self, request, refund_id: str):
        refund = (Refund.objects.select_related("payment")
                  .filter(public_id=refund_id, payment__merchant=request.user,
                          payment__livemode=request.auth.livemode).first())
        if refund is None:
            raise not_found()
        return Response(refund_to_dict(refund))
