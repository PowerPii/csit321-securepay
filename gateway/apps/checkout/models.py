"""Simulated verification (FR-CHK-05, FR-CHK-06, FR-CHK-18; screen C5).

One challenge per payment attempt. Only a keyed hash of the current code is stored; a new
code replaces the old one. Wrong codes are counted across codes, so asking for a new code
does not reset the 3 attempts.
"""
from django.db import models


class VerificationChallenge(models.Model):
    payment = models.OneToOneField("payments.Payment", on_delete=models.PROTECT,
                                   related_name="verification")
    code_hash = models.CharField(max_length=64)
    code_sent_at = models.DateTimeField()
    code_expires_at = models.DateTimeField()
    codes_sent = models.PositiveSmallIntegerField(default=1)
    wrong_attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # v2 3-D Secure (docs/v2/THREE-DS.md): the card's bank challenges the shopper instead of
    # the email code. The bank keeps the code; `three_ds_test_code` is the simulated text
    # message the checkout shows, as there is no phone. ECI and CAVV are the bank's proof.
    three_ds_id = models.CharField(max_length=28, blank=True, default="")
    three_ds_status = models.CharField(max_length=16, blank=True, default="")
    three_ds_phone_hint = models.CharField(max_length=16, blank=True, default="")
    three_ds_test_code = models.CharField(max_length=6, blank=True, default="")
    three_ds_eci = models.CharField(max_length=2, blank=True, default="")
    three_ds_cavv = models.CharField(max_length=28, blank=True, default="")

    def __str__(self):
        return f"verification for payment {self.payment_id}"
