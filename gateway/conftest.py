"""Shared fixtures for gateway tests."""
import json
import uuid
from pathlib import Path
from typing import ClassVar

import pytest
from django.core.cache import cache
from jsonschema import Draft202012Validator
from rest_framework.test import APIClient

from apps.merchants.models import ApiKey, Merchant

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"


DB_FIXTURES = ("db", "transactional_db", "django_db_reset_sequences",
               "django_db_serialized_rollback")


@pytest.fixture(autouse=True)
def _clear_risk_model_registry():
    """The learning loop's champion/challenger are cached per process (apps.risk.scoring);
    a test's rows are rolled back, so its cache must not outlive it."""
    from apps.risk import scoring

    scoring.active_model.cache_clear()
    yield
    scoring.active_model.cache_clear()


@pytest.fixture(autouse=True)
def _clear_throttle_cache(request):
    from apps.security import keyring

    # The cache is a database table (settings.CACHES), so only tests that may use the
    # database can (or need to) clear it; the rest never reach a throttle.
    db_fixtures = [name for name in DB_FIXTURES if name in request.fixturenames]
    uses_db = bool(db_fixtures) or request.node.get_closest_marker("django_db") is not None
    for name in db_fixtures:
        request.getfixturevalue(name)  # open the database before clearing the cache
    if uses_db:
        cache.clear()  # rate-limit counters must not leak between tests
    keyring.forget_all()  # data key rows roll back with each test
    yield
    keyring.forget_all()


def _validator(name):
    schema = json.loads((CONTRACTS / "schemas" / name).read_text())
    return Draft202012Validator(schema)


@pytest.fixture(scope="session")
def payment_schema():
    return _validator("payment.schema.json")


@pytest.fixture(scope="session")
def event_schema():
    """Validate a webhook event payload against contracts/events/<type>.schema.json."""
    from referencing import Registry, Resource

    docs = {}
    for folder in ("schemas", "events"):
        for path in (CONTRACTS / folder).glob("*.json"):
            doc = json.loads(path.read_text())
            docs[doc.get("$id", path.name)] = doc
    registry = Registry().with_resources(
        (uri, Resource.from_contents(doc)) for uri, doc in docs.items())

    def validate(payload):
        schema = json.loads((CONTRACTS / "events" / f"{payload['type']}.schema.json")
                            .read_text())
        Draft202012Validator(schema, registry=registry).validate(payload)

    return validate


@pytest.fixture(scope="session")
def error_schema():
    return _validator("error.schema.json")


class MerchantClient(APIClient):
    """An API client authenticated as one merchant, with its key and signing secret."""

    def __init__(self, merchant, api_key, plaintext, secret):
        super().__init__()
        self.merchant, self.api_key, self.key, self.secret = merchant, api_key, plaintext, secret
        self.credentials(HTTP_AUTHORIZATION=f"Bearer {plaintext}")

    def create_payment(self, body=None, key=None, **extra):
        body = body if body is not None else valid_body()
        headers = {} if key is False else {"Idempotency-Key": key or str(uuid.uuid4())}
        return self.post("/v1/payments", body, format="json", headers=headers, **extra)


def valid_body(**overrides):
    body = {
        "amount": 4590,
        "currency": "SGD",
        "order_reference": "ORD-10042",
        "return_url": "https://shop.localhost/orders/ORD-10042/return",
        "customer_email": "alex@example.com",
    }
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not ...}


@pytest.fixture
def make_merchant(db):
    def make(name="Demo Shop", status=Merchant.Status.ACTIVE, **fields):
        merchant = Merchant(name=name, status=status, **fields)
        secret = merchant.rotate_signing_secret()
        merchant.save()
        api_key, plaintext = ApiKey.issue(merchant)
        return MerchantClient(merchant, api_key, plaintext, secret)

    return make


@pytest.fixture
def shop(make_merchant):
    return make_merchant()


@pytest.fixture
def other_shop(make_merchant):
    return make_merchant(name="Other Shop")


# --- a stand-in for the processing simulator ------------------------------------------------

