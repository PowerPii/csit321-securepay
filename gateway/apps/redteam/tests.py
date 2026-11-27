"""Red team (v2, docs/v2/RED-TEAM.md): runs, labels, bot addresses, the live score."""
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.merchants.models import ApiKey
from apps.payments.models import Payment
from apps.redteam import service
from apps.redteam.models import RedTeamPayment, RedTeamRun
from apps.risk.features import FEATURE_NAMES
from apps.risk.hashing import ip_hash
from conftest import MerchantClient, valid_body

pytestmark = pytest.mark.django_db(databases=["default", "vault"])

PAN = "4242420000000000"  # simulator demo range 4242 4200


@pytest.fixture(autouse=True)
def red_team_on(settings):
    settings.REDTEAM_ENABLED = True


def start(shop, scenario="card_testing"):
    r = shop.post("/v1/red_team/runs", {"scenario": scenario}, format="json")
    assert r.status_code == 201, r.content
    return r.json()


def create(shop, run, kind="attack", bot="bot-1", **body):
    headers = {"Idempotency-Key": str(uuid.uuid4()), "Red-Team-Run": run["id"],
               "Red-Team-Kind": kind, "Red-Team-Bot": bot}
    return shop.post("/v1/payments", valid_body(**body), format="json", headers=headers)


def pay(shop, run, ip, card=PAN, token=None, kind="attack", **body):
    r = create(shop, run, kind=kind, **body)
    assert r.status_code == 201, r.content
    payment = Payment.objects.get(public_id=r.json()["id"])
    base = f"/checkout/{payment.checkout_session_id}"
    browser = APIClient()
    headers = {"Red-Team-Token": token or run["token"], "Red-Team-IP": ip}
    browser.post(f"{base}/card", {"number": card, "expiry_month": 12, "expiry_year": 2030,
                                  "cvc": "123", "privacy_consent": True}, format="json",
                 headers=headers)
    data = browser.post(f"{base}/submit", {}, format="json", headers=headers).json()
    return payment, data


# --- starting and ending runs ----------------------------------------------------------------

def test_a_run_starts_with_a_test_key_and_gives_its_token_once(shop):
    run = start(shop)
    assert run["id"].startswith("rt_") and run["live"] is True and len(run["token"]) > 30
    stored = RedTeamRun.objects.get(public_id=run["id"])
    assert run["token"] not in stored.token_hash  # only an HMAC is kept
    r = shop.post(f"/v1/red_team/runs/{run['id']}/end", {}, format="json")
    assert r.status_code == 200 and r.json()["live"] is False and "token" not in r.json()


def test_the_red_team_is_off_unless_enabled(shop, settings):
    settings.REDTEAM_ENABLED = False
    r = shop.post("/v1/red_team/runs", {"scenario": "card_testing"}, format="json")
    assert r.status_code == 403 and r.json()["error"]["code"] == "red_team_disabled"


def test_a_live_key_cannot_start_a_run(shop):
    _, plaintext = ApiKey.issue(shop.merchant, livemode=True)
    live = MerchantClient(shop.merchant, None, plaintext, shop.secret)
    r = live.post("/v1/red_team/runs", {"scenario": "card_testing"}, format="json")
    assert r.status_code == 403 and r.json()["error"]["code"] == "red_team_test_mode_only"


@pytest.mark.parametrize("body", [{}, {"scenario": "Card Testing"}, {"scenario": "x" * 41},
                                  {"scenario": "ok", "extra": 1}])
def test_bad_run_requests_are_refused(shop, body):
    assert shop.post("/v1/red_team/runs", body, format="json").status_code == 400


# --- labelling payments ----------------------------------------------------------------------

def test_payments_of_a_run_are_labelled_but_look_ordinary(shop, payment_schema):
    run = start(shop)
    r = create(shop, run, kind="legit", bot="shopper-3")
    assert r.status_code == 201
    payment_schema.validate(r.json())  # nothing about the red team in the merchant's view
    label = RedTeamPayment.objects.get(payment__public_id=r.json()["id"])
    assert (label.kind, label.scenario, label.bot) == ("legit", "card_testing", "shopper-3")


def test_an_unknown_ended_or_other_merchants_run_is_refused(shop, other_shop):
    run = start(shop)
    assert create(other_shop, run).status_code == 400
    assert create(shop, {"id": "rt_nope"}).status_code == 400
    shop.post(f"/v1/red_team/runs/{run['id']}/end", {}, format="json")
    r = create(shop, run)
    assert r.status_code == 400 and r.json()["error"]["code"] == "red_team_run_invalid"
    assert not Payment.objects.exists()


