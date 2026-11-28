"""Payments, processing operations and idempotency records.

Rule: one financial effect per logical operation. Every call to the simulator is an
Operation whose ID the gateway creates and stores before it calls; a retry reuses the ID.
"""
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.common.ids import new_id, new_reference
from apps.merchants.models import Merchant
from apps.payments.states import FINAL_STATES, BlockReason, PaymentStatus


def _choices(enum):
    return [(m.value, m.value) for m in enum]


class OutcomeReason(models.TextChoices):
    """Why a payment did not go ahead (payment.schema.json OutcomeReason)."""

    FRAUD_RULE = BlockReason.FRAUD_RULE
    FRAUD_MODEL = BlockReason.FRAUD_MODEL
    VERIFICATION_FAILED = BlockReason.VERIFICATION_FAILED
    VERIFICATION_EXPIRED = BlockReason.VERIFICATION_EXPIRED
    SCREENING_UNAVAILABLE = BlockReason.SCREENING_UNAVAILABLE
    AMOUNT_LIMIT = BlockReason.AMOUNT_LIMIT
    CARD_DECLINED = "card_declined"
    NOT_PROCESSED = "not_processed"
    CHECKOUT_EXPIRED = "checkout_expired"
    MERCHANT_CANCELLED = "merchant_cancelled"
    AUTHORISATION_EXPIRED = "authorisation_expired"


class ScreeningDecision(models.TextChoices):
    ALLOW = "allow"
    VERIFY = "verify"
    BLOCK = "block"
    NOT_SCREENED = "not_screened"  # screening unavailable, attempt blocked (FR-FRD-04)


