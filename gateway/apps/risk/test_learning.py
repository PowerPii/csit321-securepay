"""The learning loop (v2, docs/v2/LEARNING-LOOP.md): card-range features, a challenger trained
from red-team labels, shadow scoring, promotion and rollback."""
import random
from datetime import timedelta

import pytest
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.payments.models import Payment
from apps.redteam.models import RedTeamPayment, RedTeamRun
from apps.risk import learning, scoring
from apps.risk.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from apps.risk.models import RiskModel, ScreeningEvent
from apps.risk.screening import screen_payment
from conftest import current_totp, valid_body

pytestmark = pytest.mark.django_db(databases=["default", "vault"])

PAN = "4242420000000000"  # simulator demo range: BIN 424242


def features(**values):
    return {**{name: 0.0 for name in FEATURE_NAMES}, "amount_cents": 4590.0, **values}


@pytest.fixture
def run(shop):
    from apps.merchants.models import ApiKey

    key = ApiKey.objects.filter(merchant=shop.merchant).first()
    return RedTeamRun.objects.create(merchant=shop.merchant, api_key=key, scenario="bin_attack",
                                     token_hash="x" * 64,
                                     expires_at=timezone.now() + timedelta(hours=1))


def event(shop, feats, outcome="allow", kind=None, run=None, scenario="bin_attack",
          reasons=(), at=None):
    payment = Payment.create_for_merchant(shop.merchant, order_reference="LL", amount=4590,
                                          return_url="https://shop.test/r")
    if kind:
        RedTeamPayment.objects.create(run=run, payment=payment, kind=kind, scenario=scenario)
    return ScreeningEvent.objects.create(
        payment=payment, merchant=shop.merchant, occurred_at=at or timezone.now(),
        amount=4590, outcome=outcome, features=feats, reasons=list(reasons),
        feature_schema_version=FEATURE_SCHEMA_VERSION)


def history(shop, run, attacks=40, legit=30, genuine=30):
    """BIN attacks that slipped through (allowed), and ordinary shoppers."""
    rng = random.Random(7)  # nosec B311 - test data
    for _ in range(attacks):
        event(shop, features(bin_attempts_10m=rng.randint(8, 30),
                             bin_distinct_cards_1h=rng.randint(10, 60),
                             bin_distinct_ips_1h=rng.randint(10, 60)), kind="attack", run=run)
    for _ in range(legit):
        event(shop, features(amount_cents=rng.choice([1290, 5900, 12900])), kind="legit", run=run,
              scenario="legit")
    for _ in range(genuine):
        event(shop, features(amount_cents=rng.choice([2490, 3990, 8900]),
                             bin_attempts_10m=rng.randint(0, 1)))


def fresh_code(account):
    TOTPDevice.objects.filter(user=account.user).update(last_t=-1)
    return current_totp(account)


# --- the card range reaches screening ---------------------------------------------------------

def test_the_card_range_is_kept_and_counted(shop, fake_sim):
    for i in range(3):
        r = shop.post("/v1/payments", valid_body(), format="json",
                      headers={"Idempotency-Key": f"card-range-{i}-0000"})
        assert r.status_code == 201, r.content
        payment = Payment.objects.get(public_id=r.json()["id"])
        base = f"/checkout/{payment.checkout_session_id}"
        browser = APIClient()
        card = PAN[:-2] + ["00", "18", "26"][i]  # three cards of one range, all Luhn-valid
        browser.post(f"{base}/card", {"number": card, "expiry_month": 12, "expiry_year": 2030,
                                      "cvc": "123", "privacy_consent": True}, format="json")
        browser.post(f"{base}/submit", {}, format="json")
    last = ScreeningEvent.objects.latest("occurred_at")
    assert last.card_bin == "424242" and last.payment.card_bin == "424242"
    assert last.features["bin_attempts_10m"] == 2
    assert last.features["bin_distinct_cards_1h"] == 2
    assert last.feature_schema_version == "fs-0.3"


