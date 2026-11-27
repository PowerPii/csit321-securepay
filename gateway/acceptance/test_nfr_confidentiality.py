"""URS 4.4.1 Confidentiality: NFR-CON-01 to NFR-CON-05 at the URS's own numbers."""
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice
from nfr_helpers import capture, json_post, pay, refund
from rest_framework.test import APIClient

from apps.customers.models import CustomerAccount
from apps.payments.models import Payment
from apps.refunds.models import Refund
from conftest import STAFF_PASSWORD, make_staff_user

pytestmark = pytest.mark.django_db(databases=["default", "vault"])

STAFF_ROLES = ("merchant_operator", "administrator", "fraud_analyst")


# --- NFR-CON-01: another merchant's records are "not found" ----------------------------------

def test_nfr_con_01_twenty_of_another_merchants_records_are_not_found(
        shop, other_shop, staff_client, fake_sim):
    """Merchant A asks for 20 of merchant B's records through the API and the dashboard."""
    payments = [pay(other_shop) for _ in range(15)]
    refunds = []
    for payment in payments[:5]:
        capture(other_shop, payment)
        r = refund(other_shop, payment, 500)
        assert r.status_code == 201, r.content
        refunds.append(r.json()["id"])
    records = [p.public_id for p in payments] + refunds
    assert len(records) == 20
    before = {p.pk: (p.status, p.captured_amount) for p in Payment.objects.all()}
    refund_count = Refund.objects.count()

    operator = staff_client("merchant_operator", merchant=shop.merchant)
    secrets = {other_shop.merchant.name, other_shop.merchant.public_id, "ORD-10042"}
    answers = []
    for record in records:
        if record.startswith("rfd_"):
            answers.append(shop.get(f"/v1/refunds/{record}"))
            continue
        answers += [
            shop.get(f"/v1/payments/{record}"),
            shop.get(f"/v1/payments/{record}/refunds"),
            shop.post(f"/v1/payments/{record}/capture", {}, format="json",
                      headers={"Idempotency-Key": f"probe-capture-{record}"}),
            shop.post(f"/v1/payments/{record}/cancel", {}, format="json",
                      headers={"Idempotency-Key": f"probe-cancel-{record}"}),
            refund(shop, Payment.objects.get(public_id=record), 100),
            operator.get(f"/internal/payments/{record}"),
            operator.get(f"/internal/payments/{record}/refunds"),
            json_post(operator, f"/internal/payments/{record}/capture"),
            json_post(operator, f"/internal/payments/{record}/cancel"),
            json_post(operator, f"/internal/payments/{record}/refunds", {"amount": 100}),
        ]
    assert len(answers) == 15 * 10 + 5
    for response in answers:
        assert response.status_code == 404, (response.request["PATH_INFO"], response.content)
        text = response.content.decode()
        assert not any(secret in text for secret in secrets)
    listed = operator.get("/internal/payments").json()
    assert listed["count"] == 0  # A's operator lists none of B's payments
    # Nothing changed on B's side.
    assert {p.pk: (p.status, p.captured_amount) for p in Payment.objects.all()} == before
    assert Refund.objects.count() == refund_count


# --- NFR-CON-02 is checked with NFR-ENC-02 in test_nfr_card_data.py ---------------------------

# --- NFR-CON-03: every staff role needs its authenticator code ---------------------------------

def _csrf_client():
    client = Client(enforce_csrf_checks=True)
    client.get("/internal/me")
    client.defaults["HTTP_X_CSRFTOKEN"] = client.cookies["csrftoken"].value
    return client


def _wrong_code(account) -> str:
    device = TOTPDevice.objects.get(user=account.user, confirmed=True)
    valid = set()
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits)
    for drift in range(-3, 4):
        totp.drift = drift
        valid.add(f"{totp.token():06d}")
    return next(f"{n:06d}" for n in range(123456, 999999, 7919) if f"{n:06d}" not in valid)


@pytest.mark.parametrize("role", STAFF_ROLES)
def test_nfr_con_03_twenty_sign_ins_without_the_right_code_are_refused(role, shop):
    merchant = shop.merchant if role == "merchant_operator" else None
    account = make_staff_user(f"{role}@securepay.sg", role, merchant=merchant)
    wrong = _wrong_code(account)
    refused = 0
    for attempt in range(20):
        client = _csrf_client()
        r = json_post(client, "/internal/auth/login",
                      {"email": account.user.email, "password": STAFF_PASSWORD})
        # After five failures the account is locked: the password step refuses too, with the
        # same reply as a wrong password (SEC-07).
        assert r.status_code in (200, 401), r.content
        if r.status_code == 200 and attempt % 2:
            r = json_post(client, "/internal/auth/totp", {"code": wrong})
            assert r.status_code in (400, 401, 403), r.content
        # Missing or wrong code: still not signed in, for every staff page.
        for page in ("/internal/me", "/internal/payments", "/internal/overview"):
            assert client.get(page).status_code == 401
        refused += 1
    assert refused == 20


