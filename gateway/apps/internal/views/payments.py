"""D3 payments list, D4 payment detail, D5 refund, capture / cancel and operation re-check.

Merchant operators act on their own merchant's payments. Analysts and administrators see
every merchant read-only (FR-ADM-06), and each detail view they open is audited. Capture,
cancel and refund go through the same service functions as the merchant API, so the rules,
row locks, outbox events and audit entries are identical (actor type "staff").
"""
import re
from datetime import datetime, time, timedelta

from django.db.models import IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from apps.accounts.models import StaffAccount
from apps.audit.models import AuditEvent
from apps.audit.models import record as audit
from apps.common.errors import ApiError, invalid, not_found
from apps.common.views import body_or_empty
from apps.internal.access import ADMIN, OPERATOR, StaffAPIView, get_payment, visible_payments
from apps.internal.payment_details import operation_to_dict, payment_detail
from apps.internal.query_params import bounded_int, local_zone, parse_date
from apps.internal.serializers import payment_row
from apps.merchants.models import parse_mode
from apps.payments import reconciliation, settlement
from apps.payments.models import Operation
from apps.payments.states import PaymentStatus
from apps.refunds import service as refunds
from apps.refunds.models import Refund
from apps.refunds.serializers import refund_to_dict

MAX_PAGE_SIZE = 100
DECISIONS = {"allow", "verify", "verify_passed", "verify_failed", "block", "not_screened"}


def _completed_refunds():
    return Coalesce(Subquery(
        Refund.objects.filter(payment=OuterRef("pk"), status=Refund.Status.COMPLETED)
        .values("payment").annotate(total=Sum("amount")).values("total")[:1],
        output_field=IntegerField()), 0)


def _escalated_ops():
    return Operation.objects.filter(status=Operation.Status.UNRESOLVED,
                                    escalated_at__isnull=False)


def mode_filter(params) -> bool | None:
    """`mode=test|live` keeps one mode (v2); no `mode` keeps both (fraud work spans them)."""
    value = params.get("mode") or ""
    if not value:
        return None
    return parse_mode(value)


def filter_payments(payments, params, staff: StaffAccount):
    livemode = mode_filter(params)
    if livemode is not None:
        payments = payments.filter(livemode=livemode)
    statuses = [s for s in (params.get("status") or "").split(",") if s]
    for value in statuses:
        if value not in {s.value for s in PaymentStatus}:
            raise invalid("invalid_field", f"Unknown status '{value}'.", param="status")
    if statuses:
        payments = payments.filter(status__in=statuses)

    decision = params.get("decision") or ""
    if decision:
        if decision not in DECISIONS:
            raise invalid("invalid_field", f"Unknown risk decision '{decision}'.",
                          param="decision")
        if decision == "verify_passed":
            payments = payments.filter(
                screening_decision="verify",
                status__in=[PaymentStatus.PROCESSING, PaymentStatus.AUTHORISED,
                            PaymentStatus.CAPTURED, PaymentStatus.DECLINED,
                            PaymentStatus.UNRESOLVED, PaymentStatus.CANCELLED])
        elif decision == "verify_failed":
            payments = payments.filter(screening_decision="verify",
                                       status=PaymentStatus.BLOCKED)
        else:
            payments = payments.filter(screening_decision=decision)

    zone = local_zone()
    if params.get("from"):
        start = datetime.combine(parse_date(params["from"], "from"), time.min, zone)
        payments = payments.filter(created_at__gte=start)
    if params.get("to"):
        end = datetime.combine(parse_date(params["to"], "to") + timedelta(days=1), time.min, zone)
        payments = payments.filter(created_at__lt=end)

    query = (params.get("q") or "").strip()[:254]
    if query:
        match = (Q(reference__iexact=query) | Q(public_id=query)
                 | Q(order_reference__icontains=query))
        # The reference on the customer's receipt finds the payment however it is typed
        # ("sp 7kq4 m2xd"), and a refund receipt's RF-... reference finds its payment:
        # one public reference from receipt to dashboard (completion audit, 19 Nov 2026).
        reference = public_reference(query)
        if reference and reference.startswith("SP-"):
            match |= Q(reference=reference)
        elif reference:
            match |= Q(pk__in=Refund.objects.filter(reference=reference).values("payment_id"))
        if "@" in query:
            match |= Q(customer_email__iexact=query)
        payments = payments.filter(match)

    merchant = params.get("merchant") or ""
    if merchant and not staff.is_operator:
        payments = payments.filter(merchant__public_id=merchant)

    if params.get("attention") == "escalated":
        payments = payments.filter(pk__in=_escalated_ops().values("payment_id"))
    return payments


_REFERENCE_NOISE = re.compile(r"[\s\-_.]+")
_REFERENCE = re.compile(r"(SP|RF)([0-9A-Z]{4})([0-9A-Z]{4})")


def public_reference(raw: str) -> str | None:
    """'sp 7kq4 m2xd' -> 'SP-7KQ4-M2XD', 'rf-abcd-2345' -> 'RF-ABCD-2345'; else None."""
    match = _REFERENCE.fullmatch(_REFERENCE_NOISE.sub("", raw).upper())
    return f"{match[1]}-{match[2]}-{match[3]}" if match else None


