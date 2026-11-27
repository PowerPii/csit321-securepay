"""Creating refunds and applying the simulator's answers (UC-23, SIMULATOR-CONTRACT.md 7).

Same three steps as capture and cancel (apps/payments/settlement.py):

    1. start():  lock the payment, check completed + reserved + amount <= captured, insert
                 the Operation and the Refund (status reserved) in one transaction.
    2. submit(): mark it submitted and call POST /sim/refund outside any transaction.
    3. apply_refund_answer(): completed, failed or unresolved from the answer. Reconciliation
                 calls the same function with status-query answers.
"""
import logging

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.audit.models import record as audit
from apps.common.errors import ApiError, invalid, not_found
from apps.events.models import EventType
from apps.events.outbox import record_refund_event
from apps.fx.currencies import format_amount
from apps.guest.receipts import queue_refund_receipt
from apps.ledger.posting import post_refund
from apps.payments import processor
from apps.payments.models import Operation, Payment
from apps.payments.processor import Answer, AnswerKind
from apps.payments.settlement import Actor
from apps.payments.states import PaymentStatus
from apps.refunds.models import Refund, refund_totals

logger = logging.getLogger(__name__)

REFUND_FIELDS = {"amount", "reason"}


def validate(body: dict) -> tuple[int, str]:
    """CreateRefundRequest: `amount` (cents) and an optional `reason` of at most 200 chars."""
    for name in body:
        if name not in REFUND_FIELDS:
            raise invalid("invalid_field", f"Unknown field '{name}'.", param=name)
    if "amount" not in body or body["amount"] is None:
        raise invalid("missing_field", "'amount' is required.", param="amount")
    amount = body["amount"]
    if (not isinstance(amount, int) or isinstance(amount, bool)
            or not 1 <= amount <= 2_000_000_000):
        raise invalid("invalid_field", "'amount' must be a positive integer in the payment "
                      "currency's smallest unit.", param="amount")
    reason = body.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 200
                               or any(ord(c) < 32 and c not in "\t" for c in reason)):
        raise invalid("invalid_field", "'reason' must be text of at most 200 characters.",
                      param="reason")
    return amount, (reason or "").strip()


def refundable_amount(payment: Payment) -> int:
    if payment.status != PaymentStatus.CAPTURED:
        return 0
    refunded, reserved = refund_totals(payment)
    return payment.captured_amount - refunded - reserved


def start(payment_id: str, merchant, body: dict, actor: Actor, request_key: str = "",
          livemode: bool | None = None) -> tuple[Refund, bool]:
    """Check and reserve under the payment's row lock (FR-TXN-02, FR-TXN-03).

    Returns the refund and whether this call created it. A second request with the same
    Idempotency-Key gets the refund the first one made, so a crash between the simulator
    call and the stored reply can never send a second refund.
    """
    amount, reason = validate(body)
    rejection = None
    with transaction.atomic():
        payments = (Payment.objects.select_for_update(of=("self",)).select_related("merchant")
                    .filter(public_id=payment_id, merchant=merchant))
        if livemode is not None:  # merchant API: the key's mode only
            payments = payments.filter(livemode=livemode)
        payment = payments.first()
        if payment is None:
            raise not_found()
        if request_key:
            earlier = (Refund.objects.select_related("payment", "operation")
                       .filter(payment=payment, request_key=request_key).first())
            if earlier is not None:
                return earlier, False
        if payment.status != PaymentStatus.CAPTURED:
            raise ApiError(409, "payment_not_captured",
                           f"Only a captured payment can be refunded; this one is "
                           f"{payment.status}.")
        # SIMULATOR-CONTRACT.md section 3 rule 5: nothing new while an operation is unresolved.
        if payment.operations.filter(status=Operation.Status.UNRESOLVED).exists():
            raise ApiError(409, "operation_in_progress",
                           "An earlier refund of this payment is still waiting for the "
                           "processor. Check the payment again later.", retry_after=30)
        refundable = refundable_amount(payment)
        if amount > refundable:
            rejection = ApiError(422, "refund_exceeds_refundable",
                                 "The refund is larger than the remaining refundable amount "
                                 f"({format_amount(refundable, payment.currency)}).",
                                 param="amount")
        else:
            # v2: SGD of this refund = SGD of everything refunded after it, less before it,
            # so the SGD refunds of a payment never add up to more than its SGD capture.
            before = payment.captured_amount - refundable
            sgd = payment.to_sgd(before + amount) - payment.to_sgd(before)
            operation = Operation.objects.create(payment=payment, type=Operation.Type.REFUND,
                                                 amount=amount, settlement_amount=sgd,
                                                 request_key=request_key)
            refund = Refund.create_for_payment(payment, operation, amount=amount,
                                               settlement_amount=sgd, reason=reason,
                                               request_key=request_key)
            audit("refund.requested", actor_type=actor.type, actor_id=actor.id,
                  merchant_id=payment.merchant.public_id, target_type="refund",
                  target_id=refund.public_id, request_id=actor.request_id,
                  details={"payment_id": payment.public_id, "operation_id":
                           operation.public_id, "amount": amount,
                           "refundable_before": refundable})
    if rejection is not None:
        # Kept outside the rolled-back work: the dashboard shows rejected requests (FR-TXN-14).
        audit("refund.rejected", actor_type=actor.type, actor_id=actor.id,
              merchant_id=merchant.public_id, target_type="payment", target_id=payment_id,
              request_id=actor.request_id,
              details={"amount": amount, "refundable": refundable,
                       "code": rejection.code})
        raise rejection
    return refund, True


