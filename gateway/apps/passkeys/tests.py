"""Passkeys (v2, docs/v2/PASSKEYS.md): add one, sign in with it, approve a risky payment.

The authenticator is apps/passkeys/testing.py, so the real `webauthn` checks run on real
P-256 signatures.
"""
import re
from datetime import timedelta

import pytest
from django.conf import settings
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.checkout.models import VerificationChallenge
from apps.customers.models import CustomerAccount
from apps.events.emails import send_due
from apps.passkeys.models import Passkey, WebAuthnChallenge
from apps.passkeys.testing import SoftAuthenticator
from apps.payments.models import Payment

pytestmark = pytest.mark.django_db(databases=["default", "vault"])

EMAIL = "jane@example.com"
PASSWORD = "correct horse battery 7"
BIG = 60_000  # S$600, over the S$500 verification rule


def post(client, path, body=None):
    return client.post(path, body or {}, format="json")


def device(**kwargs) -> SoftAuthenticator:
    return SoftAuthenticator(settings.WEBAUTHN_RP_ID, settings.WEBAUTHN_ORIGIN, **kwargs)


def signed_in(email=EMAIL) -> APIClient:
    client = APIClient()
    r = post(client, "/customer/register", {"full_name": "Jane Tan", "email": email,
                                            "password": PASSWORD, "privacy_consent": True})
    assert r.status_code == 201, r.content
    return client


def add_passkey(client, authenticator, name=None):
    options = post(client, "/customer/passkeys/register/options").json()["options"]
    body = {"credential": authenticator.create(options)}
    if name is not None:
        body["name"] = name
    return post(client, "/customer/passkeys/register", body)


@pytest.fixture
def jane():
    client = signed_in()
    authenticator = device()
    r = add_passkey(client, authenticator, name="Jane's phone")
    assert r.status_code == 201, r.content
    return client, authenticator


def sign_in_with(authenticator, client=None, **kwargs):
    client = client or APIClient()
    options = post(client, "/customer/passkeys/sign-in/options").json()["options"]
    return client, post(client, "/customer/passkeys/sign-in",
                        {"credential": authenticator.get(options, **kwargs)})


# --- adding a passkey ---------------------------------------------------------------------

def test_add_a_passkey_lists_it_and_emails_the_shopper(jane):
    client, _ = jane
    data = client.get("/customer/passkeys").json()
    (row,) = data["data"]
    assert row["name"] == "Jane's phone" and row["synced"] is True
    assert row["id"].startswith("pk_") and row["last_used_at"] is None
    assert data["limit"] == settings.GATEWAY_RULES["PASSKEYS_PER_CUSTOMER"]
    send_due()
    assert any("passkey was added" in m.subject for m in mail.outbox)
    assert AuditEvent.objects.filter(action="customer.passkey_added").count() == 1


def test_registration_options_ask_for_a_verified_discoverable_passkey(jane):
    client, authenticator = jane
    options = post(client, "/customer/passkeys/register/options").json()["options"]
    assert options["rp"] == {"id": settings.WEBAUTHN_RP_ID, "name": "SecurePay"}
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["authenticatorSelection"]["residentKey"] == "required"
    assert options["attestation"] == "none"
    # The passkey already on the account is excluded, so the device won't make a second one.
    assert [c["id"] for c in options["excludeCredentials"]] == [
        authenticator.create(options)["id"]]


def test_the_same_passkey_cannot_be_added_twice(jane):
    client, authenticator = jane
    r = add_passkey(client, authenticator)
    assert r.status_code == 409 and r.json()["error"]["code"] == "passkey_exists"


def test_adding_needs_a_session():
    r = post(APIClient(), "/customer/passkeys/register/options")
    assert r.status_code == 401 and r.json()["error"]["code"] == "sign_in_required"


def test_a_passkey_from_another_site_is_refused():
    client = signed_in()
    options = post(client, "/customer/passkeys/register/options").json()["options"]
    credential = device().create(options, origin="https://evil.example")
    r = post(client, "/customer/passkeys/register", {"credential": credential})
    assert r.status_code == 400 and r.json()["error"]["code"] == "passkey_failed"
    assert not Passkey.objects.exists()


def test_a_passkey_without_user_verification_is_refused():
    client = signed_in()
    r = add_passkey(client, device(user_verified=False))
    assert r.status_code == 400 and r.json()["error"]["code"] == "passkey_failed"


def test_a_challenge_is_used_once():
    client = signed_in()
    options = post(client, "/customer/passkeys/register/options").json()["options"]
    credential = device().create(options)
    assert post(client, "/customer/passkeys/register", {"credential": credential}
                ).status_code == 201
    r = post(client, "/customer/passkeys/register", {"credential": credential})
    assert r.status_code == 400 and "expired" in r.json()["error"]["message"]


