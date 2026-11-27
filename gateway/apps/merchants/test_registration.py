"""Merchant self-registration (FR-MER-01, FR-MER-02, FR-MER-10, FR-ADM-01; wireframes D7, D8).

Sprint 10 "done when": a new merchant signs up, verifies its email, is approved, gets a key and
receives a webhook.
"""
import json
import re
from datetime import timedelta

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import StaffAccount
from apps.audit.models import AuditEvent
from apps.events import delivery, signing
from apps.events.emails import send_due
from apps.events.models import OutboundEmail
from apps.merchants.models import Merchant
from apps.payments.models import Payment
from conftest import current_totp, make_staff_user, valid_body

pytestmark = pytest.mark.django_db(databases=["default", "vault"])

PASSWORD = "demo shop passphrase 2026"
HOOK = "https://hooks.newshop.example/securepay"


def form(**over):
    body = {
        "business_name": "New Shop Pte. Ltd.", "registration_number": "202612345K",
        "website": "https://newshop.example", "contact_name": "Aisha Rahman",
        "email": "aisha@newshop.example", "password": PASSWORD, "confirmed": True,
    }
    body.update(over)
    return body


def csrf_client():
    client = Client(enforce_csrf_checks=True)
    client.get("/internal/me")
    client.defaults["HTTP_X_CSRFTOKEN"] = client.cookies["csrftoken"].value
    return client


def post(client, path, body=None):
    return client.post(f"/internal{path}", body or {}, content_type="application/json")


def register(client=None, **over):
    return post(client or csrf_client(), "/register", form(**over))


def emails_to(address, subject_part=""):
    send_due()
    return [m for m in mail.outbox if m.to == [address] and subject_part in m.subject]


def verify_token(address="aisha@newshop.example"):
    (message,) = emails_to(address, "verify your email")
    match = re.search(r"/dashboard/register/verify#token=([\w-]+)", message.body)
    assert match, message.body
    return match.group(1)


def sign_in(email, password=PASSWORD):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    client = Client()
    r = post(client, "/auth/login", {"email": email, "password": password})
    assert r.status_code == 200, r.content
    account = StaffAccount.objects.get(user__email=email)
    device = TOTPDevice.objects.get(user=account.user)  # first sign-in: set up the app
    r = post(client, "/auth/totp", {"code": current_totp(account, device)})
    assert r.status_code == 200, r.content
    return client


@pytest.fixture
def admin(staff_client):
    return staff_client("administrator")


# --- D7: the form ---------------------------------------------------------------------------

def test_registration_creates_a_pending_merchant_and_inactive_operator():
    r = register()
    assert r.status_code == 202, r.content
    assert "aisha@newshop.example" in r.json()["message"]
    merchant = Merchant.objects.get(registration_number="202612345K")
    assert merchant.status == "pending" and merchant.email_verified_at is None
    assert merchant.name == "New Shop Pte. Ltd." and merchant.self_registered
    assert not merchant.api_keys.exists()
    account = StaffAccount.objects.get(merchant=merchant)
    assert account.role == "merchant_operator" and not account.user.is_active
    assert account.user.check_password(PASSWORD)
    # Not verified yet: cannot sign in.
    r = post(csrf_client(), "/auth/login", {"email": "aisha@newshop.example",
                                            "password": PASSWORD})
    assert r.status_code == 401
    assert AuditEvent.objects.filter(action="merchant.registered",
                                     target_id=merchant.public_id).exists()


@pytest.mark.parametrize(("field", "value", "code"), [
    ("business_name", "", "missing_field"),
    ("registration_number", "12345", "invalid_field"),
    ("website", "not a site", "invalid_field"),
    ("contact_name", "  ", "missing_field"),
    ("email", "nobody", "invalid_field"),
    ("password", "short", "weak_password"),
    ("password", "aisha@newshop.example", "weak_password"),
    ("confirmed", False, "missing_field"),
])
def test_each_field_is_checked(field, value, code):
    r = register(**{field: value})
    assert r.status_code == 400, r.content
    error = r.json()["error"]
    assert error["code"] == code and error["param"] == field
    assert not Merchant.objects.exists()


@pytest.mark.parametrize("uen", ["53123456A", "202612345k", "T08LL1234A"])
def test_uen_formats(uen):
    assert register(registration_number=uen).status_code == 202
    assert Merchant.objects.get().registration_number == uen.upper()


def test_business_already_registered():
    assert register().status_code == 202
    r = register(email="someone@else.example")
    assert r.status_code == 409
    assert r.json()["error"] == {**r.json()["error"], "code": "business_already_registered",
                                 "param": "registration_number"}