def submit(refund: Refund, created: bool, request_id: str = "") -> Refund:
    """Send a refund start() just created; a refund found again is never sent twice."""
    if created:
        Refund.objects.filter(pk=refund.pk, status=Refund.Status.RESERVED).update(
            status=Refund.Status.SUBMITTED, updated_at=timezone.now())
        authorisation = Operation.objects.get(payment_id=refund.payment_id,
                                              type=Operation.Type.AUTHORISE)
        answer = processor.refund(refund.operation, authorisation.public_id, refund.public_id)
        apply_refund_answer(refund.operation_id, answer, request_id)
    return Refund.objects.select_related("payment", "operation").get(pk=refund.pk)


def apply_refund_answer(operation_id: int, answer: Answer, request_id: str = "") -> Refund:
    """Settle a refund from a simulator answer (SIMULATOR-CONTRACT.md section 7, refund column).

    A refund that is already completed or failed never changes (a late answer is ignored).
    """
    with transaction.atomic():
        operation = Operation.objects.select_for_update().get(pk=operation_id)
        payment = (Payment.objects.select_for_update(of=("self",)).select_related("merchant")
                   .get(pk=operation.payment_id))
        refund = Refund.objects.select_for_update().get(operation=operation)
        refund.payment = payment
        if operation.status not in Operation.OPEN_STATUSES:
            return refund

        record = answer.record
        if answer.kind is AnswerKind.CONFLICT and answer.conflict_code == "operation_retired":
            logger.error("Simulator says %s was retired", operation.public_id)
        elif answer.kind is AnswerKind.CONFLICT:
            logger.error("Simulator reports %s for %s", answer.conflict_code,
                         operation.public_id)
            record = None

        now = timezone.now()
        if record is None:
            operation.mark_unresolved()
            event = None
            if refund.status != Refund.Status.UNRESOLVED:
                refund.status = Refund.Status.UNRESOLVED
                refund.save()
                event = EventType.REFUND_UNRESOLVED
            action = "refund.unresolved"
            details = {"problem": answer.problem or answer.conflict_code}
        else:
            operation.simulator_reference = record.get("simulator_reference") or ""
            operation.evidence_body = answer.raw_body
            operation.evidence_signature = answer.signature
            operation.completed_at = now
            status = record.get("status")
            if status == "approved":
                operation.status = Operation.Status.SUCCEEDED
                refund.status = Refund.Status.COMPLETED
                refund.completed_at = now
                event, action = EventType.REFUND_COMPLETED, "refund.completed"
                queue_refund_receipt(refund)
                post_refund(refund)  # v2 ledger, same transaction as the outbox row
            elif status == "declined":
                # The gateway checked the same totals first, so the two records disagree.
                operation.status = Operation.Status.FAILED
                refund.status = Refund.Status.FAILED
                refund.failure_reason = Refund.FailureReason.DECLINED_BY_PROCESSOR
                if record.get("decline_reason") == "issuer_unavailable":  # v2 network down
                    logger.warning("Card network unavailable for refund %s",
                                   operation.public_id)
                else:
                    logger.error("Simulator declined refund %s (%s): records disagree",
                                 operation.public_id, record.get("decline_reason"))
                event, action = EventType.REFUND_FAILED, "refund.failed"
            else:  # no_record: never received; retired, the reservation is released
                operation.status = Operation.Status.NOT_PROCESSED
                refund.status = Refund.Status.FAILED
                refund.failure_reason = Refund.FailureReason.NOT_PROCESSED
                event, action = EventType.REFUND_FAILED, "refund.failed"
            operation.save()
            refund.save()
            details = {"simulator_reference": operation.simulator_reference or None,
                       "simulator_status": status,
                       "decline_reason": record.get("decline_reason")}
        if event is not None:
            record_refund_event(refund, event)
        audit(action, actor_type=AuditEvent.ActorType.SYSTEM, actor_id="processor",
              merchant_id=payment.merchant.public_id, target_type="refund",
              target_id=refund.public_id, request_id=request_id,
              details={"payment_id": payment.public_id, "operation_id": operation.public_id,
                       "amount": refund.amount, **details})
        return refund