def test_models_trained_on_fs02_still_load():
    from apps.risk.features import COMPATIBLE_SCHEMAS

    assert COMPATIBLE_SCHEMAS == ("fs-0.1", "fs-0.2", "fs-0.3")  # the live model is fs-0.2


# --- training ---------------------------------------------------------------------------------

def test_a_challenger_needs_enough_labels(shop, run):
    history(shop, run, attacks=5)
    with pytest.raises(Exception) as err:
        learning.train_challenger("test")
    assert err.value.code == "not_enough_labels"
    assert not RiskModel.objects.exists()


def test_a_challenger_learns_the_bin_attack_the_champion_missed(shop, run):
    history(shop, run)
    row = learning.train_challenger("test")
    assert row.role == "challenger" and row.version.startswith("ll-")
    assert row.training["attacks"] == 40 and row.training["genuine"] == 60
    h = row.training["holdout"]
    assert h["champion"]["attacks_stopped"] == 0  # they all slipped through
    assert h["challenger"]["caught_rate"] >= 0.9
    assert h["challenger"]["genuine_stopped"] == 0
    assert h["by_scenario"]["bin_attack"]["attacks"] == h["attacks"]
    # Stored with a manifest the loader checks, and loadable.
    model = scoring.load_blob(row.manifest, row.blob)
    assert model.score(features(bin_attempts_10m=20, bin_distinct_cards_1h=40,
                                bin_distinct_ips_1h=40)) > 0.9
    assert model.score(features()) < 0.5


def test_stopped_attempts_without_a_label_are_left_out(shop, run):
    event(shop, features(), outcome="block", reasons=["card_attempt_burst"])
    event(shop, features(), outcome="allow")
    rows = learning.labelled_rows()
    assert [(r.label, r.scenario) for r in rows] == [(0, "genuine")]


def test_rule_hits_are_read_back_from_the_decision_record():
    row = learning.Row({}, 1, "x", "block", ["card_attempt_burst"])
    assert learning.rule_outcome(row).value == "block"
    row = learning.Row({}, 1, "x", "block", ["high_amount", "model_score_high"])
    assert learning.rule_outcome(row).value == "verify"
    row = learning.Row({}, 1, "x", "verify", ["model_score_medium"])
    assert learning.rule_outcome(row) is None


def test_a_new_challenger_retires_the_old_one(shop, run):
    history(shop, run)
    first = learning.train_challenger("test")
    second = learning.train_challenger("test")
    first.refresh_from_db()
    assert first.role == "retired" and second.role == "challenger"


def test_a_tampered_model_is_not_loaded(shop, run):
    history(shop, run)
    row = learning.train_challenger("test")
    with pytest.raises(scoring.ArtifactError):
        scoring.load_blob(row.manifest, bytes(row.blob) + b"x")


# --- the shadow -------------------------------------------------------------------------------

def test_the_challenger_scores_in_the_shadow_but_never_decides(shop, run):
    history(shop, run)
    row = learning.train_challenger("test")
    for _ in range(12):  # a burst of cards from one range, as a BIN attack makes
        event(shop, features(), at=timezone.now() - timedelta(seconds=30))
    ScreeningEvent.objects.filter(features__amount_cents=4590.0).update(card_bin="424242")
    for i, e in enumerate(ScreeningEvent.objects.filter(card_bin="424242")):
        e.card_fingerprint, e.ip_hash = f"card{i}", f"ip{i}"
        e.save(update_fields=["card_fingerprint", "ip_hash"])
    payment = Payment.create_for_merchant(shop.merchant, order_reference="NEW", amount=4590,
                                          return_url="https://shop.test/r")
    payment.card_bin, payment.card_fingerprint = "424242", "new-card"
    payment.save()
    screening = screen_payment(payment, ip="100.64.0.1")
    e = screening.event
    assert e.outcome == "allow"  # the stub champion decided
    assert e.shadow_model_version == row.version
    assert e.shadow_outcome == "block" and e.shadow_score > 0.9
    stats = learning.shadow_stats(row.version)
    assert stats["screened"] == 1 and stats["differs"] == 1