def test_existing_email_gets_the_same_reply_and_a_notice(shop):
    make_staff_user("ops@demoshop.sg", "merchant_operator", merchant=shop.merchant)
    r = register(email="ops@demoshop.sg")
    fresh = register(registration_number="53123456A", email="fresh@new.example")
    assert r.status_code == fresh.status_code == 202
    assert r.json()["message"].replace("ops@demoshop.sg", "X") == \
        fresh.json()["message"].replace("fresh@new.example", "X")
    assert not Merchant.objects.filter(registration_number="202612345K").exists()
    (notice,) = emails_to("ops@demoshop.sg")
    assert "already has an account" in notice.body


def test_expired_unverified_registration_frees_the_uen_and_email():
    register()
    Merchant.objects.update(created_at=timezone.now() - timedelta(hours=25))
    r = register(business_name="New Shop (again)")
    assert r.status_code == 202
    assert Merchant.objects.get().name == "New Shop (again)"
    assert get_user_model().objects.filter(email="aisha@newshop.example").count() == 1


def test_registration_needs_csrf_token():
    r = Client(enforce_csrf_checks=True).post("/internal/register", form(),
                                              content_type="application/json")
    assert r.status_code == 403


# --- email verification and the administrator notice (FR-MER-02) -----------------------------

def test_verify_email_activates_the_operator_and_notifies_administrators(admin):
    register()
    token = verify_token()
    r = post(csrf_client(), "/register/verify", {"token": token})
    assert r.status_code == 200, r.content
    assert r.json()["merchant"]["status"] == "pending"
    merchant = Merchant.objects.get()
    assert merchant.email_verified_at is not None
    assert StaffAccount.objects.get(merchant=merchant).user.is_active
    (notice,) = emails_to(admin.account.email, "new merchant registration")
    assert "202612345K" in notice.body and "/dashboard/merchants" in notice.body
    # The link works once.
    r = post(csrf_client(), "/register/verify", {"token": token})
    assert r.status_code == 400 and r.json()["error"]["code"] == "link_invalid"


def test_verification_link_expires(admin):
    from apps.accounts.models import AccountToken

    register()
    token = verify_token()
    AccountToken.objects.update(expires_at=timezone.now())
    assert post(csrf_client(), "/register/verify", {"token": token}).status_code == 400


def test_pending_operator_can_sign_in_but_has_no_key():
    register()
    post(csrf_client(), "/register/verify", {"token": verify_token()})
    client = sign_in("aisha@newshop.example")
    me = client.get("/internal/me").json()
    assert me["role"] == "merchant_operator" and me["merchant"]["status"] == "pending"
    own = client.get("/internal/merchant").json()
    assert own["api_key"] is None and own["can_generate_key"] is False
    r = post(client, "/merchant/api-key")
    assert r.status_code == 409 and r.json()["error"]["code"] == "merchant_not_approved"
    assert client.get("/internal/payments").json()["count"] == 0


# --- administrator approval (FR-ADM-01) ------------------------------------------------------

def test_unverified_registration_cannot_be_approved(admin):
    register()
    merchant = Merchant.objects.get()
    data = {m["id"]: m for m in admin.get("/internal/merchants").json()["data"]}
    assert data[merchant.public_id]["registration"]["email_verified_at"] is None
    r = post(admin, f"/merchants/{merchant.public_id}/approve")
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_not_verified"


def test_approval_tells_the_merchant_and_never_shows_the_key_to_the_admin(admin):
    register()
    post(csrf_client(), "/register/verify", {"token": verify_token()})
    merchant = Merchant.objects.get()
    r = post(admin, f"/merchants/{merchant.public_id}/approve")
    assert r.status_code == 200, r.content
    assert "credentials" not in r.json() and r.json()["api_key"] is None
    (notice,) = emails_to("aisha@newshop.example", "is approved")
    assert "generate your API key" in notice.body


def test_rejection_gives_a_reason_and_frees_the_business_to_apply_again(admin):
    register()
    post(csrf_client(), "/register/verify", {"token": verify_token()})
    merchant = Merchant.objects.get()
    r = post(admin, f"/merchants/{merchant.public_id}/reject", {"reason": "UEN not found"})
    assert r.status_code == 200
    (notice,) = emails_to("aisha@newshop.example", "not approved")
    assert "UEN not found" in notice.body
    r = post(csrf_client(), "/auth/login", {"email": "aisha@newshop.example",
                                            "password": PASSWORD})
    assert r.status_code == 401  # a rejected merchant's operator cannot sign in
    assert register().status_code == 202
    assert Merchant.objects.filter(registration_number="202612345K", status="pending").exists()


