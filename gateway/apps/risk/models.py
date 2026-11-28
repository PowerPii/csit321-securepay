"""Screening records and the seeded block / review lists (architecture section 7, FR-FRD-01).

`ScreeningEvent` is both the decision record an analyst reads (score, reasons, feature values,
model / rules / policy versions) and the history that prior-activity features count. There is
one per payment attempt, so a retried submission can never be counted twice.

Nothing here holds a card number or a readable email or IP address: cards are keyed by the
vault's fingerprint, emails and IPs by a keyed hash (apps.risk.hashing).
"""
from django.db import models
from django.db.models import Q

from apps.common.ids import new_id


class ScreeningOutcome(models.TextChoices):
    ALLOW = "allow"
    VERIFY = "verify"
    BLOCK = "block"
    NOT_SCREENED = "not_screened"  # screening unavailable, the attempt was blocked (FR-FRD-04)


class ScreeningEvent(models.Model):
    public_id = models.CharField(max_length=32, unique=True, editable=False)  # scr_...
    payment = models.OneToOneField("payments.Payment", on_delete=models.PROTECT,
                                   related_name="screening")
    merchant = models.ForeignKey("merchants.Merchant", on_delete=models.PROTECT,
                                 related_name="+")
    occurred_at = models.DateTimeField()
    amount = models.PositiveIntegerField()

    # Keys for prior-activity counts (never the raw values).
    card_fingerprint = models.CharField(max_length=64, blank=True)
    card_bin = models.CharField(max_length=8, blank=True)  # fs-0.3: issuer range (6 digits)
    email_hash = models.CharField(max_length=64, blank=True)
    ip_hash = models.CharField(max_length=64, blank=True)
    device_hash = models.CharField(max_length=64, blank=True)  # no device cookie yet

    # The decision.
    outcome = models.CharField(max_length=16, choices=ScreeningOutcome.choices)
    score = models.FloatField(null=True, blank=True)
    reasons = models.JSONField(default=list, blank=True)
    features = models.JSONField(default=dict, blank=True)
    model_version = models.CharField(max_length=64, blank=True)
    rules_version = models.CharField(max_length=32, blank=True)
    policy_version = models.CharField(max_length=32, blank=True)
    feature_schema_version = models.CharField(max_length=32, blank=True)
    elapsed_ms = models.FloatField(default=0)
    # Plain-language reasons: rule hits plus the model's TreeSHAP contributions (apps.risk.explain).
    explanation = models.JSONField(default=dict, blank=True)
    # v2 learning loop: the challenger's score in the shadow. Recorded, never decides.
    shadow_model_version = models.CharField(max_length=64, blank=True)
    shadow_score = models.FloatField(null=True, blank=True)
    shadow_outcome = models.CharField(max_length=16, blank=True)
    # Why screening was unavailable (exception class / timeout). Never customer data.
    error = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["card_fingerprint", "occurred_at"], name="screen_card_time"),
            models.Index(fields=["email_hash", "occurred_at"], name="screen_email_time"),
            models.Index(fields=["ip_hash", "occurred_at"], name="screen_ip_time"),
            models.Index(fields=["card_bin", "occurred_at"], name="screen_bin_time"),
            models.Index(fields=["outcome", "-occurred_at"], name="screen_outcome_time"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(outcome__in=[o.value for o in ScreeningOutcome]),
                name="screening_outcome_valid",
            ),
        ]

    def __str__(self):
        return f"{self.public_id} {self.outcome}"

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = new_id("scr")
        super().save(*args, **kwargs)


class RiskListEntry(models.Model):
    """A card, email or IP on the block list or the review (verify) list.

    `manage.py seed_risk_lists` seeds the demo entries. Fraud analysts add cards and IP
    addresses to the block list from the dashboard with a reason, and administrators remove
    them (FR-FRD-09). A removed entry is kept, switched off, with who removed it and when.
    Values are stored the same way screening keys them.
    """

    class Kind(models.TextChoices):
        CARD = "card"  # vault card fingerprint
        EMAIL = "email"  # keyed hash of the lower-cased email
        IP = "ip"  # keyed hash of the IP address

    class Action(models.TextChoices):
        BLOCK = "block"
        VERIFY = "verify"

    kind = models.CharField(max_length=8, choices=Kind.choices)
    value_hash = models.CharField(max_length=64)
    action = models.CharField(max_length=8, choices=Action.choices)
    label = models.CharField(max_length=100, help_text="Why it is listed; shown to analysts.")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # FR-FRD-09: who listed it (blank for seeded entries), and who removed it.
    added_by = models.CharField(max_length=32, blank=True)  # stf_...
    source_case = models.CharField(max_length=32, blank=True)  # scr_... it was listed from
    # What analysts see instead of the value: "Visa •••• 0101" or "203.0.113.•" (masked).
    display = models.CharField(max_length=60, blank=True)
    removed_by = models.CharField(max_length=32, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removal_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["kind", "value_hash"], name="one_list_entry_per_value"),
        ]

    def __str__(self):
        return f"{self.kind} {self.action}: {self.label}"


