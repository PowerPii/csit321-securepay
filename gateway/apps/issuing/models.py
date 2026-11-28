"""Issued virtual cards (v2 issuing, docs/v2/ISSUING.md).

The gateway keeps the merchant's view of a card: who made it, its limit and the last four
digits. The bank (the simulator's issuer, "SecurePay Bank") holds what it spent and decides
every payment. The card number and security code are never stored here; they are in the
reply to the create call only.
"""
from django.db import models
from django.utils import timezone

from apps.merchants.models import Merchant


class IssuedCard(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        USED = "used"
        CANCELLED = "cancelled"

    public_id = models.CharField(max_length=32, unique=True, editable=False)  # ic_...
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name="issued_cards")
    livemode = models.BooleanField(default=False, editable=False)
    # SHA-256 of the merchant, mode and Idempotency-Key: a retry finds the same card.
    request_hash = models.CharField(max_length=64, unique=True, editable=False)
    description = models.CharField(max_length=100, blank=True)
    last4 = models.CharField(max_length=4)
    expiry_month = models.PositiveSmallIntegerField()
    expiry_year = models.PositiveSmallIntegerField()
    spending_limit = models.PositiveIntegerField()  # SGD cents, over the card's life
    single_use = models.BooleanField(default=False)
    # Last seen at the bank; the bank's own figures are fetched for a single card.
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    spent = models.PositiveIntegerField(default=0)
    created_by = models.CharField(max_length=40, blank=True)  # key_... or stf_...
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.public_id} *{self.last4}"