def test_no_challenger_no_shadow(shop):
    payment = Payment.create_for_merchant(shop.merchant, order_reference="NEW", amount=4590,
                                          return_url="https://shop.test/r")
    e = screen_payment(payment, ip="100.64.0.1").event
    assert e.shadow_model_version == "" and e.shadow_score is None


# --- staff API: train, promote, roll back -----------------------------------------------------

def test_staff_train_then_an_administrator_promotes_and_rolls_back(shop, run, staff_client):
    history(shop, run)
    analyst = staff_client("fraud_analyst")
    r = analyst.get("/internal/risk-models")
    assert r.status_code == 200
    assert r.json()["active"] == {"version": "stub-0", "role": "champion",
                                  "source": "configured",
                                  "policy": {"verify": 0.5, "block": 0.9}}
    assert r.json()["data"]["ready"] is True and r.json()["challenger"] is None

    r = analyst.post("/internal/risk-models/train", {}, content_type="application/json")
    assert r.status_code == 201, r.content
    version = r.json()["challenger"]["version"]
    assert AuditEvent.objects.filter(action="risk_model.trained", target_id=version).exists()
    # An analyst cannot change which model decides.
    r = analyst.post("/internal/risk-models/promote", {"version": version, "code": "000000"},
                     content_type="application/json")
    assert r.status_code == 403

    admin = staff_client("administrator")
    r = admin.post("/internal/risk-models/promote", {"version": version, "code": "000000"},
                   content_type="application/json")
    assert r.status_code == 403 and r.json()["error"]["code"] == "code_incorrect"
    assert scoring.active_model().version == "stub-0"
    r = admin.post("/internal/risk-models/promote",
                   {"version": version, "code": fresh_code(admin.account)},
                   content_type="application/json")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["active"]["version"] == version and body["active"]["source"] == "promoted"
    assert body["active"]["replaced"] == "stub-0" and body["challenger"] is None
    assert scoring.active_model().version == version  # screening uses it now
    assert AuditEvent.objects.get(action="risk_model.promoted").details["replaced"] == "stub-0"

    r = admin.post("/internal/risk-models/rollback", {"code": fresh_code(admin.account)},
                   content_type="application/json")
    assert r.status_code == 200, r.content
    assert r.json()["active"]["version"] == "stub-0"
    assert scoring.active_model().version == "stub-0"
    assert RiskModel.objects.get(version=version).role == "retired"
    r = admin.post("/internal/risk-models/rollback", {"code": fresh_code(admin.account)},
                   content_type="application/json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "nothing_to_roll_back"


def test_rolling_back_brings_back_the_previous_champion(shop, run):
    history(shop, run)
    first = learning.train_challenger("test")
    learning.promote(first.version, "stf_x")
    second = learning.train_challenger("test")
    learning.promote(second.version, "stf_x")
    retired, now = learning.rollback("stf_x")
    assert (retired.version, now) == (second.version, first.version)
    assert scoring.active_model().version == first.version


def test_only_the_challenger_can_be_promoted(shop, run):
    history(shop, run)
    row = learning.train_challenger("test")
    learning.train_challenger("test")
    with pytest.raises(Exception) as err:
        learning.promote(row.version, "stf_x")
    assert err.value.code == "not_the_challenger"


def test_operators_cannot_see_the_models(shop, staff_client):
    operator = staff_client("merchant_operator", merchant=shop.merchant)
    assert operator.get("/internal/risk-models").status_code == 403
    assert APIClient().get("/internal/risk-models").status_code == 401


def test_the_red_team_live_view_shows_the_challengers_verdict(shop, run, staff_client):
    history(shop, run, attacks=40)
    row = learning.train_challenger("test")
    ScreeningEvent.objects.filter(payment__red_team__kind="attack").update(
        shadow_model_version=row.version, shadow_outcome="block", shadow_score=0.99)
    from apps.redteam import service

    shadow = service.live(run)["shadow"]
    assert shadow["version"] == row.version
    assert (shadow["attacks"], shadow["attacks_stopped"], shadow["caught_rate"]) == (40, 40, 1.0)
