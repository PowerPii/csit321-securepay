"""Refunds and reservations (FR-TXN-01 to FR-TXN-03, UC-23, API-CONTRACT.md section 3).

Rule: completed + reserved <= captured amount, checked and reserved under a row lock on the
payment, so two refunds at the same time can never together exceed it (demo story S6).

A refund holds its amount from the moment it is created: `reserved` (not sent yet),
`submitted` (sent to the simulator) and `unresolved` (no answer; reconciliation decides) all
count as reserved. `completed` moves it to refunded; `failed` releases it. A request that
breaks the rule creates no refund (422 refund_exceeds_refundable).
"""
from django.db import IntegrityError, models, transaction
from django.db.models import Q, Sum

from apps.common.ids import new_id, new_reference
from apps.payments.models import Operation, Payment


class Refund(models.Model):
    class Status(models.TextChoices):
        RESERVED = "reserved"
        SUBMITTED = "submitted"
        COMPLETED = "completed"
        FAILED = "failed"
        UNRESOLVED = "unresolved"

    class FailureReason(models.TextChoices):
        DECLINED_BY_PROCESSOR = "declined_by_processor"
        NOT_PROCESSED = "not_processed"

    HOLDING = (Status.RESERVED, Status.SUBMITTED, Status.UNRESOLVED)

    public_id = models.CharField(max_length=32, unique=True, editable=False)  # rfd_...
    reference = models.CharField(max_length=12, unique=True, editable=False)  # RF-XXXX-XXXX
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    operation = models.OneToOneField(Operation, on_delete=models.PROTECT, related_name="refund")
    amount = models.PositiveIntegerField()  # in the payment's currency
    settlement_amount = models.PositiveIntegerField(default=0)  # v2: SGD cents
    reason = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RESERVED)
    failure_reason = models.CharField(max_length=32, choices=FailureReason.choices, blank=True)
    # The merchant's Idempotency-Key, so a retry after a lost reply finds this refund.
    request_key = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="refund_amount_positive"),
            models.CheckConstraint(
                condition=Q(status="failed") | Q(failure_reason=""),
                name="refund_failure_reason_only_when_failed",
            ),
        ]

    def __str__(self):
        return f"{self.reference} {self.status} {self.amount}"

    @property
    def is_final(self) -> bool:
        return self.status in (Refund.Status.COMPLETED, Refund.Status.FAILED)

    @classmethod
    def create_for_payment(cls, payment: Payment, operation: Operation, **fields) -> "Refund":
        fields.setdefault("settlement_amount", operation.settlement_amount)
        for attempt in range(5):
            refund = cls(payment=payment, operation=operation, public_id=new_id("rfd"),
                         reference=new_reference("RF"), **fields)
            try:
                with transaction.atomic():
                    refund.save(force_insert=True)
                return refund
            except IntegrityError:
                if attempt == 4:
                    raise
        raise AssertionError("unreachable")


def refund_totals(payment: Payment) -> tuple[int, int]:
    """(refunded, reserved) for the payment, from its refunds."""
    rows = (Refund.objects.filter(payment=payment)
            .values("status").annotate(total=Sum("amount")))
    refunded = reserved = 0
    for row in rows:
        if row["status"] == Refund.Status.COMPLETED:
            refunded += row["total"]
        elif row["status"] in Refund.HOLDING:
            reserved += row["total"]
    return refunded, reserved
