"""Pipeline stages without the database: rules, policy, model adapter, failure handling."""
import time
from datetime import UTC, datetime

import pytest

from apps.risk.pipeline import (
    Attempt,
    ListHit,
    Outcome,
    Pipeline,
    Policy,
    Rules,
    ScreeningUnavailable,
)
from apps.risk.scoring import StubModel, Thresholds


class NoFeatures:
    def __init__(self, **extra):
        self.extra = extra

    def build(self, attempt):
        return {"amount_cents": float(attempt.amount_cents), **self.extra}


class Lists:
    def __init__(self, *hits):
        self.hits = list(hits)

    def lookup(self, attempt):
        return self.hits


class FixedModel:
    policy = None

    def __init__(self, score, version="fixed-1", policy=None):
        self.value, self.version, self.policy = score, version, policy

    def score(self, features):
        return self.value


def attempt(**kw):
    base = {"attempt_id": "pay_1", "merchant_id": "mer_1", "amount_cents": 1_000,
            "card_fingerprint": "fp", "occurred_at": datetime(2026, 11, 13, tzinfo=UTC)}
    return Attempt(**{**base, **kw})


def pipeline(features=None, lists=None, model=None, **kw):
    return Pipeline(features=features or NoFeatures(), lists=lists or Lists(),
                    model=model or StubModel(), **kw)


def test_low_risk_is_allowed_and_versions_are_recorded():
    d = pipeline().screen(attempt())
    assert d.outcome is Outcome.ALLOW
    assert (d.model_version, d.rules_version, d.policy_version, d.feature_schema_version) == (
        "stub-0", "rules-0.1", "policy-0.1", "fs-0.3")
    assert d.score == 0.0 and d.reasons == []


def test_blocklisted_card_is_blocked_by_rule_with_reason():
    d = pipeline(lists=Lists(ListHit("card", "block", "stolen"))).screen(attempt())
    assert d.outcome is Outcome.BLOCK
    assert d.reasons == ["blocklisted_card"]
    assert d.block_reason == "fraud_rule"


def test_review_list_email_asks_for_verification():
    d = pipeline(lists=Lists(ListHit("email", "verify", "watch"))).screen(attempt())
    assert (d.outcome, d.reasons) == (Outcome.VERIFY, ["review_list_email"])


def test_burst_of_attempts_on_one_card_is_blocked():
    assert pipeline(features=NoFeatures(card_attempts_10m=4)).screen(
        attempt()).outcome is Outcome.ALLOW
    d = pipeline(features=NoFeatures(card_attempts_10m=5)).screen(attempt())
    assert (d.outcome, d.reasons) == (Outcome.BLOCK, ["card_attempt_burst"])


def test_high_amount_requires_verification():
    assert pipeline().screen(attempt(amount_cents=50_000)).outcome is Outcome.ALLOW
    d = pipeline().screen(attempt(amount_cents=50_001))
    assert (d.outcome, d.reasons) == (Outcome.VERIFY, ["high_amount"])


def test_block_beats_verify_and_all_block_reasons_are_kept():
    d = pipeline(lists=Lists(ListHit("card", "block", "x"), ListHit("email", "verify", "y")),
                 features=NoFeatures(card_attempts_10m=9)).screen(attempt(amount_cents=60_000))
    assert d.outcome is Outcome.BLOCK
    assert d.reasons == ["blocklisted_card", "card_attempt_burst"]


@pytest.mark.parametrize(("score", "outcome", "reasons"), [
    (0.49, Outcome.ALLOW, []),
    (0.5, Outcome.VERIFY, ["model_score_medium"]),
    (0.9, Outcome.BLOCK, ["model_score_high"]),
])
def test_policy_maps_model_score_to_actions(score, outcome, reasons):
    d = pipeline(model=FixedModel(score)).screen(attempt())
    assert (d.outcome, d.reasons, d.score) == (outcome, reasons, score)
    if outcome is Outcome.BLOCK:
        assert d.block_reason == "fraud_model"


def test_model_block_on_top_of_a_verify_rule_is_a_model_block():
    d = pipeline(model=FixedModel(0.95)).screen(attempt(amount_cents=60_000))
    assert d.reasons == ["high_amount", "model_score_high"]
    assert d.block_reason == "fraud_model"


def test_artefact_thresholds_replace_the_default_policy():
    model = FixedModel(0.3, policy=Thresholds("policy-hgb-1", 0.25, 0.8))
    d = pipeline(model=model).screen(attempt())
    assert (d.outcome, d.policy_version) == (Outcome.VERIFY, "policy-hgb-1")
    # An explicit policy wins over the model's.
    d = pipeline(model=model, policy=Policy()).screen(attempt())
    assert (d.outcome, d.policy_version) == (Outcome.ALLOW, "policy-0.1")


@pytest.mark.parametrize("model", [
    type("Broken", (), {"version": "x", "policy": None,
                        "score": lambda self, f: (_ for _ in ()).throw(RuntimeError("down"))})(),
    FixedModel(float("nan")),
    FixedModel(1.5),
])
def test_model_failure_or_nonsense_is_screening_unavailable_not_fraud(model):
    with pytest.raises(ScreeningUnavailable):
        pipeline(model=model).screen(attempt())


def test_slow_screening_is_unavailable(settings):
    settings.GATEWAY_RULES = {**settings.GATEWAY_RULES, "SCREENING_TIMEOUT_MS": 20}

    class Slow(NoFeatures):
        def build(self, attempt):
            time.sleep(0.05)
            return super().build(attempt)

    with pytest.raises(ScreeningUnavailable, match="timeout"):
        pipeline(features=Slow()).screen(attempt())


def test_rules_never_see_card_numbers():
    d = pipeline().screen(attempt())
    assert not any("pan" in k or "number" in k for k in d.features)
    assert set(Attempt.__dataclass_fields__) >= {"card_fingerprint"}
    assert not {"card_number", "pan", "cvc"} & set(Attempt.__dataclass_fields__)


def test_rules_are_ordered_deterministically():
    rules = Rules()
    hits = [ListHit("email", "block", "b"), ListHit("card", "block", "a")]
    assert rules.evaluate(attempt(), {}, hits)[1] == ["blocklisted_card", "blocklisted_email"]


def test_seed_relabels_the_old_blocklist_entry_without_the_card_number(db):
    """NFR-CON-02: an older seed showed the whole test card number in the label."""
    from django.core.management import call_command

    from apps.risk.models import RiskListEntry

    call_command("seed_risk_lists", verbosity=0)
    old = "Demo: test card 4000 0000 0000 0101 reported stolen"
    RiskListEntry.objects.filter(kind="card").update(label=old, display=old[:60])
    call_command("seed_risk_lists", verbosity=0)
    entry = RiskListEntry.objects.get(kind="card")
    assert entry.label == entry.display == "Demo: test card ending 0101 reported stolen"
