"""SecurePay Bank's issued cards (v2 issuing, docs/v2/ISSUING.md).

Virtual cards that merchants create through the gateway. The card number is never stored:
the bank keeps a keyed hash to find the card in an authorisation, and the last four digits.
Only the issuer node authorises against these rows; the card-management API (/sim/issuing)
creates and cancels them.
"""
from django.db import models


class IssuedCard(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        USED = "used"  # a single-use card after its one approved payment
        CANCELLED = "cancelled"

    card_id = models.CharField(max_length=40, unique=True)  # the gateway's ic_ ID
    merchant_id = models.CharField(max_length=32, db_index=True)
    pan_hash = models.CharField(max_length=64, unique=True)
    last4 = models.CharField(max_length=4)
    expiry_month = models.PositiveSmallIntegerField()
    expiry_year = models.PositiveSmallIntegerField()
    spending_limit = models.PositiveIntegerField()  # SGD cents, over the card's life
    spent = models.PositiveIntegerField(default=0)  # approved and not reversed
    single_use = models.BooleanField(default=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.card_id} *{self.last4} {self.status}"


class IssuerAuthorisation(models.Model):
    """What the bank answered for each 0100 on an issued card."""

    card = models.ForeignKey(IssuedCard, on_delete=models.PROTECT, related_name="authorisations")
    stan = models.CharField(max_length=6)
    rrn = models.CharField(max_length=12)
    acceptor_id = models.CharField(max_length=15)  # DE 42
    amount = models.PositiveIntegerField()
    response_code = models.CharField(max_length=2)
    captured = models.PositiveIntegerField(null=True, blank=True)
    reversed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["stan", "rrn"], name="issuer_auth_keys")]
        ordering = ["-created_at"]
