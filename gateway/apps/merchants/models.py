"""Merchants and their API keys (FR-MER-01 to FR-MER-03, FR-MER-10).

A merchant is either created by an administrator, or registers itself (sprint 10, FR-MER-01):
then it carries its business details and contact email, and an administrator can approve it
only after that email is verified. Each merchant has one active API key and one signing secret.
"""
import hashlib

from django.db import models
from django.db.models import Q

from apps.common import crypto
from apps.common.ids import new_id, random_base62

API_KEY_PREFIX = "sp_test_"  # test mode; kept as the v1 name
LIVE_API_KEY_PREFIX = "sp_live_"  # v2: live mode ("live" is still sandbox money)
API_KEY_BODY_LENGTH = 40
SIGNING_SECRET_PREFIX = "whsec_"  # nosec B105


class Merchant(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        ACTIVE = "active"
        REJECTED = "rejected"
        SUSPENDED = "suspended"  # FR-ADM-02: API and dashboard off; can be reinstated
        DEACTIVATED = "deactivated"  # permanent: keys revoked, history kept

    public_id = models.CharField(max_length=32, unique=True, editable=False)
    name = models.CharField(max_length=120)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    # Encrypted `whsec_...`; signs webhooks and API replies (API-CONTRACT.md section 2).
    signing_secret_encrypted = models.TextField(editable=False)
    webhook_url = models.URLField(max_length=2048, blank=True)
    webhook_url_approved = models.BooleanField(default=False)  # FR-ADM-13
    # Per-merchant ceiling set by an administrator (FR-ADM-07); null = only the global limit.
    max_amount_cents = models.PositiveIntegerField(null=True, blank=True)
    # Self-registration (D7). Blank for merchants an administrator created.
    registration_number = models.CharField(max_length=10, blank=True)  # Singapore UEN
    website = models.URLField(max_length=300, blank=True)
    contact_name = models.CharField(max_length=120, blank=True)
    contact_email = models.EmailField(max_length=254, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    status_reason = models.CharField(max_length=200, blank=True)  # suspension or deactivation
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["pending", "active", "rejected", "suspended",
                                         "deactivated"]),
                name="merchant_status_valid",
            ),
            # "This business is already registered" (UC-15); a rejected one may apply again.
            models.UniqueConstraint(
                fields=["registration_number"],
                condition=~Q(registration_number="") & ~Q(status="rejected"),
                name="merchant_uen_unique",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.public_id})"

    # DRF treats the authenticated principal as request.user; a merchant authenticated by
    # API key is the user of a merchant API request.
    is_authenticated = True
    is_anonymous = False

    @property
    def is_active_merchant(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def self_registered(self) -> bool:
        return bool(self.contact_email)

    @property
    def signing_secret(self) -> str:
        return crypto.decrypt(self.signing_secret_encrypted)

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = new_id("mer")
        if not self.signing_secret_encrypted:
            self.rotate_signing_secret()
        super().save(*args, **kwargs)

    def rotate_signing_secret(self) -> str:
        """Set a new signing secret and return it. Show it to the merchant once."""
        secret = SIGNING_SECRET_PREFIX + random_base62(32)
        self.signing_secret_encrypted = crypto.encrypt(secret)
        return secret


def digest_api_key(key: str) -> str:
    """SHA-256 of the full key. Enough for a 238-bit random key; lets us look it up by index."""
    return hashlib.sha256(key.encode()).hexdigest()


class ApiKeyQuerySet(models.QuerySet):
    def active(self):
        return self.filter(revoked_at__isnull=True)


class ApiKey(models.Model):
    """A merchant API key. Only the digest is stored; the key is shown once (FR-MER-03)."""

    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name="api_keys")
    public_id = models.CharField(max_length=32, unique=True, editable=False)
    digest = models.CharField(max_length=64, unique=True, editable=False)
    # First characters after the prefix, so staff can tell keys apart without seeing them.
    hint = models.CharField(max_length=24, editable=False)
    # v2 test and live mode: a key works in exactly one mode, and everything it creates or
    # reads (payments, refunds, payouts, balance) belongs to that mode only.
    livemode = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    objects = ApiKeyQuerySet.as_manager()

    class Meta:
        constraints = [
            # One active key per merchant and mode: rotation revokes the old key and issues
            # the new one in the same transaction (FR-MER-04).
            models.UniqueConstraint(
                fields=["merchant", "livemode"], condition=Q(revoked_at__isnull=True),
                name="one_active_api_key_per_merchant_mode",
            ),
        ]

    def __str__(self):
        return f"{self.hint}... ({'revoked' if self.revoked_at else 'active'})"

    @property
    def mode(self) -> str:
        return "live" if self.livemode else "test"

    @classmethod
    def issue(cls, merchant: Merchant, livemode: bool = False) -> tuple["ApiKey", str]:
        """Create a key for the merchant and return it with the plaintext, to show once."""
        prefix = LIVE_API_KEY_PREFIX if livemode else API_KEY_PREFIX
        plaintext = prefix + random_base62(API_KEY_BODY_LENGTH)
        api_key = cls.objects.create(
            merchant=merchant,
            public_id=new_id("key"),
            digest=digest_api_key(plaintext),
            hint=plaintext[: len(prefix) + 4],
            livemode=livemode,
        )
        return api_key, plaintext


def parse_mode(value, param: str = "mode") -> bool:
    """'test' (or nothing) -> False, 'live' -> True; anything else is a 400."""
    from apps.common.errors import invalid

    if value in (None, "", "test"):
        return False
    if value == "live":
        return True
    raise invalid("invalid_field", "mode must be 'test' or 'live'.", param=param)