class Payment(models.Model):
    """A card payment. Merchant, amount and currency are fixed at creation (FR-CHK-03)."""

    public_id = models.CharField(max_length=32, unique=True, editable=False)
    reference = models.CharField(max_length=12, unique=True, editable=False)  # SP-XXXX-XXXX
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name="payments")
    # v2: the mode of the API key that created it. Test and live payments never mix: a key
    # only sees its own mode, and each mode has its own ledger accounts.
    livemode = models.BooleanField(default=False, editable=False)
    order_reference = models.CharField(max_length=64)
    # In the payment's currency, as integers of its smallest unit (cents for SGD; whole yen).
    amount = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="SGD")
    # v2 multi-currency (docs/v2/FX.md): the rate locked when the payment was created (SGD
    # per one unit; 1 for SGD), the sheet it came from, the FX fee, and the amount in SGD
    # cents. Capture, refunds, fees, screening and the simulator all use SGD.
    fx_rate = models.DecimalField(max_digits=20, decimal_places=10, default=1)
    fx_rate_sheet = models.CharField(max_length=32, blank=True)
    fx_fee_bps = models.PositiveSmallIntegerField(default=0)
    settlement_amount = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=24, choices=_choices(PaymentStatus), default=PaymentStatus.CREATED
    )
    return_url = models.URLField(max_length=2048)
    customer_email = models.EmailField(max_length=254, blank=True)
    # The customer who was signed in when the payment was submitted (FR-ACC-04). Guests: null.
    customer = models.ForeignKey("customers.CustomerAccount", on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name="payments")
    # FR-ACC-12: the privacy notice version the customer agreed to when entering the card.
    privacy_notice_version = models.CharField(max_length=16, blank=True)
    description = models.CharField(max_length=200, blank=True)
    # [{name, quantity, unit_amount}] as the merchant sent them; they add up to `amount`.
    line_items = models.JSONField(null=True, blank=True)

    # Hosted checkout session (FR-CHK-16): the ID is the only thing in the checkout URL.
    checkout_session_id = models.CharField(max_length=32, unique=True, editable=False)
    checkout_expires_at = models.DateTimeField()

    # Card summary after tokenisation in the vault. Never a card number (FR-TXN-09).
    card_token = models.CharField(max_length=32, blank=True)
    card_brand = models.CharField(max_length=16, blank=True)
    card_last4 = models.CharField(max_length=4, blank=True)
    # Keyed HMAC of the card number from the vault, for prior-activity screening features.
    card_fingerprint = models.CharField(max_length=64, blank=True)
    # v2 learning loop: the card's first 6 digits (issuer range, BIN), for range features.
    card_bin = models.CharField(max_length=8, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    screening_decision = models.CharField(
        max_length=16, choices=ScreeningDecision.choices, blank=True
    )
    outcome_reason = models.CharField(max_length=32, choices=OutcomeReason.choices, blank=True)

    captured_amount = models.PositiveIntegerField(default=0)
    captured_settlement_amount = models.PositiveIntegerField(default=0)  # SGD cents
    # Increases on every change the merchant is notified about (API-CONTRACT.md section 6).
    sequence = models.PositiveIntegerField(default=1)

    # FR-ADM-09: HMAC of the fields that decide money and status, set on every save and
    # checked on a schedule (apps/security/integrity.py). Empty only for rows from before it.
    integrity_hash = models.CharField(max_length=64, blank=True, editable=False)

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    authorised_at = models.DateTimeField(null=True, blank=True)
    captured_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["merchant", "-created_at"]),
            models.Index(fields=["merchant", "livemode", "-created_at"]),
            models.Index(fields=["status", "checkout_expires_at"]),
        ]
        constraints = [
            # The limits are in SGD (S$0.50 to S$10,000), whatever the payment's currency.
            models.CheckConstraint(
                condition=Q(settlement_amount__gte=50) & Q(settlement_amount__lte=1_000_000)
                & Q(amount__gt=0),
                name="payment_settlement_amount_in_range",
            ),
            models.CheckConstraint(
                condition=Q(currency__in=["SGD", "USD", "EUR", "MYR", "THB", "IDR", "PHP",
                                          "JPY"]),
                name="payment_currency_supported",
            ),
            models.CheckConstraint(
                condition=Q(status__in=[s.value for s in PaymentStatus]),
                name="payment_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(captured_amount__lte=models.F("amount")),
                name="payment_captured_within_amount",
            ),
        ]

    def __str__(self):
        return f"{self.reference} {self.status} {self.amount}"

    def save(self, *args, **kwargs):
        from apps.security.integrity import payment_digest

        if self.currency == "SGD":  # v2 FX: an SGD payment's SGD amounts are its amounts
            self.settlement_amount = self.amount
            self.captured_settlement_amount = self.captured_amount
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "settlement_amount",
                                           "captured_settlement_amount"}
        self.integrity_hash = payment_digest(self)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = {*kwargs["update_fields"], "integrity_hash"}
        super().save(*args, **kwargs)

    def to_sgd(self, amount: int) -> int:
        """SGD cents for `amount` of this payment's currency, at its locked rate."""
        from apps.fx.rates import to_sgd

        return to_sgd(amount, self.currency, self.fx_rate)

    @property
    def is_final(self) -> bool:
        return PaymentStatus(self.status) in FINAL_STATES

    @property
    def authorisation_expires_at(self):
        if self.status != PaymentStatus.AUTHORISED or self.authorised_at is None:
            return None
        return self.authorised_at + timedelta(days=settings.GATEWAY_RULES["AUTH_EXPIRY_DAYS"])

    @property
    def checkout_url(self) -> str | None:
        if self.status != PaymentStatus.CREATED or timezone.now() >= self.checkout_expires_at:
            return None
        return f"{settings.CHECKOUT_BASE_URL.rstrip('/')}/{self.checkout_session_id}"

    @classmethod
    def create_for_merchant(cls, merchant: Merchant, **fields) -> "Payment":
        """Insert a new payment with fresh identifiers, retrying the rare reference clash."""
        now = timezone.now()
        ttl = timedelta(minutes=settings.GATEWAY_RULES["CHECKOUT_SESSION_TTL_MINUTES"])
        for attempt in range(5):
            payment = cls(
                merchant=merchant,
                public_id=new_id("pay"),
                reference=new_reference("SP"),
                checkout_session_id=new_id("cs"),
                created_at=now,
                checkout_expires_at=now + ttl,
                **fields,
            )
            try:
                with transaction.atomic():
                    payment.save(force_insert=True)
                return payment
            except IntegrityError:
                if attempt == 4:
                    raise
        raise AssertionError("unreachable")


