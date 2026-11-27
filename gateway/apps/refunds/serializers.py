"""The Refund object as merchants see it (contracts/schemas/refund.schema.json)."""
from apps.common.timefmt import rfc3339


def refund_to_dict(refund) -> dict:
    payment = refund.payment
    return {
        "id": refund.public_id,
        "object": "refund",
        "livemode": payment.livemode,
        "reference": refund.reference,
        "payment_id": payment.public_id,
        "payment_reference": payment.reference,
        "amount": refund.amount,
        "currency": payment.currency,
        "status": refund.status,
        "reason": refund.reason or None,
        "failure_reason": refund.failure_reason or None,
        "created_at": rfc3339(refund.created_at),
        "updated_at": rfc3339(refund.updated_at),
        "completed_at": rfc3339(refund.completed_at),
    }