@pytest.mark.parametrize("role", STAFF_ROLES)
def test_nfr_con_03_new_staff_account_cannot_proceed_without_mfa(role, shop):
    merchant = shop.merchant if role == "merchant_operator" else None
    account = make_staff_user(f"new-{role}@securepay.sg", role, merchant=merchant,
                              with_device=False)
    client = _csrf_client()
    r = json_post(client, "/internal/auth/login",
                  {"email": account.user.email, "password": STAFF_PASSWORD})
    assert r.status_code == 200 and r.json()["setup"]["otpauth_uri"].startswith("otpauth://")
    assert client.get("/internal/auth/totp").json()["setup_required"] is True
    for page in ("/internal/me", "/internal/payments", "/internal/overview"):
        assert client.get(page).status_code == 401
    assert not TOTPDevice.objects.filter(user=account.user, confirmed=True).exists()


# --- NFR-CON-04: Argon2id with a unique salt; 12 characters minimum ----------------------------

ARGON2ID = re.compile(r"^argon2\$argon2id\$v=19\$m=\d+,t=\d+,p=\d+\$([A-Za-z0-9+/]+)\$")


def test_nfr_con_04_passwords_are_salted_argon2id_and_at_least_12_characters(shop):
    for n, role in enumerate(STAFF_ROLES * 2):
        make_staff_user(f"staff{n}@securepay.sg", role,
                        merchant=shop.merchant if role == "merchant_operator" else None)
    browser = APIClient()
    for n in range(3):
        r = browser.post("/customer/register", {
            "full_name": "Jane Tan", "email": f"jane{n}@example.com",
            "password": "correct horse battery 7", "privacy_consent": True}, format="json")
        assert r.status_code == 201, r.content
        browser = APIClient()

    hashes = list(get_user_model().objects.values_list("password", flat=True))
    hashes += list(CustomerAccount.objects.values_list("password", flat=True))
    assert len(hashes) == 9
    salts = []
    for stored in hashes:
        match = ARGON2ID.match(stored)
        assert match, f"not an Argon2id hash: {stored[:20]}..."
        salts.append(match.group(1))
    assert len(set(salts)) == len(salts)  # same password, different salt every time

    r = APIClient().post("/customer/register", {
        "full_name": "Short Pw", "email": "short@example.com", "password": "abcdefgh123",
        "privacy_consent": True}, format="json")
    assert len("abcdefgh123") == 11
    assert r.status_code == 400
    assert not CustomerAccount.objects.filter(email="short@example.com").exists()


# --- NFR-CON-05: the permission matrix (Figure 3.3.1, Table 3.3.1) -----------------------------

# Who may call each endpoint. "public" endpoints have no access control (card entry, sign-in,
# registration, health checks) and are only listed, so a new endpoint cannot slip in unseen.
OP, AD, AN = "merchant_operator", "administrator", "fraud_analyst"
STAFF = {OP, AD, AN}
ANALYST_OR_ADMIN = {AN, AD}
KEY, CUSTOMER = "api_key", "customer"

