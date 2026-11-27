"""The Payment object as merchants see it (contracts/schemas/payment.schema.json).

Used for API replies and webhook snapshots, so both always agree.
"""
from apps.common.timefmt import rfc3339
from apps.payments.models import Operation, OutcomeReason, Payment
from apps.payments.states import PaymentStatus

# Plain-language explanation with the next step (FR-MER-07). Safe for staff, not customers.
OUTCOME_MESSAGES = {
    OutcomeReason.FRAUD_RULE: "Blocked by a fraud rule. Do not retry; ask the customer to "
                              "contact you if they think this is wrong.",
    OutcomeReason.FRAUD_MODEL: "Blocked by fraud screening. Do not retry; ask the customer to "
                               "contact you if they think this is wrong.",
    OutcomeReason.VERIFICATION_FAILED: "The customer entered the wrong verification code too "
                                       "many times. They can start a new payment.",
    OutcomeReason.VERIFICATION_EXPIRED: "The checkout session ended before a correct "
                                        "verification code was entered. The customer can "
                                        "start a new payment.",
    OutcomeReason.SCREENING_UNAVAILABLE: "Screening was unavailable, so the attempt was "
                                         "stopped. This is not a fraud finding; try again "
                                         "later.",
    OutcomeReason.AMOUNT_LIMIT: "The amount is outside the allowed limits.",
    OutcomeReason.CARD_DECLINED: "The card issuer declined the payment. The customer can try "
                                 "another card.",
    OutcomeReason.NOT_PROCESSED: "The processor has no record of this payment, so nothing was "
                                 "charged. The customer can pay again.",
    OutcomeReason.CHECKOUT_EXPIRED: "The customer did not pay within the checkout time. Create "
                                    "a new payment if they still want to buy.",
    OutcomeReason.MERCHANT_CANCELLED: "You cancelled this authorisation. Nothing was charged.",
    OutcomeReason.AUTHORISATION_EXPIRED: "The authorisation was not captured in time and was "
                                         "cancelled. Nothing was charged.",
}


def _pending_operation(payment: Payment):
    op = (
        payment.operations.filter(
            type__in=[Operation.Type.CAPTURE, Operation.Type.CANCEL],
            status=Operation.Status.UNRESOLVED,
        )
        .order_by("-created_at")
        .first()
    )
    if op is None:
        return None
    return {"type": op.type, "status": "unresolved", "requested_at": rfc3339(op.created_at)}


def payment_to_dict(payment: Payment) -> dict:
    from apps.refunds.models import refund_totals

    refunded, reserved = refund_totals(payment) if payment.captured_amount else (0, 0)
    refundable = (
        payment.captured_amount - refunded - reserved
        if payment.status == PaymentStatus.CAPTURED else 0
    )
    card = None
    if payment.card_token:
        card = {"token": payment.card_token, "brand": payment.card_brand or "unknown",
                "last4": payment.card_last4}
    outcome = None
    if payment.outcome_reason:
        reason = OutcomeReason(payment.outcome_reason)
        outcome = {"reason": reason.value, "message": OUTCOME_MESSAGES[reason]}
    return {
        "id": payment.public_id,
        "object": "payment",
        "livemode": payment.livemode,
        "reference": payment.reference,
        "order_reference": payment.order_reference,
        "status": payment.status,
        "amount": payment.amount,
        "currency": payment.currency,
        "line_items": payment.line_items or None,
        "captured_amount": payment.captured_amount,
        "refundable_amount": refundable,
        "refunded_amount": refunded,
        "refund_reserved_amount": reserved,
        "card": card,
        "screening": ({"decision": payment.screening_decision}
                      if payment.screening_decision else None),
        "outcome": outcome,
        "pending_operation": _pending_operation(payment),
        "sequence": payment.sequence,
        "return_url": payment.return_url,
        "checkout_url": payment.checkout_url,
        "checkout_expires_at": rfc3339(payment.checkout_expires_at),
        "authorisation_expires_at": rfc3339(payment.authorisation_expires_at),
        "created_at": rfc3339(payment.created_at),
        "updated_at": rfc3339(payment.updated_at),
        "authorised_at": rfc3339(payment.authorised_at),
        "captured_at": rfc3339(payment.captured_at),
        "cancelled_at": rfc3339(payment.cancelled_at),
    }