class FraudAlert(models.Model):
    """One alert to fraud analysts for each flagged payment (FR-FRD-08, UC-50).

    Created in the checkout's transaction when screening blocks a payment or asks for a
    verification code. The one-to-one link to the decision record is what keeps an event from
    raising a second alert: a later step on the same payment (a wrong code, a retry of the same
    submission) finds the alert that is already there. Unscreened attempts (screening
    unavailable) are a security matter for administrators (UC-38, UC-39), not a fraud alert.
    """

    public_id = models.CharField(max_length=32, unique=True, editable=False)  # fal_...
    screening = models.OneToOneField(ScreeningEvent, on_delete=models.PROTECT,
                                     related_name="alert")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.public_id} {self.screening_id}"

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = new_id("fal")
        super().save(*args, **kwargs)


class FraudAlertRead(models.Model):
    """When one staff member opened (or dismissed) an alert. Each analyst has their own
    unread list, so one analyst reading an alert does not hide it from the others."""

    alert = models.ForeignKey(FraudAlert, on_delete=models.CASCADE, related_name="reads")
    staff = models.ForeignKey("accounts.StaffAccount", on_delete=models.CASCADE,
                              related_name="+")
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["alert", "staff"], name="one_read_per_alert_staff"),
        ]


class RuleSetState(models.Model):
    """One row (pk=1): the current rule set version and the evaluation freeze (FR-FRD-02).

    `version` goes up by one with every change to the fraud rules; screening records it as
    rules_version "rules-0.<version>" (version 1 is the seeded set, which matches rules-0.1).
    While `frozen_since` is set an evaluation run is in progress and the rules cannot change.
    """

    version = models.PositiveIntegerField(default=1)
    frozen_since = models.DateTimeField(null=True, blank=True)
    frozen_by = models.CharField(max_length=32, blank=True)
    freeze_note = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"rules-0.{self.version}"

    @property
    def label(self) -> str:
        return f"rules-0.{self.version}"


class FraudRule(models.Model):
    """One version of an administrator-editable fraud rule (FR-FRD-02, UC-37).

    A rule is a condition on one screening feature (`feature op value`) and an action (block
    or ask for a verification code). Rows are never changed in place: editing or switching a
    rule off adds a new row with the next `revision` and closes the previous one
    (`superseded_at`), so every earlier version is kept. The current version of each rule has
    superseded_at = null; screening uses those that are enabled.
    """

    class Op(models.TextChoices):
        GT = "gt", ">"
        GTE = "gte", ">="
        LT = "lt", "<"
        LTE = "lte", "<="
        EQ = "eq", "="

    class Action(models.TextChoices):
        BLOCK = "block"
        VERIFY = "verify"

    rule_id = models.CharField(max_length=32, db_index=True)  # frl_..., same for every revision
    revision = models.PositiveIntegerField(default=1)
    code = models.SlugField(max_length=48)  # the reason recorded when it fires
    description = models.CharField(max_length=200, blank=True)
    feature = models.CharField(max_length=48)
    op = models.CharField(max_length=4, choices=Op.choices)
    value = models.FloatField()
    action = models.CharField(max_length=8, choices=Action.choices)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=32, blank=True)  # stf_..., blank when seeded
    superseded_at = models.DateTimeField(null=True, blank=True)
    ruleset_version = models.PositiveIntegerField()  # the RuleSetState.version it began

    class Meta:
        ordering = ["rule_id", "-revision"]
        constraints = [
            models.UniqueConstraint(fields=["rule_id", "revision"], name="fraud_rule_revision"),
            models.UniqueConstraint(fields=["rule_id"], condition=Q(superseded_at__isnull=True),
                                    name="fraud_rule_one_current"),
            models.CheckConstraint(condition=Q(op__in=["gt", "gte", "lt", "lte", "eq"]),
                                   name="fraud_rule_op_valid"),
            models.CheckConstraint(condition=Q(action__in=["block", "verify"]),
                                   name="fraud_rule_action_valid"),
        ]

    def __str__(self):
        return f"{self.rule_id} r{self.revision} {self.code}"


class RiskModel(models.Model):
    """A model trained by the learning loop (v2, docs/v2/LEARNING-LOOP.md).

    The gateway starts with the configured artefact (RISK_MODEL_VERSION). The learning loop
    trains a *challenger* from labelled screening history; it scores every attempt in the
    shadow (recorded, never decides) until an administrator promotes it to *champion*, which
    then decides. Rolling back retires the champion and brings back the one it replaced, or
    the configured artefact. At most one challenger and one champion exist at a time.

    The model file is kept here (`blob`) so every gateway process sees the same one; it is
    loaded only after its SHA-256, scikit-learn version and features are checked.
    """

    class Role(models.TextChoices):
        CHALLENGER = "challenger"
        CHAMPION = "champion"
        RETIRED = "retired"

    version = models.CharField(max_length=64, unique=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.CHALLENGER)
    manifest = models.JSONField()
    blob = models.BinaryField()
    sha256 = models.CharField(max_length=64)
    training = models.JSONField(default=dict, blank=True)  # data counts and holdout scores
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=32, blank=True)  # staff public ID or "command"
    promoted_at = models.DateTimeField(null=True, blank=True)
    promoted_by = models.CharField(max_length=32, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    # The champion this one replaced when promoted; None = the configured artefact.
    replaced = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+")
    replaced_configured = models.CharField(max_length=64, blank=True)  # its version, for show

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["role"], condition=Q(role="challenger"),
                                    name="one_challenger"),
            models.UniqueConstraint(fields=["role"], condition=Q(role="champion"),
                                    name="one_champion"),
        ]

    def __str__(self) -> str:
        return f"{self.version} ({self.role})"