class Operation(models.Model):
    """One call to the processing simulator with a gateway-generated ID (FR-SIM-03, UC-28).

    The row is committed before the simulator is called. If the answer is lost, the payment
    or refund is Unresolved and reconciliation asks the simulator about this same ID, so the
    customer is never charged twice.
    """

    class Type(models.TextChoices):
        AUTHORISE = "authorise"
        CAPTURE = "capture"
        CANCEL = "cancel"
        REFUND = "refund"

    class Status(models.TextChoices):
        PENDING = "pending"  # recorded, call not answered yet
        SUCCEEDED = "succeeded"
        FAILED = "failed"  # the simulator answered no (declined)
        UNRESOLVED = "unresolved"  # no answer; reconciliation owns it
        NOT_PROCESSED = "not_processed"  # simulator has no record; retired, never resent

    OPEN_STATUSES = (Status.PENDING, Status.UNRESOLVED)

    public_id = models.CharField(max_length=32, unique=True, editable=False)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="operations")
    type = models.CharField(max_length=16, choices=Type.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    amount = models.PositiveIntegerField()  # in the payment's currency
    # v2: what the simulator is asked for, in SGD cents (= amount for SGD payments).
    settlement_amount = models.PositiveIntegerField(default=0)
    # The simulator's record ID and its signed reply, kept as evidence (SIMULATOR-CONTRACT.md
    # section 2; FR-TXN-13: corrections need simulator evidence).
    simulator_reference = models.CharField(max_length=32, blank=True)
    evidence_body = models.TextField(blank=True)
    evidence_signature = models.CharField(max_length=128, blank=True)
    # The merchant's Idempotency-Key for a capture or cancel, so a retry of a request whose
    # reply was lost finds this operation instead of making a second one.
    request_key = models.CharField(max_length=255, blank=True)
    # For cancel: why (merchant_cancelled, or authorisation_expired for the 7-day job).
    reason = models.CharField(max_length=32, blank=True)
    # Reconciliation (SIMULATOR-CONTRACT.md section 7, S2): status checks done so far, when
    # the next one is due, and when the operation was handed to administrators (UC-42).
    reconciliation_checks = models.PositiveSmallIntegerField(default=0)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    next_check_at = models.DateTimeField(null=True, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "last_checked_at"]),
                   models.Index(fields=["next_check_at"], condition=Q(status="unresolved"),
                                name="operation_check_due_idx")]
        constraints = [
            # A payment is authorised once; a retry of the same attempt reuses the operation.
            models.UniqueConstraint(
                fields=["payment"], condition=Q(type="authorise"),
                name="one_authorise_operation_per_payment",
            ),
            # Only one capture or cancel can be in flight (409 operation_in_progress).
            models.UniqueConstraint(
                fields=["payment"],
                condition=Q(type__in=["capture", "cancel"], status__in=["pending", "unresolved"]),
                name="one_open_capture_or_cancel_per_payment",
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name="operation_amount_positive"),
        ]

    def __str__(self):
        return f"{self.public_id} {self.type} {self.status}"

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = new_id("op")
        if not self.settlement_amount and self.amount:  # v2 FX: SGD at the payment's rate
            self.settlement_amount = self.payment.to_sgd(self.amount)
        super().save(*args, **kwargs)

    def mark_unresolved(self) -> None:
        """No usable answer: reconciliation owns it from now on. Schedules the first status
        check (30 s, SIMULATOR-CONTRACT.md S2) unless one is already scheduled."""
        self.status = Operation.Status.UNRESOLVED
        if self.next_check_at is None and self.escalated_at is None:
            self.next_check_at = timezone.now() + check_delay(0)
        self.save(update_fields=["status", "next_check_at", "updated_at"])


_random = secrets.SystemRandom()


def check_delay(checks_done: int) -> timedelta:
    """Wait before status check number `checks_done + 1`: RECONCILIATION_DELAYS_SECONDS with
    +/-10% jitter, so a burst of timeouts is not checked all at once."""
    delays = settings.GATEWAY_RULES["RECONCILIATION_DELAYS_SECONDS"]
    base = delays[min(checks_done, len(delays) - 1)]
    return timedelta(seconds=base * _random.uniform(0.9, 1.1))


class IdempotencyRecord(models.Model):
    """A merchant's Idempotency-Key and the stored reply (API-CONTRACT.md section 5).

    Created as `in_progress` before the request runs and completed in the same transaction
    as the request's own writes, so a stored reply always matches what was committed.
    """

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress"
        COMPLETED = "completed"

    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE,
                                 related_name="idempotency_records")
    livemode = models.BooleanField(default=False)  # keys are scoped per mode
    key = models.CharField(max_length=255)
    method = models.CharField(max_length=8)
    path = models.CharField(max_length=512)
    request_fingerprint = models.CharField(max_length=64)  # SHA-256 hex, never the body
    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.IN_PROGRESS)
    locked_at = models.DateTimeField(default=timezone.now)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    # The exact bytes sent, so a replay is byte-identical (jsonb would reorder keys).
    response_body = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["merchant", "livemode", "key"],
                                    name="idempotency_key_per_merchant_mode"),
        ]
        indexes = [models.Index(fields=["expires_at"])]

    def __str__(self):
        return f"{self.key} {self.method} {self.path} {self.status}"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @classmethod
    def purge_expired(cls) -> int:
        """Delete records past their 24 hours. For the worker's housekeeping job."""
        deleted, _ = cls.objects.filter(expires_at__lte=timezone.now()).delete()
        return deleted