def summary(staff: StaffAccount, livemode: bool | None = None) -> dict:
    """The tiles above the D3 table, for what this staff member can see (in one mode)."""
    payments = visible_payments(staff)
    if livemode is not None:
        payments = payments.filter(livemode=livemode)
    zone = local_zone()
    now = timezone.now().astimezone(zone)
    today = datetime.combine(now.date(), time.min, zone)
    month = datetime.combine(now.date().replace(day=1), time.min, zone)
    refunds_qs = Refund.objects.filter(status=Refund.Status.COMPLETED, completed_at__gte=month)
    if staff.is_operator:
        refunds_qs = refunds_qs.filter(payment__merchant_id=staff.merchant_id)
    if livemode is not None:
        refunds_qs = refunds_qs.filter(payment__livemode=livemode)
    unresolved_ops = Operation.objects.filter(status=Operation.Status.UNRESOLVED,
                                              payment__in=payments)
    return {
        "awaiting_capture": payments.filter(status=PaymentStatus.AUTHORISED).count(),
        "unresolved": payments.filter(Q(status=PaymentStatus.UNRESOLVED)
                                      | Q(pk__in=unresolved_ops.values("payment_id")))
        .distinct().count(),
        "blocked_today": payments.filter(status=PaymentStatus.BLOCKED,
                                         created_at__gte=today).count(),
        "refunded_this_month": refunds_qs.aggregate(total=Sum("amount"))["total"] or 0,
        "escalated": (_escalated_ops().filter(payment__in=payments).count()
                      if not staff.is_operator else None),
    }


class PaymentListView(StaffAPIView):
    """GET ?mode=&status=&decision=&from=&to=&q=&merchant=&attention=escalated&page=&page_size="""

    def get(self, request):
        params = request.query_params
        payments = filter_payments(visible_payments(self.staff), params, self.staff)
        page = bounded_int(params.get("page"), "page", 1, 1, 100_000)
        size = bounded_int(params.get("page_size"), "page_size", 25, 1, MAX_PAGE_SIZE)
        count = payments.count()
        rows = (payments.annotate(refunded=_completed_refunds())
                .order_by("-created_at", "-id")[(page - 1) * size: page * size])
        return Response({
            "object": "list",
            "count": count,
            "page": page,
            "page_size": size,
            "data": [payment_row(p, p.refunded) for p in rows],
            "summary": summary(self.staff, mode_filter(params)),
        })


class PaymentDetailView(StaffAPIView):
    def get(self, request, payment_id: str):
        payment = get_payment(self.staff, payment_id)
        if not self.staff.is_operator:
            # FR-ADM-06: each cross-merchant view by an administrator or analyst is logged.
            audit("payment.viewed", actor_type=AuditEvent.ActorType.STAFF,
                  actor_id=self.staff.public_id, merchant_id=payment.merchant.public_id,
                  target_type="payment", target_id=payment.public_id,
                  request_id=request.request_id)
        return Response(payment_detail(self.staff, payment))


class _SettleView(StaffAPIView):
    """Capture or cancel from D4. 200 when the simulator confirmed it; 202 when it did not
    answer (the operation is unresolved and reconciliation finishes it)."""

    roles = OPERATOR
    op_type = ""

    def post(self, request, payment_id: str):
        payment = get_payment(self.staff, payment_id)
        body = body_or_empty(request)
        operation, created = settlement.start(payment.public_id, self.op_type, self.actor(),
                                              merchant=self.staff.merchant, body=dict(body))
        payment, operation = settlement.settle(operation, created, request.request_id)
        if operation.status == Operation.Status.NOT_PROCESSED:
            raise ApiError(409, "operation_not_processed",
                           "The processor has no record of this request, so nothing changed. "
                           "Try again.")
        if operation.status == Operation.Status.FAILED:
            raise ApiError(409, "processor_rejected",
                           f"The processor refused the {self.op_type}. Nothing changed; an "
                           "administrator has been told.")
        code = (status.HTTP_200_OK if operation.status == Operation.Status.SUCCEEDED
                else status.HTTP_202_ACCEPTED)
        return Response(payment_detail(self.staff, payment), status=code)


class CaptureView(_SettleView):
    op_type = Operation.Type.CAPTURE


class CancelView(_SettleView):
    op_type = Operation.Type.CANCEL


class RefundView(StaffAPIView):
    """D5. POST {amount, reason}: 201 with the refund (completed, failed or unresolved).
    GET: the payment's refunds and the refundable amount."""

    roles = OPERATOR

    def get(self, request, payment_id: str):
        payment = get_payment(self.staff, payment_id)
        return Response({
            "object": "list",
            "payment_id": payment.public_id,
            "refundable_amount": refunds.refundable_amount(payment),
            "data": [refund_to_dict(r) for r in
                     Refund.objects.filter(payment=payment).select_related("payment")],
        })

    def post(self, request, payment_id: str):
        payment = get_payment(self.staff, payment_id)
        body = body_or_empty(request)
        refund, created = refunds.start(payment.public_id, self.staff.merchant, dict(body),
                                        self.actor())
        refund = refunds.submit(refund, created, request.request_id)
        return Response(refund_to_dict(refund), status=status.HTTP_201_CREATED)


class OperationRecheckView(StaffAPIView):
    """FR-TXN-13 / UC-42: an administrator asks the simulator again about an unresolved
    operation, by its original ID. Only a signed answer can settle it; nobody sets an
    outcome by hand."""

    roles = ADMIN

    def post(self, request, operation_id: str):
        operation = Operation.objects.filter(public_id=operation_id).first()
        if operation is None:
            raise not_found()
        if operation.status != Operation.Status.UNRESOLVED:
            raise ApiError(409, "operation_not_unresolved",
                           f"This operation is already {operation.status}.")
        result = reconciliation.check(
            operation.pk, actor=(AuditEvent.ActorType.STAFF, self.staff.public_id),
            request_id=request.request_id, manual=True)
        return Response({"operation": operation_to_dict(result),
                         "settled": result.status != Operation.Status.UNRESOLVED})