class FakeSimulator:
    """Answers /sim/* like the real simulator's test cards, through httpx.MockTransport.

    `mode` changes how replies come back: "normal", "bad_signature", "unsigned", "http_500".
    """

    APPROVE = frozenset({"4242424242424242", "5555555555554444", "4000000000003220"})
    APPROVE_PREFIXES = ("42424200", "55555500", "40003220")  # the demo ranges (SIMULATOR-CONTRACT.md 5)
    DECLINE: ClassVar = {"4000000000009995": "insufficient_funds",
                         "4000000000000002": "card_declined"}
    HANG = frozenset({"4000000000000119", "4000000000000127", "4000000000000135"})
    LOST_REQUEST = "4000000000000135"  # nothing recorded; the others record an approval

    def __init__(self, secret):
        self.secret = secret
        self.mode = "normal"
        self.requests = []  # (method, path, json body or None)
        self.records = {}
        self.auth_state = {}
        self.settle_mode = "normal"  # capture/cancel: "normal", "hang", "decline"
        self.refund_mode = "normal"  # refund: "normal", "hang", "lost", "decline"
        self.status_mode = "normal"  # status query: "normal", "fail" (503), "conflict"
        self.refunded = {}  # authorisation operation ID -> approved refund total
        self.cards = {}  # v2 issuing: card_id -> the bank's card
        self.threeds = {}  # v2 3-D Secure: three_ds_id -> the bank's authentication
        self.threeds_mode = "normal"  # "normal", "hang" (the bank does not answer)

    def record(self, body, status, reason=None):
        number = body["card"]["number"]
        return {
            "operation_id": body["operation_id"], "object": "operation", "type": "authorise",
            "status": status, "decline_reason": reason, "merchant_id": body["merchant_id"],
            "payment_id": body["payment_id"], "authorisation_id": None, "refund_id": None,
            "amount": body["amount"], "currency": "SGD",
            "card": {"brand": "visa" if number.startswith("4") else "mastercard",
                     "last4": number[-4:]},
            "simulator_reference": "sim_" + "S" * 24,
            "authorisation": ({"state": "open", "authorised_amount": body["amount"],
                               "captured_amount": 0, "refunded_amount": 0}
                              if status == "approved" else None),
            "recorded_at": "2026-11-13T05:00:00Z",
        }

    def reply(self, data, status=200):
        import httpx

        from apps.events import signing

        raw = json.dumps(data).encode()
        headers = {"Content-Type": "application/json"}
        if self.mode == "http_500":
            return httpx.Response(500, content=b"{}", headers=headers)
        if self.mode != "unsigned":
            secret = "wrong-secret" if self.mode == "bad_signature" else self.secret
            headers["Simulator-Signature"] = signing.sign(raw, secret)
        return httpx.Response(status, content=raw, headers=headers)

    def __call__(self, request):
        import httpx

        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        assert request.headers["Authorization"].startswith("Bearer simk_")
        if request.url.path == "/sim/authorise":
            number = body["card"]["number"]
            if number in self.HANG:
                if number != self.LOST_REQUEST:  # lost response: recorded, never answered
                    self.records[body["operation_id"]] = self.record(body, "approved")
                raise httpx.ReadTimeout("no reply", request=request)
            shopper_bank = number[:2] == "51" and number[6:8] == "00"  # 51BB BB00 (v2)
            if number in self.APPROVE or (number not in self.DECLINE and (
                    number.startswith(self.APPROVE_PREFIXES) or shopper_bank)):
                rec = self.record(body, "approved")
            else:
                rec = self.record(body, "declined", self.DECLINE.get(number, "card_declined"))
            self.records[body["operation_id"]] = rec
            return self.reply(rec)
        if request.url.path in ("/sim/capture", "/sim/cancel"):
            if self.settle_mode == "hang":
                raise httpx.ReadTimeout("no reply", request=request)
            return self.reply(self.settle(request.url.path.rsplit("/", 1)[1], body))
        if request.url.path == "/sim/refund":
            return self.refund(request, body)
        if request.url.path.startswith("/sim/operations/"):
            return self.status_query(request.url.path.rsplit("/", 1)[1],
                                     request.url.params.get("type"))
        if request.url.path.startswith("/sim/issuing/cards"):
            return self.issuing(request, body)
        if request.url.path.startswith("/sim/3ds/"):
            return self.three_ds(request, body)
        raise AssertionError(f"unexpected call {request.url.path}")

    THREE_DS_CODE = "135790"  # the code SecurePay Bank "texts" in this fake

    def three_ds(self, request, body):
        """v2 3-D Secure: SecurePay Bank's ACS (simulator apps/threeds)."""
        import httpx

        if self.threeds_mode == "hang":
            raise httpx.ReadTimeout("no reply", request=request)
        parts = request.url.path.split("/")[3:]  # after /sim/3ds
        if parts == ["authenticate"]:
            number = body["card"]["number"]
            if not (number == "4000000000003220" or number.startswith(("40003220", "457120"))):
                return self.reply({"object": "three_ds", "payment_id": body["payment_id"],
                                   "status": "not_enrolled"})
            existing = next((a for a in self.threeds.values()
                             if a["payment_id"] == body["payment_id"]), None)
            if existing:
                return self.reply(existing)
            three_ds_id = "3ds_" + str(len(self.threeds)).zfill(24)
            auth = self.threeds[three_ds_id] = {
                "object": "three_ds", "three_ds_id": three_ds_id,
                "payment_id": body["payment_id"], "status": "pending", "trans_status": None,
                "acs": "SecurePay Bank", "phone_hint": "•••• 21", "last4": number[-4:],
                "amount": body["amount"], "attempts_remaining": 3,
                "expires_at": "2099-01-01T00:00:00Z", "eci": None, "cavv": None,
                "test_code": self.THREE_DS_CODE}
            return self.reply(auth, 201)
        auth = self.threeds[parts[0]]
        if parts[1:] == ["challenge"] and auth["status"] == "pending":
            if body["code"] == self.THREE_DS_CODE:
                auth.update(status="authenticated", trans_status="Y", eci="05",
                            cavv="Q" * 27 + "=", test_code=None)
            else:
                auth["attempts_remaining"] -= 1
                if auth["attempts_remaining"] == 0:
                    auth.update(status="failed", trans_status="N", test_code=None)
        return self.reply(auth)

    def issuing(self, request, body):
        """v2 issuing: SecurePay Bank's card-management API (simulator apps/issuing)."""
        parts = request.url.path.split("/")[4:]  # after /sim/issuing/cards
        if request.method == "POST" and not parts:
            card = self.cards.get(body["card_id"])
            if card is not None:
                return self.reply(card)
            card = self.cards[body["card_id"]] = {
                "object": "issued_card", "card_id": body["card_id"],
                "merchant_id": body["merchant_id"], "last4": "4242", "expiry_month": 11,
                "expiry_year": 2029, "spending_limit": body["spending_limit"], "spent": 0,
                "remaining": body["spending_limit"], "single_use": body["single_use"],
                "status": "active", "authorisations": []}
            return self.reply({**card, "number": "4571200000004242", "cvc": "321"}, 201)
        card = self.cards.get(parts[0])
        if card is None:
            return self.reply({"error": {"code": "not_found", "message": "x"}}, 404)
        if parts[1:] == ["cancel"]:
            card["status"] = "cancelled"
        return self.reply(card)

    def status_query(self, operation_id, op_type):
        """Section 6: the record, or a new no_record entry that retires the ID."""
        import httpx

        if self.status_mode == "fail":
            return httpx.Response(503, content=b"{}")
        if self.status_mode == "conflict":
            rec = self.records.get(operation_id) or {"operation_id": operation_id}
            return self.reply({"error": {"code": "operation_id_conflict", "message": "x"},
                               "operation": rec}, status=409)
        if operation_id not in self.records:
            self.records[operation_id] = {
                "operation_id": operation_id, "object": "operation",
                "type": op_type or "authorise", "status": "no_record", "decline_reason": None,
                "merchant_id": None, "payment_id": None, "authorisation_id": None,
                "refund_id": None, "amount": None, "currency": None, "card": None,
                "simulator_reference": None, "authorisation": None,
                "recorded_at": "2026-11-13T05:00:00Z"}
        return self.reply(self.records[operation_id])

    def refund(self, request, body):
        import httpx

        if body["operation_id"] in self.records:
            return self.reply(self.records[body["operation_id"]])
        if self.refund_mode == "lost":
            raise httpx.ReadTimeout("no reply", request=request)
        auth_id = body["authorisation_id"]
        auth = self.records.get(auth_id)
        captured = next((r["amount"] for r in self.records.values()
                         if r.get("type") == "capture" and r.get("authorisation_id") == auth_id
                         and r["status"] == "approved"), None)
        done = self.refunded.get(auth_id, 0)
        reason = None
        if auth is None:
            reason = "authorisation_not_found"
        elif captured is None:
            reason = "authorisation_not_captured"
        elif done + body["amount"] > captured or self.refund_mode == "decline":
            reason = "refund_exceeds_captured"
        if reason is None:
            self.refunded[auth_id] = done + body["amount"]
        rec = {**(auth or {}), "operation_id": body["operation_id"], "type": "refund",
               "status": "declined" if reason else "approved", "decline_reason": reason,
               "authorisation_id": auth_id, "refund_id": body["refund_id"],
               "amount": body["amount"], "card": None,
               "simulator_reference": "sim_" + "U" * 24}
        self.records[body["operation_id"]] = rec
        if self.refund_mode == "hang":
            raise httpx.ReadTimeout("no reply", request=request)
        return self.reply(rec)

    def settle(self, op_type, body):
        """Capture or cancel against an authorisation this fake approved."""
        if body["operation_id"] in self.records:
            return self.records[body["operation_id"]]
        auth = self.records.get(body["authorisation_id"])
        state = self.auth_state.get(body["authorisation_id"], "open")
        reason = None
        if auth is None or auth["status"] != "approved":
            reason = "authorisation_not_found"
        elif state != "open" or self.settle_mode == "decline":
            reason = "authorisation_not_open"
        amount = body.get("amount", auth["amount"] if auth else 0)
        if reason is None:
            self.auth_state[body["authorisation_id"]] = (
                "captured" if op_type == "capture" else "cancelled")
        rec = {**(auth or {}), "operation_id": body["operation_id"], "type": op_type,
               "status": "declined" if reason else "approved", "decline_reason": reason,
               "authorisation_id": body["authorisation_id"], "amount": amount, "card": None,
               "simulator_reference": "sim_" + "T" * 24}
        self.records[body["operation_id"]] = rec
        return rec


