"""Double-entry ledger (v2, Sprint 1).

Every movement of money is one JournalEntry with two or more JournalLines. A line's amount
is signed: a debit is positive and a credit is negative, so the lines of an entry always sum
to zero in each currency. The database enforces that at commit time (migration 0002), and
refuses any UPDATE or DELETE on entries and lines, like the audit trail. A mistake is fixed
with a new, reversing entry, never by editing an old one.
"""
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from apps.merchants.models import Merchant


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("ledger rows are append-only")

    def delete(self):
        raise PermissionError("ledger rows are append-only")


class AppendOnlyModel(models.Model):
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("ledger rows are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("ledger rows are append-only")


class LedgerAccount(models.Model):
    """One account in the chart of accounts. Merchant accounts belong to one merchant."""

    class Kind(models.TextChoices):
        # Gateway-wide
        NETWORK_CLEARING = "network_clearing"  # asset: money the card network owes us
        FEE_REVENUE = "fee_revenue"  # income: our processing fees
        # One of each per merchant
        MERCHANT_AVAILABLE = "merchant_available"  # liability: owed to the merchant now
        MERCHANT_RESERVE = "merchant_reserve"  # liability: owed to the merchant, held back

    # Debit-normal kinds; the others are credit-normal (see `balance`).
    DEBIT_NORMAL = frozenset({Kind.NETWORK_CLEARING})
    MERCHANT_KINDS = frozenset({Kind.MERCHANT_AVAILABLE, Kind.MERCHANT_RESERVE})

    kind = models.CharField(max_length=32, choices=Kind.choices)
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="ledger_accounts")
    currency = models.CharField(max_length=3, default="SGD")
    # Test and live mode keep separate books: every account, gateway-wide ones included,
    # exists once per mode, so test money can never reach a live balance.
    livemode = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["kind", "merchant", "currency", "livemode"],
                                    name="ledger_account_unique_per_merchant_mode"),
            models.UniqueConstraint(fields=["kind", "currency", "livemode"],
                                    condition=Q(merchant__isnull=True),
                                    name="ledger_account_unique_gateway_wide_mode"),
            models.CheckConstraint(
                condition=(Q(kind__in=["merchant_available", "merchant_reserve"],
                             merchant__isnull=False)
                           | Q(kind__in=["network_clearing", "fee_revenue"],
                               merchant__isnull=True)),
                name="ledger_account_merchant_matches_kind",
            ),
        ]

    def __str__(self):
        owner = self.merchant.public_id if self.merchant_id else "gateway"
        return f"{self.kind} {owner} {self.currency}{' live' if self.livemode else ''}"

    def balance(self) -> int:
        """The balance in its natural sign: positive means the account holds money."""
        total = self.lines.aggregate(total=Sum("amount"))["total"] or 0
        return total if self.kind in self.DEBIT_NORMAL else -total


class JournalEntry(AppendOnlyModel):
    """One balanced movement of money, posted in the same transaction as its cause."""

    class Kind(models.TextChoices):
        CAPTURE = "capture"
        REFUND = "refund"
        RESERVE_RELEASE = "reserve_release"  # source: the capture entry (jrn_...)
        PAYOUT = "payout"  # source: the payout (po_...)

    public_id = models.CharField(max_length=32, unique=True, editable=False)  # jrn_...
    kind = models.CharField(max_length=16, choices=Kind.choices)
    # The object that caused it (pay_... for a capture, rfd_... for a refund, the capture's
    # jrn_... for a reserve release, po_... for a payout). One entry per cause and kind, so a
    # repeated or late answer can never post twice.
    source_id = models.CharField(max_length=32)
    payment = models.ForeignKey("payments.Payment", on_delete=models.PROTECT, null=True,
                                blank=True, related_name="journal_entries")
    payout = models.ForeignKey("ledger.Payout", on_delete=models.PROTECT, null=True,
                               blank=True, related_name="journal_entries")
    refund = models.ForeignKey("refunds.Refund", on_delete=models.PROTECT, null=True,
                               blank=True, related_name="journal_entries")
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT,
                                 related_name="journal_entries")
    livemode = models.BooleanField(default=False, editable=False)  # same as all its accounts
    description = models.CharField(max_length=200, blank=True)
    # When the money moved (the capture or refund time); differs from posted_at only for
    # entries added later by `manage.py ledger_backfill`.
    occurred_at = models.DateTimeField()
    posted_at = models.DateTimeField(default=timezone.now, editable=False)
    backfilled = models.BooleanField(default=False)

    class Meta:
        ordering = ["posted_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["kind", "source_id"],
                                    name="journal_entry_one_per_source"),
            models.CheckConstraint(
                condition=(Q(kind="payout", payout__isnull=False, payment__isnull=True)
                           | (~Q(kind="payout") & Q(payment__isnull=False, payout__isnull=True))),
                name="journal_entry_cause_matches_kind",
            ),
        ]
        indexes = [models.Index(fields=["merchant", "-posted_at"]),
                   models.Index(fields=["merchant", "livemode", "-posted_at"]),
                   models.Index(fields=["kind", "occurred_at"])]

    def __str__(self):
        return f"{self.public_id} {self.kind} {self.source_id}"


class JournalLine(AppendOnlyModel):
    """One side of an entry: positive = debit, negative = credit, never zero."""

    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="lines")
    amount = models.BigIntegerField()  # minor units (cents)
    currency = models.CharField(max_length=3, default="SGD")
    memo = models.CharField(max_length=32)  # gross, fee, reserve, net, refund, release, payout

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=~Q(amount=0), name="journal_line_not_zero"),
        ]

    def __str__(self):
        side = "Dr" if self.amount > 0 else "Cr"
        return f"{side} {abs(self.amount)} {self.currency} {self.account}"


class Payout(models.Model):
    """Money sent from a merchant's available balance to their bank account.

    Thin slice (v2 Sprint 1): the transfer itself is simulated and counts as paid at once.
    The ledger entry (Dr merchant_available, Cr network_clearing) is posted in the same
    transaction, under a lock on the merchant's available account, so two payouts can never
    together take more than the balance.
    """

    class Status(models.TextChoices):
        PAID = "paid"

    public_id = models.CharField(max_length=32, unique=True, editable=False)  # po_...
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name="payouts")
    livemode = models.BooleanField(default=False, editable=False)
    amount = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="SGD")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PAID)
    requested_by = models.CharField(max_length=32, blank=True)  # stf_... or "system"
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payout_amount_positive"),
        ]

    def __str__(self):
        return f"{self.public_id} {self.amount} {self.status}"