def test_a_challenge_expires():
    client = signed_in()
    options = post(client, "/customer/passkeys/register/options").json()["options"]
    WebAuthnChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    r = post(client, "/customer/passkeys/register", {"credential": device().create(options)})
    assert r.status_code == 400 and r.json()["error"]["code"] == "passkey_failed"


def test_the_worker_deletes_old_challenges():
    post(APIClient(), "/customer/passkeys/sign-in/options")
    post(APIClient(), "/customer/passkeys/sign-in/options")
    WebAuthnChallenge.objects.filter(pk=WebAuthnChallenge.objects.first().pk).update(
        expires_at=timezone.now() - timedelta(hours=2))
    assert WebAuthnChallenge.purge_expired() == 1 and WebAuthnChallenge.objects.count() == 1


def test_one_shoppers_challenge_cannot_register_on_another_account():
    jane, other = signed_in(), signed_in("ali@example.com")
    options = post(jane, "/customer/passkeys/register/options").json()["options"]
    r = post(other, "/customer/passkeys/register", {"credential": device().create(options)})
    assert r.status_code == 400 and not Passkey.objects.exists()


def test_remove_a_passkey(jane):
    client, authenticator = jane
    passkey_id = client.get("/customer/passkeys").json()["data"][0]["id"]
    assert post(client, f"/customer/passkeys/{passkey_id}/remove").status_code == 204
    assert client.get("/customer/passkeys").json()["data"] == []
    _, r = sign_in_with(authenticator)
    assert r.status_code == 400


def test_another_shopper_cannot_remove_my_passkey(jane):
    client, _ = jane
    passkey_id = client.get("/customer/passkeys").json()["data"][0]["id"]
    other = signed_in("ali@example.com")
    assert post(other, f"/customer/passkeys/{passkey_id}/remove").status_code == 404
    assert Passkey.objects.count() == 1


# --- signing in -------------------------------------------------------------------------------

def test_sign_in_with_a_passkey(jane):
    _, authenticator = jane
    client, r = sign_in_with(authenticator)
    assert r.status_code == 200, r.content
    assert r.json()["email"] == EMAIL
    assert client.get("/customer/me").status_code == 200
    passkey = Passkey.objects.get()
    assert passkey.sign_count == 1 and passkey.last_used_at is not None
    audit = AuditEvent.objects.filter(action="customer.signed_in").latest("id")
    assert audit.details["method"] == "passkey"


def test_sign_in_options_do_not_name_any_account():
    options = post(APIClient(), "/customer/passkeys/sign-in/options").json()["options"]
    assert options["rpId"] == settings.WEBAUTHN_RP_ID
    assert options["userVerification"] == "required"
    assert options.get("allowCredentials", []) == []


def test_a_cloned_passkey_is_refused(jane):
    _, authenticator = jane
    assert sign_in_with(authenticator, counter=5)[1].status_code == 200
    _, r = sign_in_with(authenticator, counter=5)  # counter did not go up: a copy
    assert r.status_code == 400 and r.json()["error"]["code"] == "passkey_failed"


def test_an_unknown_passkey_cannot_sign_in():
    _, r = sign_in_with(device())
    assert r.status_code == 400 and r.json()["error"]["code"] == "passkey_failed"


def test_a_wrong_signature_cannot_sign_in(jane):
    _, authenticator = jane
    client = APIClient()
    options = post(client, "/customer/passkeys/sign-in/options").json()["options"]
    credential = authenticator.get(options)
    credential["response"]["signature"] = device().get(options)["response"]["signature"]
    r = post(client, "/customer/passkeys/sign-in", {"credential": credential})
    assert r.status_code == 400 and client.get("/customer/me").status_code == 401


def test_passkey_sign_in_works_while_password_sign_in_is_locked(jane):
    _, authenticator = jane
    CustomerAccount.objects.update(locked_until=timezone.now() + timedelta(minutes=15))
    assert sign_in_with(authenticator)[1].status_code == 200


def test_rubbish_answers_are_refused(jane):
    client = APIClient()
    for body in ({}, {"credential": "x"}, {"credential": {"response": {}}},
                 {"credential": {"response": {"clientDataJSON": "!!"}}},
                 {"credential": {"id": "x" * 20_000}}):
        r = post(client, "/customer/passkeys/sign-in", body)
        assert r.status_code == 400, body


# --- approving a risky payment ---------------------------------------------------------------

def pay(shop, browser, amount=BIG):
    from conftest import valid_body

    payment = Payment.objects.get(public_id=shop.create_payment(
        valid_body(amount=amount)).json()["id"])
    base = f"/checkout/{payment.checkout_session_id}"
    card = {"number": "4242424242424242", "expiry_month": 12, "expiry_year": 2030,
            "cvc": "123", "privacy_consent": True}
    assert post(browser, f"{base}/card", card).status_code == 200
    data = post(browser, f"{base}/submit").json()
    return payment, base, data


