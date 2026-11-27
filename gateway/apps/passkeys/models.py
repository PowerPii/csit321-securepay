"""Passkeys (WebAuthn credentials) and the one-time challenges they sign (docs/v2/PASSKEYS.md).

A passkey belongs to one shopper or one staff member. It stores only what the browser gave us at registration: the credential ID, the public
key and the signature counter. The private key never leaves the shopper's device.

A challenge is stored as its SHA-256 only. The browser signs the challenge inside
clientDataJSON, so the gateway finds the row from the answer itself, checks it was issued
for the same purpose (and payment), and uses it once.
"""
from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.common.ids import new_id


class Passkey(models.Model):
    public_id = models.CharField(max_length=32, unique=True, editable=False)  # pk_...
    # Exactly one owner: a shopper account or (v2 slice 3) a staff account.
    customer = models.ForeignKey("customers.CustomerAccount", on_delete=models.CASCADE,
                                 null=True, blank=True, related_name="passkeys")
    staff = models.ForeignKey("accounts.StaffAccount", on_delete=models.CASCADE, null=True,
                              blank=True, related_name="passkeys")
    credential_id = models.BinaryField(unique=True)
    public_key = models.BinaryField()
    sign_count = models.BigIntegerField(default=0)
    transports = models.JSONField(default=list, blank=True)
    name = models.CharField(max_length=60)
    aaguid = models.CharField(max_length=36, blank=True)
    backed_up = models.BooleanField(default=False)  # synced passkey (iCloud, Google, ...)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [models.CheckConstraint(
            name="passkey_has_one_owner",
            condition=(models.Q(customer__isnull=False, staff__isnull=True)
                       | models.Q(customer__isnull=True, staff__isnull=False)))]

    def __str__(self):
        return f"{self.public_id} {self.name}"

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = new_id("pk")
        super().save(*args, **kwargs)


class WebAuthnChallenge(models.Model):
    class Purpose(models.TextChoices):
        REGISTER = "register"
        SIGN_IN = "sign_in"
        APPROVE_PAYMENT = "approve_payment"
        STAFF_SIGN_IN = "staff_sign_in"  # step 2 of the dashboard sign-in

    challenge_hash = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    customer = models.ForeignKey("customers.CustomerAccount", on_delete=models.CASCADE,
                                 null=True, blank=True, related_name="+")
    staff = models.ForeignKey("accounts.StaffAccount", on_delete=models.CASCADE, null=True,
                              blank=True, related_name="+")
    payment = models.ForeignKey("payments.Payment", on_delete=models.CASCADE, null=True,
                                blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["expires_at"])]

    @classmethod
    def purge_expired(cls) -> int:
        deleted, _ = cls.objects.filter(
            expires_at__lt=timezone.now() - timedelta(hours=1)).delete()
        return deleted
