"""SecurePay Bank's 3-D Secure authentications (v2, docs/v2/THREE-DS.md).

One row per payment the gateway asked the bank to authenticate. The bank keeps a keyed
hash of the card number (to match the later 0100), never the number. The one-time code is
not stored either: it is derived from the authentication's ID with the bank's key.
"""
from django.db import models


class Authentication(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"  # challenge sent, waiting for the cardholder's code
        AUTHENTICATED = "authenticated"  # transStatus Y: right code, CAVV issued
        FAILED = "failed"  # transStatus N: three wrong codes

    three_ds_id = models.CharField(max_length=28, unique=True)
    merchant_id = models.CharField(max_length=32, db_index=True)
    payment_id = models.CharField(max_length=32, unique=True)
    pan_hash = models.CharField(max_length=64)
    last4 = models.CharField(max_length=4)
    amount = models.PositiveIntegerField()  # SGD cents, as the 0100 will carry it
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    wrong_attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    authenticated_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)  # the 0100 that carried its CAVV

    def __str__(self):
        return f"{self.three_ds_id} *{self.last4} {self.status}"