def approve(browser, base, authenticator, **kwargs):
    options = post(browser, f"{base}/verify/passkey/options").json()["options"]
    return post(browser, f"{base}/verify/passkey",
                {"credential": authenticator.get(options, **kwargs)})


def test_risky_payment_is_approved_with_a_passkey_and_no_email_is_sent(jane, shop, fake_sim):
    browser, authenticator = jane
    payment, base, data = pay(shop, browser)
    assert data["status"] == "verification_required"
    v = data["verification"]
    assert (v["passkey"], v["code_sent"], v["sent_to"]) == (True, False, None)
    send_due()
    assert not [m for m in mail.outbox if "verification code" in m.subject]

    options = post(browser, f"{base}/verify/passkey/options").json()["options"]
    assert len(options["allowCredentials"]) == 1  # only this shopper's passkeys
    r = post(browser, f"{base}/verify/passkey", {"credential": authenticator.get(options)})
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "authorised"
    verified = AuditEvent.objects.get(target_id=payment.public_id, action="payment.verified")
    assert verified.details["method"] == "passkey"
    assert VerificationChallenge.objects.get(payment=payment).verified_at is not None


def test_shopper_can_ask_for_an_email_code_instead(jane, shop, fake_sim):
    browser, _ = jane
    _, base, _ = pay(shop, browser)
    r = post(browser, f"{base}/verify", {"code": "123456"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "code_not_sent"
    r = post(browser, f"{base}/verify/resend")  # no wait for the first code
    assert r.status_code == 200, r.content
    assert r.json()["verification"]["code_sent"] is True
    send_due()
    (code_mail,) = [m for m in mail.outbox if "verification code" in m.subject]
    code = re.search(r"verification code is (\d{6})", code_mail.body).group(1)
    assert post(browser, f"{base}/verify", {"code": code}).json()["status"] == "authorised"


def test_a_guest_without_a_passkey_still_gets_the_email_code(shop, fake_sim):
    _, _, data = pay(shop, APIClient())
    assert data["verification"]["passkey"] is False
    assert data["verification"]["code_sent"] is True


def test_passkey_approval_needs_the_same_shopper_signed_in(jane, shop, fake_sim):
    browser, _ = jane
    _, base, _ = pay(shop, browser)
    stranger = APIClient()  # someone with the checkout link but not the session
    r = post(stranger, f"{base}/verify/passkey/options")
    assert r.status_code == 409 and r.json()["error"]["code"] == "passkey_not_available"


def test_another_shoppers_passkey_cannot_approve(jane, shop, fake_sim):
    browser, _ = jane
    _, base, _ = pay(shop, browser)
    ali = signed_in("ali@example.com")
    ali_device = device()
    assert add_passkey(ali, ali_device).status_code == 201
    options = post(browser, f"{base}/verify/passkey/options").json()["options"]
    r = post(browser, f"{base}/verify/passkey", {"credential": ali_device.get(options)})
    assert r.status_code == 400
    data = browser.get(base).json()
    assert data["status"] == "verification_required"
    assert data["verification"]["attempts_remaining"] == 2


def test_a_sign_in_challenge_cannot_approve_a_payment(jane, shop, fake_sim):
    browser, authenticator = jane
    _, base, _ = pay(shop, browser)
    options = post(APIClient(), "/customer/passkeys/sign-in/options").json()["options"]
    r = post(browser, f"{base}/verify/passkey", {"credential": authenticator.get(options)})
    assert r.status_code == 400


def test_a_challenge_for_one_payment_cannot_approve_another(jane, shop, fake_sim):
    browser, authenticator = jane
    _, first, _ = pay(shop, browser)
    _, second, _ = pay(shop, browser)
    options = post(browser, f"{first}/verify/passkey/options").json()["options"]
    r = post(browser, f"{second}/verify/passkey", {"credential": authenticator.get(options)})
    assert r.status_code == 400


def test_three_bad_passkey_answers_block_the_payment(jane, shop, fake_sim):
    browser, authenticator = jane
    payment, base, _ = pay(shop, browser)
    for _ in range(2):
        r = approve(browser, base, authenticator, origin="https://evil.example")
        assert r.status_code == 400
    r = approve(browser, base, authenticator, origin="https://evil.example")
    assert r.status_code == 200 and r.json()["status"] == "blocked"
    assert "/sim/authorise" not in [p for _, p, _ in fake_sim.requests]
    assert Payment.objects.get(pk=payment.pk).outcome_reason == "verification_failed"
