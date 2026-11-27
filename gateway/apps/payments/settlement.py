"""Capture and cancel of an authorised payment (API-CONTRACT.md section 3, UC-24, FR-TXN-04/05).

Three steps, each with its own transaction, so the operation row is committed before the
simulator is called (SIMULATOR-CONTRACT.md section 3):

    1. start():  lock the payment, check the rules, insert the Operation.
    2. the call: processor.capture / processor.cancel with the authorisation's operation ID.
    3. apply_settlement_answer(): settle the operation and the payment from a signed answer.

The merchant API, the 7-day auto-cancel job and (sprint 6) the dashboard all go through
`settle()`. Reconciliation (sprint 5) reuses `apply_settlement_answer` with status-query
answers, so every path settles a payment the same way.
"""
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.models import record as audit
from apps.common.errors import ApiError, invalid, not_found
from apps.events.models import EventType
from apps.ledger.posting import post_capture
from apps.payments import processor
from apps.payments.models import Operation, OutcomeReason, Payment
from apps.payments.processing import _move
from apps.payments.processor import Answer, AnswerKind
from apps.payments.states import PaymentStatus

logger = logging.getLogger(__name__)

SETTLEMENT_TYPES = (Operation.Type.CAPTURE, Operation.Type.CANCEL)


@dataclass(frozen=True)
class Actor:
    """Who asked, for the audit log: merchant_api + key ID, system + job name, staff + user."""

    type: str
    id: str
    request_id: str = ""


def _authorisation_expired(payment: Payment) -> bool:
    days = settings.GATEWAY_RULES["AUTH_EXPIRY_DAYS"]
    return (payment.authorised_at is not None
            and timezone.now() >= payment.authorised_at + timedelta(days=days))


def _check_can_settle(payment: Payment, allow_expired: bool = False) -> None:
    """The 409s of API-CONTRACT.md section 3 (FR-TXN-05)."""
    status = PaymentStatus(payment.status)
    if status == PaymentStatus.CAPTURED:
        raise ApiError(409, "payment_already_captured", "This payment was already captured.")
    if status == PaymentStatus.CANCELLED:
        if payment.outcome_reason == OutcomeReason.AUTHORISATION_EXPIRED:
            raise ApiError(409, "authorisation_expired",
                           "The authorisation expired and was cancelled.")
        raise ApiError(409, "payment_already_cancelled", "This payment was already cancelled.")
    if status != PaymentStatus.AUTHORISED:
        raise ApiError(409, "payment_not_authorised",
                       f"Only an authorised payment can be captured or cancelled; this one is "
                       f"{status}.")
    if not allow_expired and _authorisation_expired(payment):
        raise ApiError(409, "authorisation_expired",
                       "The authorisation is older than "
                       f"{settings.GATEWAY_RULES['AUTH_EXPIRY_DAYS']} days and is being "
                       "cancelled.")
    if payment.operations.filter(type__in=SETTLEMENT_TYPES,
                                 status__in=Operation.OPEN_STATUSES).exists():
        raise ApiError(409, "operation_in_progress",
                       "A capture or cancel for this payment is still waiting for the "
                       "processor. Check the payment again later.", retry_after=30)


def capture_amount(body: dict, payment: Payment) -> int:
    """`{}` captures everything; `{"amount": n}` captures less, once (contract section 3)."""
    for name in body:
        if name != "amount":
            raise invalid("invalid_field", f"Unknown field '{name}'.", param=name)
    amount = body.get("amount", payment.amount)
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
        raise invalid("invalid_field", "'amount' must be a positive integer number of cents.",
                      param="amount")
    if amount > payment.amount:
        raise ApiError(422, "capture_exceeds_authorised",
                       f"The capture amount is more than the authorised {payment.amount} "
                       "cents.", param="amount")
    return amount


def start(payment_id: str, op_type: str, actor: Actor, *, merchant=None, body=None,
          request_key: str = "", reason: str = "", allow_expired: bool = False,
          livemode: bool | None = None) -> tuple[Operation, bool]:
    """Lock the payment, check the rules and commit the operation row (step 1).

    Returns the operation and whether this call created it. `allow_expired` is for the
    7-day auto-cancel job only: merchants get 409 authorisation_expired instead.

    With a `request_key` (the merchant's Idempotency-Key) a second request with the same key
    gets the operation the first one made, so a crash between the call and the stored reply
    can never send a second capture.
    """
    with transaction.atomic():
        payments = Payment.objects.select_for_update(of=("self",)).select_related("merchant")
        if merchant is not None:
            payments = payments.filter(merchant=merchant)
        if livemode is not None:  # merchant API: the key's mode only
            payments = payments.filter(livemode=livemode)
        payment = payments.filter(public_id=payment_id).first()
        if payment is None:
            raise not_found()
        if request_key:
            earlier = payment.operations.filter(type=op_type, request_key=request_key).first()
            if earlier is not None:
                return earlier, False
        _check_can_settle(payment, allow_expired)
        if op_type == Operation.Type.CAPTURE:
            amount = capture_amount(body or {}, payment)
        else:
            if body:
                name = next(iter(body))
                raise invalid("invalid_field", f"Unknown field '{name}'.", param=name)
            amount = payment.amount
            reason = reason or OutcomeReason.MERCHANT_CANCELLED
        operation = Operation.objects.create(payment=payment, type=op_type, amount=amount,
                                             request_key=request_key, reason=reason)
        audit(f"payment.{op_type}_requested", actor_type=actor.type, actor_id=actor.id,
              merchant_id=payment.merchant.public_id, target_type="payment",
              target_id=payment.public_id, request_id=actor.request_id,
              details={"operation_id": operation.public_id, "amount": amount,
                       "reason": reason or None})
    return operation, True