def test_admin_created_merchant_still_gets_its_key_on_approval(admin):
    created = post(admin, "/merchants", {"name": "Admin Made"}).json()
    r = post(admin, f"/merchants/{created['id']}/approve")
    assert r.json()["credentials"]["api_key"].startswith("sp_test_")


# --- D8 and webhook submission (FR-MER-03, FR-MER-10) ----------------------------------------

def test_operator_submits_webhook_and_admin_approves(admin):
    register()
    post(csrf_client(), "/register/verify", {"token": verify_token()})
    client = sign_in("aisha@newshop.example")
    r = post(client, "/merchant/webhook", {"webhook_url": "http://plain.example/hook"})
    assert r.status_code == 400
    r = post(client, "/merchant/webhook", {"webhook_url": HOOK})
    assert r.status_code == 200
    assert r.json()["webhook_url"] == HOOK and r.json()["webhook_url_approved"] is False
    assert emails_to(admin.account.email, "webhook address to approve")
    assert AuditEvent.objects.filter(action="merchant.webhook_submitted").exists()


def test_operator_of_another_role_cannot_use_d8(admin):
    assert admin.get("/internal/merchant").status_code == 403


def test_new_merchant_signs_up_verifies_is_approved_gets_a_key_and_receives_a_webhook(
        admin, fake_sim, monkeypatch, settings):
    received = []

    def endpoint(request):
        received.append(request)
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(delivery, "TRANSPORT", httpx.MockTransport(endpoint))
    settings.WEBHOOK_ALLOWED_PRIVATE_HOSTS = frozenset({"hooks.newshop.example"})

    # 1. Sign up (D7) and verify the email.
    assert register().status_code == 202
    assert post(csrf_client(), "/register/verify", {"token": verify_token()}).status_code == 200
    operator = sign_in("aisha@newshop.example")
    assert post(operator, "/merchant/webhook", {"webhook_url": HOOK}).status_code == 200

    # 2. An administrator approves the merchant and its webhook address.
    merchant = Merchant.objects.get(registration_number="202612345K")
    assert post(admin, f"/merchants/{merchant.public_id}/approve").status_code == 200
    assert post(admin, f"/merchants/{merchant.public_id}/webhook",
                {"approved": True}).status_code == 200

    # 3. The operator generates the key and signing secret (D8), shown once.
    r = post(operator, "/merchant/api-key")
    assert r.status_code == 201, r.content
    creds = r.json()["credentials"]
    assert creds["api_key"].startswith("sp_test_") and creds["signing_secret"].startswith("whsec_")
    again = post(operator, "/merchant/api-key")
    assert again.status_code == 409 and again.json()["error"]["code"] == "api_key_exists"
    assert "credentials" not in operator.get("/internal/merchant").json()
    # v2: a live key next to it. The signing secret is shared, so it is kept (null here).
    live = post(operator, "/merchant/api-key", {"mode": "live"})
    assert live.status_code == 201, live.content
    assert live.json()["credentials"]["api_key"].startswith("sp_live_")
    assert live.json()["credentials"]["signing_secret"] is None
    assert live.json()["live_api_key"]["mode"] == "live"
    assert live.json()["api_key"]["mode"] == "test"
    assert post(operator, "/merchant/api-key", {"mode": "live"}).status_code == 409
    assert post(operator, "/merchant/api-key", {"mode": "real"}).status_code == 400

    # 4. The key works on the merchant API, and a paid order sends a signed webhook.
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {creds['api_key']}")
    r = api.post("/v1/payments", valid_body(), format="json",
                 headers={"Idempotency-Key": "new-shop-1"})
    assert r.status_code == 201, r.content
    payment = Payment.objects.get(public_id=r.json()["id"])
    browser = APIClient()
    base = f"/checkout/{payment.checkout_session_id}"
    browser.post(f"{base}/card", {"number": "4242424242424242", "expiry_month": 12,
                                  "expiry_year": 2030, "cvc": "123", "privacy_consent": True}, format="json")
    browser.post(f"{base}/submit", {}, format="json")
    payment.refresh_from_db()
    assert payment.status == "authorised"

    delivery.deliver_due()
    assert received, "no webhook was sent"
    request = received[-1]
    assert str(request.url) == HOOK
    assert signing.verify(request.content, request.headers["SecurePay-Signature"],
                          creds["signing_secret"])
    assert json.loads(request.content)["type"] == "payment.authorised"
    assert OutboundEmail.objects.filter(kind="email_verification").exists()