@pytest.fixture
def fake_sim(settings, monkeypatch):
    import httpx

    from apps.payments import processor

    sim = FakeSimulator(settings.SIMULATOR_SIGNING_SECRET)
    monkeypatch.setattr(processor, "TRANSPORT", httpx.MockTransport(sim))
    return sim


# --- staff dashboard ------------------------------------------------------------------------

STAFF_PASSWORD = "correct horse battery staple"


def make_staff_user(email, role, password=STAFF_PASSWORD, merchant=None, with_device=True):
    """A staff account; with a confirmed authenticator device unless with_device=False."""
    from django.contrib.auth import get_user_model
    from django_otp.plugins.otp_totp.models import TOTPDevice

    from apps.accounts.models import StaffAccount

    user = get_user_model().objects.create_user(username=email, email=email,
                                                password=password, first_name="Test")
    account = StaffAccount.objects.create(user=user, role=role, merchant=merchant)
    if with_device:
        TOTPDevice.objects.create(user=user, name="Authenticator app", confirmed=True)
    return account


def current_totp(account, device=None):
    from django_otp.oath import TOTP
    from django_otp.plugins.otp_totp.models import TOTPDevice

    device = device or TOTPDevice.objects.get(user=account.user, confirmed=True)
    return f"{TOTP(device.bin_key, device.step, device.t0, device.digits).token():06d}"


@pytest.fixture
def staff_client(db):
    """staff_client(role, merchant=None) -> a signed-in django.test.Client (.account set)."""
    from django.test import Client

    counter = iter(range(1000))

    def make(role, merchant=None):
        account = make_staff_user(f"{role}{next(counter)}@securepay.sg", role,
                                  merchant=merchant)
        client = Client()
        r = client.post("/internal/auth/login", {"email": account.user.email,
                                                 "password": STAFF_PASSWORD},
                        content_type="application/json")
        assert r.status_code == 200, r.content
        r = client.post("/internal/auth/totp", {"code": current_totp(account)},
                        content_type="application/json")
        assert r.status_code == 200, r.content
        client.account = account
        return client

    return make
