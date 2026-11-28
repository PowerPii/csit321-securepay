"""Fraud screening pipeline: features -> rules -> model -> policy (architecture section 7).

Runs synchronously when the customer pays, before anything reaches the simulator, and only
uses information available at that moment. Every stage is swappable for tests and for the
trained model:

    features  apps.risk.features.SqlFeatures   prior-activity counts and card history (fs-0.2)
    lists     DbRiskLists                      seeded block / review lists
    rules     Rules / DbRules                  deterministic, versioned; editable by
                                               administrators (apps.risk.rulebook, FR-FRD-02)
    model     apps.risk.scoring.active_model() StubModel until an artefact is installed
    policy    Policy                           versioned thresholds; an artefact may ship its own

Any failure, or a run longer than the budget (GATEWAY_RULES["SCREENING_TIMEOUT_MS"], 500 ms
by FR-FRD-04), raises ScreeningUnavailable. The caller blocks the attempt as
"screening_unavailable", never as fraud.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from django.conf import settings

from apps.risk import explain
from apps.risk.features import FEATURE_SCHEMA_VERSION, SqlFeatures
from apps.risk.scoring import Model, Thresholds, active_model

logger = logging.getLogger(__name__)

RULES_VERSION = "rules-0.1"
DEFAULT_POLICY = Thresholds(version="policy-0.1", verify_threshold=0.5, block_threshold=0.9)


class Outcome(StrEnum):
    ALLOW = "allow"
    VERIFY = "verify"  # simulated verification code (FR-CHK-05)
    BLOCK = "block"


@dataclass(frozen=True)
class Attempt:
    """What the pipeline sees. Never a card number: the vault fingerprint and keyed hashes."""

    attempt_id: str
    merchant_id: str
    amount_cents: int
    card_fingerprint: str
    occurred_at: datetime
    email_hash: str = ""
    ip_hash: str = ""
    device_hash: str = ""
    card_bin: str = ""  # fs-0.3: first 6 digits of the card (issuer range)


@dataclass
class Decision:
    outcome: Outcome
    score: float | None
    reasons: list[str] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)
    model_version: str | None = None
    rules_version: str = RULES_VERSION
    policy_version: str = DEFAULT_POLICY.version
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    elapsed_ms: float = 0.0
    by_rule: bool = False  # a rule (not the model) caused a block or a verification
    explanation: dict = field(default_factory=dict)  # apps.risk.explain; {} if it failed
    # v2 learning loop: what the challenger would have decided. Recorded, never applied.
    shadow_model_version: str = ""
    shadow_score: float | None = None
    shadow_outcome: str = ""

    @property
    def block_reason(self) -> str:
        """The payment's outcome reason when this decision blocks (contract OutcomeReason)."""
        if self.by_rule:
            return "amount_limit" if self.reasons == ["amount_over_limit"] else "fraud_rule"
        return "fraud_model"


class FeatureBuilder(Protocol):
    def build(self, attempt: Attempt) -> dict[str, float]: ...


@dataclass(frozen=True)
class ListHit:
    kind: str  # card, email, ip
    action: str  # block or verify
    label: str


class RiskLists(Protocol):
    def lookup(self, attempt: Attempt) -> list[ListHit]: ...


class DbRiskLists:
    def lookup(self, attempt: Attempt) -> list[ListHit]:
        from django.db.models import Q

        from apps.risk.models import RiskListEntry

        keys = [("card", attempt.card_fingerprint), ("email", attempt.email_hash),
                ("ip", attempt.ip_hash)]
        query = Q()
        for kind, value in keys:
            if value:
                query |= Q(kind=kind, value_hash=value)
        if not query:
            return []
        return [ListHit(kind, action, label) for kind, action, label in
                RiskListEntry.objects.filter(query, active=True)
                .values_list("kind", "action", "label")]


@dataclass(frozen=True)
class RuleSpec:
    """One editable rule: `feature op value` -> action, recorded as reason `code`."""

    code: str
    feature: str
    op: str  # gt, gte, lt, lte, eq
    value: float
    action: str  # block or verify

    def matches(self, value: float) -> bool:
        return {"gt": value > self.value, "gte": value >= self.value, "lt": value < self.value,
                "lte": value <= self.value, "eq": value == self.value}[self.op]


# rules-0.1 as it was hard-coded in the MVP; the seeded rule set in the database is the same.
DEFAULT_RULES = (
    RuleSpec("card_attempt_burst", "card_attempts_10m", "gte", 5, "block"),
    RuleSpec("high_amount", "amount_cents", "gt", 50_000, "verify"),
)


