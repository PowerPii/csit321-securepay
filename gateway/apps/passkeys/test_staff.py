"""Staff passkeys (v2): password, then a passkey in place of the authenticator code.

Fifa chose this on 28 Nov 2026: the password step stays, so the two-step sign-in (FR-ACC-14)
and its lockout (FR-ACC-15) are unchanged.
"""
import pytest
from django.conf import settings
from django.core import mail
from django.test import Client
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import StaffAccount
from apps.audit.models import AuditEvent
from apps.events.emails import send_due
from apps.passkeys.models import Passkey
from apps.passkeys.testing import SoftAuthenticator
from conftest import current_totp, make_staff_user

pytestmark = pytest.mark.django_db(databases=["default", "vault"])

PASSWORD = "correct horse battery staple"
EMAIL = "analyst@securepay.sg"


def fresh_totp(account):
    TOTPDevice.objects.filter(user=account.user).update(last_t=-1)
    return current_totp(account)


def csrf_client():
    client = Client(enforce_csrf_checks=True)
    client.get("/internal/me")  # sets the CSRF cookie
    client.defaults["HTTP_X_CSRFTOKEN"] = client.cookies["csrftoken"].value
    return client


def post(client, path, body=None):
    r = client.post(path, body or {}, content_type="application/json")
    if "csrftoken" in client.cookies:  # sign-in rotates the token
        client.defaults["HTTP_X_CSRFTOKEN"] = client.cookies["csrftoken"].value
    return r


def login(client, email=EMAIL):
    return post(client, "/internal/auth/login", {"email": email, "password": PASSWORD})


def signed_in(account):
    client = csrf_client()
    login(client, account.email)
    assert post(client, "/internal/auth/totp", {"code": fresh_totp(account)}).status_code == 200
    return client


def device():
    return SoftAuthenticator(settings.WEBAUTHN_RP_ID, settings.WEBAUTHN_ORIGIN)


def add(client, account, authenticator, code=None, name="Work laptop"):
    options = post(client, "/internal/me/passkeys/register/options").json()["options"]
    return post(client, "/internal/me/passkeys/register", {
        "credential": authenticator.create(options), "name": name,
        "code": code or fresh_totp(account)})


@pytest.fixture
def analyst():
    return make_staff_user(EMAIL, "fraud_analyst", password=PASSWORD)


@pytest.fixture
def with_passkey(analyst):
    authenticator = device()
    r = add(signed_in(analyst), analyst, authenticator)
    assert r.status_code == 201, r.content
    return analyst, authenticator


def passkey_step(client, authenticator, **kwargs):
    options = post(client, "/internal/auth/passkey/options").json()["options"]
    return post(client, "/internal/auth/passkey",
                {"credential": authenticator.get(options, **kwargs)})


def test_add_a_passkey_needs_the_authenticator_code(analyst):
    client = signed_in(analyst)
    r = add(client, analyst, device(), code="000000")
    assert r.status_code == 403 and r.json()["error"]["code"] == "code_incorrect"
    assert not Passkey.objects.exists()
    r = add(client, analyst, device())
    assert r.status_code == 201, r.content
    (row,) = client.get("/internal/me/passkeys").json()["data"]
    assert row["name"] == "Work laptop"
    assert Passkey.objects.get().staff_id == analyst.pk
    send_due()
    assert any("passkey was added" in m.subject for m in mail.outbox)
    added = AuditEvent.objects.get(action="staff.passkey_added")
    assert added.actor_id == analyst.public_id and added.details["mfa"] is True


def test_password_then_passkey_signs_in(with_passkey):
    _, authenticator = with_passkey
    client = csrf_client()
    r = login(client)
    assert r.status_code == 200 and r.json()["passkey"] is True
    assert client.get("/internal/me").status_code == 401  # password alone is not a sign-in
    assert client.get("/internal/auth/totp").json()["passkey"] is True
    r = passkey_step(client, authenticator)
    assert r.status_code == 200, r.content
    assert r.json()["email"] == EMAIL
    assert client.get("/internal/me").status_code == 200
    assert client.get("/internal/payments").status_code == 200  # a verified (2FA) session
    signed = AuditEvent.objects.filter(action="staff.signed_in").latest("id")
    assert signed.details["method"] == "passkey"


def test_the_passkey_step_needs_the_password_first(with_passkey):
    r = post(csrf_client(), "/internal/auth/passkey/options")
    assert r.status_code == 401 and r.json()["error"]["code"] == "sign_in_required"


def test_staff_without_a_passkey_are_not_offered_one(analyst):
    client = csrf_client()
    assert login(client).json()["passkey"] is False
    r = post(client, "/internal/auth/passkey/options")
    assert r.status_code == 409 and r.json()["error"]["code"] == "passkey_not_available"


def test_another_staff_members_passkey_cannot_sign_in(with_passkey):
    _ = with_passkey
    other = make_staff_user("ops@securepay.sg", "administrator", password=PASSWORD)
    theirs = device()
    assert add(signed_in(other), other, theirs).status_code == 201
    client = csrf_client()
    login(client)
    r = passkey_step(client, theirs)
    assert r.status_code == 400 and client.get("/internal/me").status_code == 401
    assert StaffAccount.objects.get(user__email=EMAIL).failed_attempts == 1


def test_bad_passkey_answers_count_towards_the_lockout(with_passkey):
    analyst, authenticator = with_passkey
    client = csrf_client()
    login(client)
    limit = settings.GATEWAY_RULES["STAFF_LOCKOUT_FAILURES"]
    for _ in range(limit - 1):
        r = passkey_step(client, authenticator, origin="https://evil.example")
        assert r.status_code == 400
    r = passkey_step(client, authenticator, origin="https://evil.example")
    assert r.status_code == 403 and r.json()["error"]["code"] == "account_locked"
    analyst.refresh_from_db()
    assert analyst.is_locked()


def test_a_shopper_passkey_flow_cannot_use_a_staff_passkey(with_passkey):
    from rest_framework.test import APIClient

    _, authenticator = with_passkey
    shopper = APIClient()
    options = shopper.post("/customer/passkeys/sign-in/options", {}, format="json").json()
    r = shopper.post("/customer/passkeys/sign-in",
                     {"credential": authenticator.get(options["options"])}, format="json")
    assert r.status_code == 400


def test_remove_a_staff_passkey(with_passkey):
    analyst, _ = with_passkey
    client = signed_in(analyst)
    passkey_id = client.get("/internal/me/passkeys").json()["data"][0]["id"]
    assert post(client, f"/internal/me/passkeys/{passkey_id}/remove").status_code == 204
    assert not Passkey.objects.exists()
    fresh = csrf_client()
    assert login(fresh).json()["passkey"] is False


def test_passkey_pages_need_a_full_sign_in(analyst):
    client = csrf_client()
    login(client)  # password only
    assert client.get("/internal/me/passkeys").status_code == 401
    assert post(client, "/internal/me/passkeys/register/options").status_code == 401