MATRIX: dict[tuple[str, str], set[str] | str] = {
    ("healthz", "get"): "public",
    ("readyz", "get"): "public",
    ("status.json", "get"): "public",
    ("webhooks/resend", "post"): "public",  # Resend (Svix) signature
    ("api/schema/", "get"): "public",  # DEBUG only
    # Merchant API (API key)
    ("v1/payments", "post"): {KEY},
    ("v1/payments/{id}", "get"): {KEY},
    ("v1/payments/{id}/capture", "post"): {KEY},
    ("v1/payments/{id}/cancel", "post"): {KEY},
    ("v1/payments/{id}/refunds", "get"): {KEY},
    ("v1/payments/{id}/refunds", "post"): {KEY},
    ("v1/refunds/{id}", "get"): {KEY},
    # Hosted checkout: the unguessable session ID is the credential
    ("checkout/{id}", "get"): "public",
    ("checkout/{id}/card", "post"): "public",
    ("checkout/{id}/submit", "post"): "public",
    ("checkout/{id}/verify", "post"): "public",
    ("checkout/{id}/verify/resend", "post"): "public",
    ("checkout/{id}/result", "get"): "public",
    # Guest status: the emailed code is the credential
    ("guest/status/code", "post"): "public",
    ("guest/status/verify", "post"): "public",
    ("guest/status/payment", "get"): "public",
    ("guest/status/receipt.pdf", "get"): "public",
    ("guest/status/sign-out", "post"): "public",
    # Customer accounts
    ("customer/register", "post"): "public",
    ("customer/sign-in", "post"): "public",
    ("customer/sign-out", "post"): "public",
    ("customer/password-reset/request", "post"): "public",
    ("customer/password-reset/check", "post"): "public",
    ("customer/password-reset/confirm", "post"): "public",
    ("customer/sign-out-everywhere", "post"): {CUSTOMER},
    ("customer/me", "get"): {CUSTOMER},
    ("customer/password", "post"): {CUSTOMER},
    ("customer/profile", "get"): {CUSTOMER},
    ("customer/profile", "post"): {CUSTOMER},
    ("customer/insights", "get"): {CUSTOMER},
    ("customer/payments", "get"): {CUSTOMER},
    ("customer/payments/{id}", "get"): {CUSTOMER},
    ("customer/payments/{id}/receipt.pdf", "get"): {CUSTOMER},
    # Staff sign-in and merchant registration
    ("internal/auth/login", "post"): "public",
    ("internal/auth/totp", "get"): "public",
    ("internal/auth/totp", "post"): "public",
    ("internal/auth/backup-code", "post"): "public",
    ("internal/auth/logout", "post"): "public",
    ("internal/auth/password-reset", "post"): "public",
    ("internal/auth/password-reset/check", "post"): "public",
    ("internal/auth/password-reset/confirm", "post"): "public",
    ("internal/register", "post"): "public",
    ("internal/register/verify", "post"): "public",
    ("internal/simulator/notifications", "post"): "public",  # simulator signature
    # Staff dashboard
    ("internal/me", "get"): STAFF,
    ("internal/me/backup-codes", "get"): STAFF,
    ("internal/me/backup-codes", "post"): STAFF,
    ("internal/overview", "get"): STAFF,
    ("internal/payments", "get"): STAFF,
    ("internal/payments/{id}", "get"): STAFF,
    ("internal/merchant", "get"): {OP},
    ("internal/merchant/api-key", "post"): {OP},
    ("internal/merchant/api-key/rotate", "post"): {OP},
    ("internal/merchant/api-key/revoke", "post"): {OP},
    ("internal/merchant/webhook", "post"): {OP},
    ("internal/webhooks", "get"): {OP, AD},
    ("internal/webhooks/{id}", "get"): {OP, AD},
    ("internal/webhooks/{id}/resend", "post"): {OP, AD},
    # v2 ledger: balances and entries (operator: own; administrator: all, read-only)
    ("internal/balance", "get"): {OP, AD},
    ("internal/ledger/entries", "get"): {OP, AD},
    ("internal/payouts", "get"): {OP, AD},
    ("internal/payouts", "post"): {OP},
    ("internal/payments/{id}/refunds", "get"): {OP},
    ("internal/payments/{id}/refunds", "post"): {OP},
    ("internal/payments/{id}/capture", "post"): {OP},
    ("internal/payments/{id}/cancel", "post"): {OP},
    ("internal/operations/{id}/recheck", "post"): {AD},
    ("internal/reviews", "get"): ANALYST_OR_ADMIN,
    ("internal/reviews/{id}", "get"): ANALYST_OR_ADMIN,
    ("internal/reviews/{id}", "post"): ANALYST_OR_ADMIN,
    ("internal/alerts", "get"): ANALYST_OR_ADMIN,
    ("internal/alerts/unread", "get"): ANALYST_OR_ADMIN,
    ("internal/alerts/read-all", "post"): ANALYST_OR_ADMIN,
    ("internal/alerts/{id}/read", "post"): ANALYST_OR_ADMIN,
    ("internal/reports/fraud", "get"): ANALYST_OR_ADMIN,
    ("internal/reports/fraud/export", "get"): ANALYST_OR_ADMIN,
    ("internal/insights/fraud", "get"): ANALYST_OR_ADMIN,
    ("internal/fraud/search", "get"): ANALYST_OR_ADMIN,
    ("internal/blocklist", "get"): ANALYST_OR_ADMIN,
    ("internal/blocklist", "post"): ANALYST_OR_ADMIN,
    ("internal/blocklist/{n}/remove", "post"): {AD},
    ("internal/rules", "get"): ANALYST_OR_ADMIN,
    ("internal/rules", "post"): {AD},
    ("internal/rules/check", "post"): {AD},
    ("internal/rules/freeze", "post"): {AD},
    ("internal/rules/unfreeze", "post"): {AD},
    ("internal/rules/{id}", "get"): ANALYST_OR_ADMIN,
    ("internal/rules/{id}", "post"): {AD},
    ("internal/rules/{id}/enabled", "post"): {AD},
    ("internal/merchants", "get"): {AD},
    ("internal/merchants", "post"): {AD},
    ("internal/merchants/{id}/approve", "post"): {AD},
    ("internal/merchants/{id}/reject", "post"): {AD},
    ("internal/merchants/{id}/webhook", "post"): {AD},
    ("internal/merchants/{id}/status", "post"): {AD},
    ("internal/merchants/{id}/limit", "post"): {AD},
    ("internal/staff", "get"): {AD},
    ("internal/staff/{id}/status", "post"): {AD},
    ("internal/staff/{id}/role", "post"): {AD},
    ("internal/limits", "get"): {AD},
    ("internal/limits", "post"): {AD},
    ("internal/audit", "get"): {AD},
    ("internal/integrity", "get"): {AD},
    ("internal/integrity/runs", "get"): {AD},
    ("internal/integrity/runs/{id}", "get"): {AD},
    ("internal/integrity/run", "post"): {AD},
    ("internal/security/keys/rotate", "post"): {AD},
    ("internal/security/alerts", "get"): {AD},
    ("internal/security/alerts/open-count", "get"): {AD},
    ("internal/security/alerts/{id}", "get"): {AD},
    ("internal/security/alerts/{id}/resolve", "post"): {AD},
}