@dataclass
class Rules:
    """Block and review lists and the amount ceiling (fixed), then the editable rules.

    `items` defaults to rules-0.1; screening uses DbRules, which reads the administrators'
    current rule set (FR-FRD-02)."""

    items: tuple[RuleSpec, ...] = DEFAULT_RULES
    version: str = RULES_VERSION

    def _load(self) -> None:
        """Hook for DbRules; called inside the pipeline's error handling."""

    def evaluate(self, attempt: Attempt, features: dict[str, float],
                 hits: list[ListHit]) -> tuple[Outcome | None, list[str]]:
        self._load()
        blocks, verifies = [], []
        for hit in hits:
            (blocks if hit.action == "block" else verifies).append(
                f"{'blocklisted' if hit.action == 'block' else 'review_list'}_{hit.kind}")
        if attempt.amount_cents > settings.GATEWAY_RULES["MAX_AMOUNT_CENTS"]:
            blocks.append("amount_over_limit")
        values = {**features, "amount_cents": float(attempt.amount_cents)}
        for rule in self.items:
            if rule.feature in values and rule.matches(values[rule.feature]):
                (blocks if rule.action == "block" else verifies).append(rule.code)
        if blocks:
            return Outcome.BLOCK, sorted(set(blocks))
        if verifies:
            return Outcome.VERIFY, sorted(set(verifies))
        return None, []


@dataclass
class Policy:
    thresholds: Thresholds = DEFAULT_POLICY

    @property
    def version(self) -> str:
        return self.thresholds.version

    def decide(self, rule_outcome: Outcome | None, score: float) -> tuple[Outcome, list[str]]:
        if rule_outcome is Outcome.BLOCK:
            return Outcome.BLOCK, []
        if score >= self.thresholds.block_threshold:
            return Outcome.BLOCK, ["model_score_high"]
        if rule_outcome is Outcome.VERIFY:
            return Outcome.VERIFY, []
        if score >= self.thresholds.verify_threshold:
            return Outcome.VERIFY, ["model_score_medium"]
        return Outcome.ALLOW, []


class DbRules(Rules):
    """The current rule set from the database, read once per screening."""

    def _load(self) -> None:
        from apps.risk.rulebook import current_rule_set

        self.version, self.items = current_rule_set()


class ScreeningUnavailable(Exception):
    pass


@dataclass
class Pipeline:
    features: FeatureBuilder = field(default_factory=SqlFeatures)
    lists: RiskLists = field(default_factory=DbRiskLists)
    rules: Rules = field(default_factory=Rules)
    model: Model = field(default_factory=active_model)
    policy: Policy | None = None  # None: the model's own thresholds, else policy-0.1
    shadow: Model | None = None  # the learning loop's challenger, scored but never deciding

    def _shadow(self, rule_outcome: Outcome | None, feats: dict[str, float]):
        """The challenger's score and outcome under its own thresholds, or None. A failing
        challenger never affects the decision."""
        if self.shadow is None:
            return None
        try:
            score = float(self.shadow.score(feats))
            if not 0.0 <= score <= 1.0:
                raise ValueError(f"shadow score {score} outside [0, 1]")
            outcome, _ = Policy(self.shadow.policy or DEFAULT_POLICY).decide(rule_outcome, score)
        except Exception:
            logger.exception("Challenger %s could not score",
                             getattr(self.shadow, "version", "?"))
            return None
        return self.shadow.version, round(score, 6), outcome.value

    def screen(self, attempt: Attempt) -> Decision:
        budget_ms = settings.GATEWAY_RULES["SCREENING_TIMEOUT_MS"]
        start = time.perf_counter()
        try:
            feats = self.features.build(attempt)
            hits = self.lists.lookup(attempt)
            rule_outcome, reasons = self.rules.evaluate(attempt, feats, hits)
            score = float(self.model.score(feats))
            if not 0.0 <= score <= 1.0:
                raise ValueError(f"model score {score} outside [0, 1]")
        except Exception as exc:  # any failure stops before a financial effect
            raise ScreeningUnavailable(type(exc).__name__) from exc
        policy = self.policy or Policy(self.model.policy or DEFAULT_POLICY)
        outcome, more = policy.decide(rule_outcome, score)
        # Explaining is part of the decision record but never decides anything: if it fails the
        # decision stands without it. It runs inside the timed budget (NFR-PER-02).
        try:
            why = explain.explain(
                outcome=outcome.value, reasons=reasons + more, features=feats,
                amount_cents=attempt.amount_cents, score=score,
                contributions=explain_model(self.model, feats),
                rules={r.code: (r.feature, r.op, r.value) for r in self.rules.items},
                max_amount=settings.GATEWAY_RULES["MAX_AMOUNT_CENTS"])
        except Exception:
            logger.exception("Could not explain screening of %s", attempt.attempt_id)
            why = {}
        shadow = self._shadow(rule_outcome, feats)
        elapsed = (time.perf_counter() - start) * 1000
        if elapsed > budget_ms:
            raise ScreeningUnavailable(f"timeout after {elapsed:.0f} ms (budget {budget_ms} ms)")
        return Decision(
            outcome=outcome,
            score=score,
            reasons=reasons + more,
            features=feats,
            model_version=self.model.version,
            rules_version=self.rules.version,
            policy_version=policy.version,
            elapsed_ms=round(elapsed, 2),
            by_rule=outcome is not Outcome.ALLOW and not more,
            explanation=why,
            **(dict(zip(("shadow_model_version", "shadow_score", "shadow_outcome"), shadow,
                        strict=True)) if shadow else {}),
        )


def explain_model(model: Model, features: dict[str, float]):
    """The model's TreeSHAP contributions, or None for a model that cannot explain (stub)."""
    method = getattr(model, "explain", None)
    return method(features) if method else None
