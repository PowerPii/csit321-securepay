"""v2 3-D Secure: a risky payment is challenged by the card's bank (docs/v2/THREE-DS.md)."""
# ruff: noqa: F811 (pytest fixtures imported from test_screening)
import pytest

from apps.audit.models import AuditEvent
from apps.checkout.models import VerificationChallenge
from apps.checkout.test_screening import (  # noqa: F401 (fixtures)
    BIG,
    browser,
    mailoutbox,
    make_payment,
    pay,
    url,
)
from apps.payments.models import Payment, PaymentStatus

pytestmark = pytest.mark.django_db(databases=["default", "vault"], transaction=True)

ENROLLED = "4000000000003220"


def paths(fake_sim):
    return [p for _, p, _ in fake_sim.requests]


def test_the_bank_challenges_a_risky_payment_and_its_proof_goes_with_the_authorisation(
        browser, make_payment, fake_sim, mailoutbox):
    payment = make_payment(amount=BIG)
    data = pay(browser, payment, ENROLLED).json()
    assert data["status"] == "verification_required"
    v = data["verification"]
    assert v["method"] == "three_ds" and v["passkey"] is False and v["resend_available_at"] is None
    assert v["issuer_challenge"] == {"bank": "SecurePay Bank", "card_last4": "3220",
                                     "phone_hint": "•••• 21", "simulated_text_code": "135790"}
    assert len(mailoutbox) == 0  # the bank texts the code; we send no email
    sent = fake_sim.requests[0][2]
    assert paths(fake_sim) == ["/sim/3ds/authenticate"]
    assert sent["amount"] == BIG and "cvc" not in sent["card"]
    assert browser.post(url(payment, "/verify/resend"), {}, format="json").status_code == 409

    wrong = browser.post(url(payment, "/verify"), {"code": "000000"}, format="json")
    assert wrong.status_code == 400 and "2 attempts left" in wrong.json()["error"]["message"]
    data = browser.post(url(payment, "/verify"), {"code": "135790"}, format="json").json()
    assert (data["status"], data["result"]) == ("authorised", "approved")
    authorise = next(b for _, p, b in fake_sim.requests if p == "/sim/authorise")
    assert authorise["authentication"] == {"three_ds_id": "3ds_" + "0" * 24, "eci": "05",
                                           "cavv": "Q" * 27 + "="}
    challenge = VerificationChallenge.objects.get(payment=payment)
    assert (challenge.three_ds_status, challenge.three_ds_eci, challenge.three_ds_test_code) == (
        "authenticated", "05", "")
    verified = AuditEvent.objects.get(target_id=payment.public_id, action="payment.verified")
    assert verified.details["method"] == "three_ds" and "cavv" not in verified.details


def test_three_wrong_codes_at_the_bank_block_the_payment(browser, make_payment, fake_sim):
    payment = make_payment(amount=BIG)
    pay(browser, payment, ENROLLED)
    for _ in range(2):
        assert browser.post(url(payment, "/verify"), {"code": "000000"},
                            format="json").status_code == 400
    data = browser.post(url(payment, "/verify"), {"code": "000000"}, format="json").json()
    assert data["status"] == "blocked"
    assert Payment.objects.get(pk=payment.pk).outcome_reason == "verification_failed"
    assert "/sim/authorise" not in paths(fake_sim)


def test_a_card_that_is_not_enrolled_gets_the_email_code(browser, make_payment, fake_sim,
                                                         mailoutbox):
    payment = make_payment(amount=BIG)
    v = pay(browser, payment).json()["verification"]
    assert v["method"] == "email_code" and v["issuer_challenge"] is None
    assert len(mailoutbox) == 1
    asked = AuditEvent.objects.get(target_id=payment.public_id,
                                   action="payment.verification_required")
    assert asked.details["three_ds"] == "not_enrolled"


def test_a_bank_that_does_not_answer_falls_back_to_the_email_code(browser, make_payment,
                                                                  fake_sim, mailoutbox):
    fake_sim.threeds_mode = "hang"
    payment = make_payment(amount=BIG)
    v = pay(browser, payment, ENROLLED).json()["verification"]
    assert v["method"] == "email_code" and len(mailoutbox) == 1
    assert Payment.objects.get(pk=payment.pk).status == PaymentStatus.VERIFICATION_REQUIRED


def test_a_low_risk_payment_is_not_challenged(browser, make_payment, fake_sim):
    payment = make_payment()
    assert pay(browser, payment, ENROLLED).json()["status"] == "authorised"
    assert paths(fake_sim) == ["/sim/authorise"]
    assert "authentication" not in fake_sim.requests[0][2]


def test_the_dashboard_shows_the_banks_challenge(browser, make_payment, fake_sim, shop,
                                                 staff_client):
    payment = make_payment(amount=BIG)
    pay(browser, payment, ENROLLED)
    browser.post(url(payment, "/verify"), {"code": "135790"}, format="json")
    fake_sim.records[next(iter(fake_sim.records))]["network"] = {
        "stan": "123456", "rrn": "ABCDEFGHIJKL", "auth_code": "A1B2C3", "response_code": "00",
        "response_text": "Approved", "three_ds": {"three_ds_id": "3ds_" + "0" * 24, "eci": "05"},
        "trace": [{"node": "acquirer", "mti_in": "0100", "mti_out": "0110",
                   "response_code": "00", "elapsed_ms": 3}]}
    operator = staff_client("merchant_operator", merchant=shop.merchant)
    detail = operator.get(f"/internal/payments/{payment.public_id}").json()
    assert detail["three_d_secure"] == {"bank": "SecurePay Bank", "status": "authenticated",
                                        "eci": "05", "wrong_codes": 0}