def test_an_expired_run_is_refused(shop):
    run = start(shop)
    RedTeamRun.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert create(shop, run).status_code == 400


def test_a_run_has_a_payment_cap(shop, settings):
    settings.GATEWAY_RULES = {**settings.GATEWAY_RULES, "REDTEAM_MAX_PAYMENTS": 2}
    run = start(shop)
    assert create(shop, run).status_code == 201 and create(shop, run).status_code == 201
    r = create(shop, run)
    assert r.status_code == 429 and r.json()["error"]["code"] == "red_team_run_full"


# --- where a bot comes from ---------------------------------------------------------------------

def test_screening_sees_the_bots_address(shop, fake_sim):
    run = start(shop)
    payment, data = pay(shop, run, "100.64.1.2")
    assert data["status"] == "authorised"
    assert payment.screening.ip_hash == ip_hash("100.64.1.2")


def test_the_address_header_is_ignored_without_the_runs_token(shop, fake_sim):
    run = start(shop)
    payment, _ = pay(shop, run, "100.64.1.2", token="not-the-token")
    assert payment.screening.ip_hash != ip_hash("100.64.1.2")


def test_another_runs_token_does_not_vouch_for_this_payment(shop, fake_sim):
    run, other = start(shop), start(shop, "bin_attack")
    payment, _ = pay(shop, run, "100.64.1.2", token=other["token"])
    assert payment.screening.ip_hash != ip_hash("100.64.1.2")


def test_the_address_header_is_ignored_when_the_red_team_is_off(shop, fake_sim, settings):
    run = start(shop)
    r = create(shop, run)
    settings.REDTEAM_ENABLED = False
    payment = Payment.objects.get(public_id=r.json()["id"])
    request = APIClient().get("/").wsgi_request
    request.META["HTTP_RED_TEAM_TOKEN"], request.META["HTTP_RED_TEAM_IP"] = run["token"], "1.2.3.4"
    assert service.bot_ip(request, payment) is None


def test_each_bot_is_rate_limited_on_its_own_address(shop):
    from apps.common.throttling import AnonRateThrottle

    run = start(shop)
    request = APIClient().get("/").wsgi_request
    request.META["HTTP_RED_TEAM_TOKEN"], request.META["HTTP_RED_TEAM_IP"] = run["token"], "100.64.0.9"
    assert AnonRateThrottle().get_ident(request) == "red-team:100.64.0.9"
    wrong = APIClient().get("/").wsgi_request
    wrong.META["HTTP_RED_TEAM_TOKEN"], wrong.META["HTTP_RED_TEAM_IP"] = "wrong", "100.64.0.9"
    assert AnonRateThrottle().get_ident(wrong) != "red-team:100.64.0.9"


def test_screening_never_reads_the_labels():
    assert not any("red" in name or "attack" in name for name in FEATURE_NAMES)


# --- the live score ---------------------------------------------------------------------------------

def test_a_card_testing_burst_is_caught_and_scored_live(shop, fake_sim, staff_client):
    run = start(shop)
    outcomes = [pay(shop, run, "100.64.3.4", amount=100)[1]["status"] for _ in range(7)]
    assert outcomes[:5] == ["authorised"] * 5 and outcomes[5:] == ["blocked"] * 2
    pay(shop, run, "100.64.9.9", card="5555550000000002", kind="legit")

    analyst = staff_client("fraud_analyst")
    data = analyst.get(f"/internal/red-team/runs/{run['id']}").json()
    totals = data["totals"]
    assert (totals["attacks"], totals["stopped"], totals["legit"]) == (7, 2, 1)
    assert totals["caught_rate"] == round(2 / 7, 4) and totals["false_positive_rate"] == 0
    assert sum(b["attacks"] for b in data["buckets"]) == 7
    assert sum(b["attacks_blocked"] for b in data["buckets"]) == 2
    (scenario,) = data["scenarios"]
    assert scenario == {"scenario": "card_testing", "attacks": 7, "blocked": 2, "verify": 0,
                        "allowed": 5, "pending": 0}
    runs = analyst.get("/internal/red-team/runs").json()
    assert runs["enabled"] is True and runs["data"][0]["attacks"] == 7


def test_only_analysts_and_administrators_see_the_red_team(shop, staff_client):
    run = start(shop)
    operator = staff_client("merchant_operator", merchant=shop.merchant)
    assert operator.get("/internal/red-team/runs").status_code == 403
    assert operator.get(f"/internal/red-team/runs/{run['id']}").status_code == 403
    assert staff_client("administrator").get("/internal/red-team/runs").status_code == 200
    assert APIClient().get("/internal/red-team/runs").status_code == 401