def call(operation: Operation) -> Answer:
    """Send the operation to the simulator (step 2). Never inside a transaction."""
    authorisation = Operation.objects.get(payment_id=operation.payment_id,
                                          type=Operation.Type.AUTHORISE)
    if operation.type == Operation.Type.CAPTURE:
        return processor.capture(operation, authorisation.public_id)
    return processor.cancel(operation, authorisation.public_id)


def apply_settlement_answer(operation_id: int, answer: Answer, request_id: str = "") -> Payment:
    """Settle a capture or cancel from the simulator's answer (step 3, contract section 7)."""
    with transaction.atomic():
        operation = Operation.objects.select_for_update().get(pk=operation_id)
        payment = (Payment.objects.select_for_update(of=("self",)).select_related("merchant")
                   .get(pk=operation.payment_id))
        if operation.status not in Operation.OPEN_STATUSES:
            return payment  # already settled; a late or repeated answer changes nothing

        record = answer.record
        if answer.kind is AnswerKind.CONFLICT and answer.conflict_code == "operation_retired":
            logger.error("Simulator says %s was retired", operation.public_id)
        elif answer.kind is AnswerKind.CONFLICT:
            logger.error("Simulator reports %s for %s", answer.conflict_code,
                         operation.public_id)
            record = None

        now = timezone.now()
        kind = operation.type
        if record is None:
            # The payment keeps its status; pending_operation shows the unresolved call.
            operation.mark_unresolved()
            action = f"payment.{kind}_unresolved"
            details = {"problem": answer.problem or answer.conflict_code}
        else:
            operation.simulator_reference = record.get("simulator_reference") or ""
            operation.evidence_body = answer.raw_body
            operation.evidence_signature = answer.signature
            operation.completed_at = now
            status = record.get("status")
            if status == "approved" and kind == Operation.Type.CAPTURE:
                operation.status = Operation.Status.SUCCEEDED
                payment.captured_amount = operation.amount
                payment.captured_at = now
                _move(payment, PaymentStatus.CAPTURED, EventType.PAYMENT_CAPTURED)
                post_capture(payment)  # v2 ledger, same transaction as the outbox row
                action = "payment.captured"
            elif status == "approved":
                operation.status = Operation.Status.SUCCEEDED
                payment.cancelled_at = now
                payment.outcome_reason = operation.reason or OutcomeReason.MERCHANT_CANCELLED
                _move(payment, PaymentStatus.CANCELLED, EventType.PAYMENT_CANCELLED)
                action = "payment.cancelled"
            elif status == "declined":
                # The gateway checked the same rules first, so the two records disagree,
                # unless the card network could not be reached (v2, NETWORK-CONTRACT.md 7):
                # then nothing changed and the merchant can simply try again.
                operation.status = Operation.Status.FAILED
                if record.get("decline_reason") == "issuer_unavailable":
                    logger.warning("Card network unavailable for %s %s", kind,
                                   operation.public_id)
                else:
                    logger.error("Simulator declined %s %s (%s): records disagree", kind,
                                 operation.public_id, record.get("decline_reason"))
                action = f"payment.{kind}_rejected_by_processor"
            else:  # no_record (status query only): retired; the merchant may try again
                operation.status = Operation.Status.NOT_PROCESSED
                action = f"payment.{kind}_not_processed"
            operation.save()
            details = {"simulator_reference": operation.simulator_reference or None,
                       "simulator_status": status,
                       "decline_reason": record.get("decline_reason")}

        audit(action, actor_type="system", actor_id="processor",
              merchant_id=payment.merchant.public_id, target_type="payment",
              target_id=payment.public_id, request_id=request_id,
              details={"operation_id": operation.public_id, "amount": operation.amount,
                       **details})
        return payment


def settle(operation: Operation, created: bool, request_id: str = "") -> tuple[Payment, Operation]:
    """Call the simulator for an operation start() just created and apply the answer.

    An operation found again through its request key (`created` False) is not sent a second
    time; its current state is returned, and reconciliation owns anything still open.
    """
    if created:
        payment = apply_settlement_answer(operation.pk, call(operation), request_id)
    else:
        payment = Payment.objects.select_related("merchant").get(pk=operation.payment_id)
    operation.refresh_from_db()
    return payment, operation