PRINCIPALS = ("guest", CUSTOMER, KEY, OP, AD, AN)
PARAM = re.compile(r"<(?:(?!int:)\w+:)?\w+>")
INT_PARAM = re.compile(r"<int:\w+>")


def _endpoints():
    """Every (path, method) the gateway serves, apart from Django admin (off on the server)."""
    found = set()

    def walk(patterns, prefix=""):
        for entry in patterns:
            if isinstance(entry, URLResolver):
                if str(entry.pattern).startswith("admin/"):
                    continue
                walk(entry.url_patterns, prefix + str(entry.pattern))
            elif isinstance(entry, URLPattern):
                view = getattr(entry.callback, "cls", None) or getattr(
                    entry.callback, "view_class", None)
                path = PARAM.sub("{id}", INT_PARAM.sub("{n}", prefix + str(entry.pattern)))
                for method in ("get", "post", "put", "patch", "delete"):
                    if view is not None and hasattr(view, method):
                        found.add((path, method))

    walk(get_resolver().url_patterns)
    return found


def test_nfr_con_05_every_endpoint_is_in_the_permission_matrix(settings):
    served = _endpoints()
    assert served - set(MATRIX) == set(), "endpoints missing from the matrix"
    assert {k for k in set(MATRIX) - served if k[0] != "api/schema/"} == set(), \
        "matrix rows for endpoints that no longer exist"


@pytest.fixture
def principals(shop, staff_client):
    customer = APIClient()
    r = customer.post("/customer/register", {
        "full_name": "Jane Tan", "email": "jane@example.com",
        "password": "correct horse battery 7", "privacy_consent": True}, format="json")
    assert r.status_code == 201, r.content
    return {
        "guest": Client(),
        CUSTOMER: customer,
        KEY: shop,
        OP: staff_client(OP, merchant=shop.merchant),
        AD: staff_client(AD),
        AN: staff_client(AN),
    }


def _call(client, path, method):
    url = "/" + path.replace("{id}", "x_unknown0000000000000000").replace("{n}", "999999")
    if method == "get":
        return client.get(url)
    if isinstance(client, APIClient):
        return client.post(url, {}, format="json", headers={"Idempotency-Key": "matrix-probe"})
    return client.post(url, {}, content_type="application/json")


def test_nfr_con_05_six_principals_against_every_endpoint_match_the_matrix(principals):
    """Allowed = anything but 401/403; denied = 401 or 403. Records of another owner are
    covered by NFR-CON-01 ("not found")."""
    from django.core.cache import cache

    checked, mismatches = 0, []
    for (path, method), allowed in sorted(MATRIX.items()):
        if allowed == "public":
            continue
        # Every probe comes from one test IP; the per-IP rate limits are not under test here
        # and would otherwise answer 429 once the matrix has enough customer endpoints.
        cache.clear()
        for name in PRINCIPALS:
            status = _call(principals[name], path, method).status_code
            ok = status not in (401, 403) if name in allowed else status in (401, 403)
            checked += 1
            if not ok:
                mismatches.append(f"{method.upper()} /{path} as {name}: {status}")
    assert mismatches == []
    assert checked == 6 * sum(1 for v in MATRIX.values() if v != "public")
