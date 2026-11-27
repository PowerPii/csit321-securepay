"""The simulator's authoritative fictional processing record (SIMULATOR-CONTRACT.md 3, 4, 6).

`OperationRecord` holds at most one row per gateway `operation_id` (unique key). A status
query for an unknown ID inserts a `no_record` row under that same key, which retires the
ID: whichever insert commits first wins, so there is never an in-between answer.
Records never change once written. `Authorisation` carries the running totals that
capture, cancel and refund (later sprints) act on.
"""
from django.db import models
from django.db.models import Q


class Authorisation(models.Model):
    class State(models.TextChoices):
        OPEN = "open"
        CAPTURED = "captured"
        CANCELLED = "cancelled"

    operation_id = models.CharField(max_length=32, unique=True)  # the authorise operation
    merchant_id = models.CharField(max_length=32)
    payment_id = models.CharField(max_length=32)
    state = models.CharField(max_length=16, choices=State.choices, default=State.OPEN)
    authorised_amount = models.PositiveIntegerField()
    captured_amount = models.PositiveIntegerField(default=0)
    refunded_amount = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(captured_amount__lte=models.F("authorised_amount")),
                                   name="sim_captured_within_authorised"),
            models.CheckConstraint(condition=Q(refunded_amount__lte=models.F("captured_amount")),
                                   name="sim_refunded_within_captured"),
        ]

    def __str__(self):
        return f"{self.operation_id} {self.state} {self.authorised_amount}"

    def to_dict(self) -> dict:
        return {"state": self.state, "authorised_amount": self.authorised_amount,
                "captured_amount": self.captured_amount, "refunded_amount": self.refunded_amount}


class OperationRecord(models.Model):
    class Type(models.TextChoices):
        AUTHORISE = "authorise"
        CAPTURE = "capture"
        CANCEL = "cancel"
        REFUND = "refund"

    class Status(models.TextChoices):
        APPROVED = "approved"
        DECLINED = "declined"
        NO_RECORD = "no_record"  # retired by a status query; a late arrival gets 409

    operation_id = models.CharField(max_length=32, unique=True)
    type = models.CharField(max_length=16, choices=Type.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    decline_reason = models.CharField(max_length=40, blank=True)
    merchant_id = models.CharField(max_length=32, null=True, blank=True)
    payment_id = models.CharField(max_length=32, null=True, blank=True)
    refund_id = models.CharField(max_length=32, null=True, blank=True)
    # For capture, cancel and refund: the authorise operation they act on.
    authorisation_ref = models.CharField(max_length=32, null=True, blank=True)
    # The authorisation this operation belongs to (approved authorise: the one it opened).
    authorisation = models.ForeignKey(Authorisation, null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="operations")
    amount = models.PositiveIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, null=True, blank=True)
    # Brand and last four only. The card number and security code are never stored.
    card_brand = models.CharField(max_length=16, blank=True)
    card_last4 = models.CharField(max_length=4, blank=True)
    simulator_reference = models.CharField(max_length=32, unique=True, null=True, blank=True)
    # SHA-256 of the request without card number and security code, to spot a reused ID.
    request_fingerprint = models.CharField(max_length=64, blank=True)
    # v2: how the authorisation crossed the network (NETWORK-CONTRACT.md 5), no card number.
    network = models.JSONField(null=True, blank=True)
    recorded_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(status="no_record") | Q(simulator_reference__isnull=False),
                name="sim_record_has_reference",
            ),
        ]

    def __str__(self):
        return f"{self.operation_id} {self.type} {self.status}"
