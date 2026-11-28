"""Screening in checkout (sprint 4): allow, verify by code (S2), block (S3), outage.

The simulator is the httpx fake from conftest; emails go to Django's locmem outbox.
"""
import re
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.checkout.models import VerificationChallenge
from apps.events.emails import send_due
from apps.events.models import OutboxEvent
from apps.payments.models import Operation, Payment
from apps.risk.models import RiskListEntry, ScreeningEvent
from apps.vault.models import StoredCard

# Real commits: the email and the vault clean-up run after the commit, as in production.
pytestmark = pytest.mark.django_db(databases=["default", "vault"], transaction=True)

PAN = "4242424242424242"
BLOCKLISTED = "4000000000000101"
BIG = 60_000  # S$600, over the S$500 verification rule


def card(number=PAN):
    return {"number": number, "expiry_month": 12, "expiry_year": 2030, "cvc": "123", "privacy_consent": True}


@pytest.fixture
def browser():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def make_payment(shop):
    from conftest import valid_body

    def make(**overrides):
        r = shop.create_payment(valid_body(**overrides))
        assert r.status_code == 201, r.content
        return Payment.objects.get(public_id=r.json()["id"])

    return make


@pytest.fixture
def lists(db):
    call_command("seed_risk_lists", verbosity=0)


def url(payment, suffix=""):
    return f"/checkout/{payment.checkout_session_id}{suffix}"


def pay(browser, payment, number=PAN, **extra):
    r = browser.post(url(payment, "/card"), card(number), format="json")
    assert r.status_code == 200, r.content
    return browser.post(url(payment, "/submit"), {}, format="json", **extra)


class _WorkerOutbox:
    """pytest-django's mailoutbox, read after the worker has sent what is queued: codes go
    through the email outbox (apps.events.emails) like every other email."""

    def __init__(self, outbox):
        self._outbox = outbox

    def _sent(self):
        send_due()
        return self._outbox

    def __getitem__(self, i):
        return self._sent()[i]

    def __len__(self):
        return len(self._sent())

    def __iter__(self):
        return iter(self._sent())


@pytest.fixture
def mailoutbox(mailoutbox):
    return _WorkerOutbox(mailoutbox)


def code_from(mail):
    return re.search(r"verification code is (\d{6})", mail.body).group(1)


def authorisations(fake_sim):
    """Calls that reach the card network; v2 3-D Secure first asks the bank whether the card
    is enrolled, which is not an authorisation (docs/v2/THREE-DS.md)."""
    return [r for r in fake_sim.requests if r[1] != "/sim/3ds/authenticate"]


def events(payment):
    return [e.type for e in OutboxEvent.objects.filter(payment=payment).order_by("sequence")]


# --- allow ---------------------------------------------------------------------------------

def test_low_risk_is_screened_allowed_and_recorded(browser, make_payment, fake_sim):
    payment = make_payment()
    data = pay(browser, payment, REMOTE_ADDR="203.0.113.9").json()
    assert data["status"] == "authorised" and data["verification"] is None
    record = ScreeningEvent.objects.get(payment=payment)
    assert (record.outcome, record.score, record.model_version) == ("allow", 0.0, "stub-0")
    assert record.features["amount_cents"] == 4590
    assert record.card_fingerprint == Payment.objects.get(pk=payment.pk).card_fingerprint
    assert len(record.email_hash) == 64 and len(record.ip_hash) == 64
    audit = AuditEvent.objects.get(target_id=payment.public_id, action="payment.submitted")
    assert audit.details["screening"] == "allow"
    assert audit.details["screening_id"] == record.public_id


# --- S2: simulated verification ----------------------------------------------------------------

def test_s2_risky_attempt_verified_by_code_then_authorised(browser, make_payment, fake_sim,
                                                         mailoutbox, shop, payment_schema,
                                                         event_schema):
    payment = make_payment(amount=BIG)
    data = pay(browser, payment).json()
    assert (data["status"], data["result"]) == ("verification_required",
                                                "verification_required")
    v = data["verification"]
    assert v["sent_to"] == "a•••@example.com" and v["attempts_remaining"] == 3
    assert v["code_expires_at"] and v["resend_available_at"]
    assert authorisations(fake_sim) == []  # nothing is authorised before the code
    assert StoredCard.objects.using("vault").count() == 1  # kept, encrypted, until then

    [mail] = mailoutbox
    assert mail.to == ["alex@example.com"]
    code = code_from(mail)
    assert code not in mail.subject and "S$600.00" in mail.body  # codes never in subjects
    assert mail.subject == "Your SecurePay verification code"
    # The merchant sees verification_required, with a webhook.
    api = shop.get(f"/v1/payments/{payment.public_id}").json()
    payment_schema.validate(api)
    assert api["status"] == "verification_required"
    assert api["screening"] == {"decision": "verify"}

    data = browser.post(url(payment, "/verify"), {"code": code}, format="json").json()
    assert (data["status"], data["result"]) == ("authorised", "approved")
    assert len(authorisations(fake_sim)) == 1
    assert not StoredCard.objects.using("vault").exists()
    assert events(payment) == ["payment.verification_required", "payment.processing",
                               "payment.authorised"]
    for e in OutboxEvent.objects.filter(payment=payment):
        event_schema(e.payload)
    record = ScreeningEvent.objects.get(payment=payment)
    assert (record.outcome, record.reasons) == ("verify", ["high_amount"])
    actions = set(AuditEvent.objects.filter(target_id=payment.public_id)
                  .values_list("action", flat=True))
    assert {"payment.verification_required", "payment.verified", "payment.submitted"} <= actions
    # The code itself is nowhere but in the email.
    assert code not in str(list(AuditEvent.objects.values_list("details", flat=True)))
    assert VerificationChallenge.objects.get(payment=payment).code_hash != code


def test_review_list_email_is_verified_even_for_small_amounts(browser, make_payment, fake_sim,
                                                              mailoutbox, lists):
    payment = make_payment(customer_email="review@example.com")
    assert pay(browser, payment).json()["status"] == "verification_required"
    assert ScreeningEvent.objects.get(payment=payment).reasons == ["review_list_email"]


def test_three_wrong_codes_block_the_attempt(browser, make_payment, fake_sim, mailoutbox,
                                             shop):
    payment = make_payment(amount=BIG)
    pay(browser, payment)
    wrong = "000000" if code_from(mailoutbox[0]) != "000000" else "111111"
    for left in (2, 1):
        r = browser.post(url(payment, "/verify"), {"code": wrong}, format="json")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "code_incorrect"
        assert f"{left} attempt" in r.json()["error"]["message"]
        assert browser.get(url(payment)).json()["verification"]["attempts_remaining"] == left
    r = browser.post(url(payment, "/verify"), {"code": wrong}, format="json")
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["result"]) == ("blocked", "verification_failed")
    assert authorisations(fake_sim) == []
    assert not StoredCard.objects.using("vault").exists()
    api = shop.get(f"/v1/payments/{payment.public_id}").json()
    assert api["outcome"]["reason"] == "verification_failed"
    assert events(payment)[-1] == "payment.blocked"
    # The right code no longer helps.
    r = browser.post(url(payment, "/verify"), {"code": code_from(mailoutbox[0])}, format="json")
    assert r.json()["status"] == "blocked" and authorisations(fake_sim) == []


@pytest.mark.parametrize("code", ["12345", "abcdef", 123456, None, "1234567"])
def test_malformed_codes_do_not_count(browser, make_payment, fake_sim, mailoutbox, code):
    payment = make_payment(amount=BIG)
    pay(browser, payment)
    r = browser.post(url(payment, "/verify"), {"code": code}, format="json")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_code"
    assert VerificationChallenge.objects.get(payment=payment).wrong_attempts == 0


def test_expired_code_is_rejected_and_a_new_code_replaces_it(browser, make_payment, fake_sim,
                                                           mailoutbox):
    payment = make_payment(amount=BIG)
    pay(browser, payment)
    old = code_from(mailoutbox[0])
    past = timezone.now() - timedelta(seconds=1)
    VerificationChallenge.objects.filter(payment=payment).update(
        code_expires_at=past, code_sent_at=past - timedelta(minutes=10))
    r = browser.post(url(payment, "/verify"), {"code": old}, format="json")
    assert r.status_code == 400 and r.json()["error"]["code"] == "code_expired"
    assert VerificationChallenge.objects.get(payment=payment).wrong_attempts == 0

    r = browser.post(url(payment, "/verify/resend"), {}, format="json")
    assert r.status_code == 200 and len(mailoutbox) == 2
    new = code_from(mailoutbox[1])
    if new != old:
        r = browser.post(url(payment, "/verify"), {"code": old}, format="json")
        assert r.json()["error"]["code"] == "code_incorrect"  # the old code is replaced
    r = browser.post(url(payment, "/verify"), {"code": new}, format="json")
    assert r.json()["status"] == "authorised"


def test_resend_has_a_cooldown_and_a_limit(browser, make_payment, fake_sim, mailoutbox,
                                           settings):
    payment = make_payment(amount=BIG)
    pay(browser, payment)
    r = browser.post(url(payment, "/verify/resend"), {}, format="json")
    assert r.status_code == 429 and r.json()["error"]["code"] == "resend_too_soon"
    assert r["Retry-After"]
    for _ in range(settings.GATEWAY_RULES["VERIFICATION_MAX_CODES"] - 1):
        VerificationChallenge.objects.filter(payment=payment).update(
            code_sent_at=timezone.now() - timedelta(minutes=1))
        assert browser.post(url(payment, "/verify/resend"), {},
                            format="json").status_code == 200
    VerificationChallenge.objects.filter(payment=payment).update(
        code_sent_at=timezone.now() - timedelta(minutes=1))
    r = browser.post(url(payment, "/verify/resend"), {}, format="json")
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_codes"
    assert browser.get(url(payment)).json()["verification"]["resend_available_at"] is None
    assert len(mailoutbox) == settings.GATEWAY_RULES["VERIFICATION_MAX_CODES"]


def test_wrong_codes_count_across_new_codes(browser, make_payment, fake_sim, mailoutbox):
    payment = make_payment(amount=BIG)
    pay(browser, payment)
    browser.post(url(payment, "/verify"), {"code": "999999"}, format="json")
    VerificationChallenge.objects.filter(payment=payment).update(
        code_sent_at=timezone.now() - timedelta(minutes=1))
    browser.post(url(payment, "/verify/resend"), {}, format="json")
    assert browser.get(url(payment)).json()["verification"]["attempts_remaining"] in (2, 3)
    assert VerificationChallenge.objects.get(payment=payment).wrong_attempts >= 1


def test_session_ending_during_verification_blocks(browser, make_payment, fake_sim,
                                                   mailoutbox):
    payment = make_payment(amount=BIG)
    pay(browser, payment)
    Payment.objects.filter(pk=payment.pk).update(
        checkout_expires_at=timezone.now() - timedelta(seconds=1))
    data = browser.get(url(payment)).json()
    assert (data["status"], data["result"]) == ("blocked", "verification_failed")
    payment.refresh_from_db()
    assert payment.outcome_reason == "verification_expired"
    assert not StoredCard.objects.using("vault").exists()
    r = browser.post(url(payment, "/verify"), {"code": code_from(mailoutbox[0])}, format="json")
    assert r.json()["status"] == "blocked" and authorisations(fake_sim) == []


def test_worker_blocks_verifications_whose_session_ended(browser, make_payment, fake_sim,
                                                         mailoutbox):
    from apps.payments import jobs

    payment = make_payment(amount=BIG)
    pay(browser, payment)
    Payment.objects.filter(pk=payment.pk).update(
        checkout_expires_at=timezone.now() - timedelta(seconds=1))
    assert jobs.expire_checkout_sessions() == 1
    payment.refresh_from_db()
    assert (payment.status, payment.outcome_reason) == ("blocked", "verification_expired")
    assert events(payment)[-1] == "payment.blocked"


def test_verify_before_screening_or_on_an_allowed_payment_changes_nothing(
        browser, make_payment, fake_sim):
    payment = make_payment()
    r = browser.post(url(payment, "/verify"), {"code": "123456"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "created"
    pay(browser, payment)
    r = browser.post(url(payment, "/verify/resend"), {}, format="json")
    assert r.json()["status"] == "authorised" and len(fake_sim.requests) == 1


# --- S3: blocked -----------------------------------------------------------------------------

def test_s3_blocklisted_card_blocked_with_reasons_for_the_analyst(browser, make_payment,
                                                                 fake_sim, lists, shop,
                                                                 event_schema):
    payment = make_payment()
    data = pay(browser, payment, BLOCKLISTED).json()
    assert (data["status"], data["result"]) == ("blocked", "not_completed")
    assert "fraud" not in str(data) and "blocklist" not in str(data)  # never told to the customer
    assert fake_sim.requests == []
    assert not StoredCard.objects.using("vault").exists()
    api = shop.get(f"/v1/payments/{payment.public_id}").json()
    assert api["screening"] == {"decision": "block"}
    assert api["outcome"]["reason"] == "fraud_rule"
    [event] = OutboxEvent.objects.filter(payment=payment)
    assert event.type == "payment.blocked"
    event_schema(event.payload)
    record = ScreeningEvent.objects.get(payment=payment)
    assert (record.outcome, record.reasons) == ("block", ["blocklisted_card"])
    assert record.rules_version == "rules-0.1" and record.model_version == "stub-0"
    audit = AuditEvent.objects.get(target_id=payment.public_id, action="payment.blocked")
    assert audit.details["reasons"] == ["blocklisted_card"]
    assert audit.details["outcome_reason"] == "fraud_rule"


def test_blocklisted_email_is_blocked(browser, make_payment, fake_sim, lists):
    payment = make_payment(customer_email="Blocked@Example.com")
    assert pay(browser, payment).json()["status"] == "blocked"
    assert ScreeningEvent.objects.get(payment=payment).reasons == ["blocklisted_email"]


def test_s3_burst_of_attempts_on_one_card_is_blocked(browser, make_payment, fake_sim):
    for _ in range(5):
        assert pay(browser, make_payment(), "4000000000000002").json()["status"] == "declined"
    payment = make_payment()
    data = pay(browser, payment, "4000000000000002").json()
    assert data["status"] == "blocked"
    record = ScreeningEvent.objects.get(payment=payment)
    assert record.reasons == ["card_attempt_burst"]
    assert record.features["card_attempts_10m"] == 5
    assert len(fake_sim.requests) == 5


def test_inactive_list_entries_are_ignored(browser, make_payment, fake_sim, lists):
    RiskListEntry.objects.update(active=False)
    assert pay(browser, make_payment(), BLOCKLISTED).json()["status"] == "declined"


# --- screening unavailable (FR-FRD-04, NFR-AVA-04) ---------------------------------------------

@pytest.mark.parametrize("mode", ["error", "slow"])
def test_outage_blocks_as_not_screened_and_never_reaches_the_simulator(
        browser, make_payment, fake_sim, settings, shop, mode):
    settings.DEBUG = True  # the switch is DEBUG-only; pytest runs with DEBUG off
    settings.RISK_SIMULATE_OUTAGE = mode
    payment = make_payment()
    data = pay(browser, payment).json()
    assert (data["status"], data["result"]) == ("blocked", "unavailable")
    assert fake_sim.requests == []
    api = shop.get(f"/v1/payments/{payment.public_id}").json()
    assert api["screening"] == {"decision": "not_screened"}
    assert api["outcome"]["reason"] == "screening_unavailable"
    assert "not a fraud finding" in api["outcome"]["message"]
    record = ScreeningEvent.objects.get(payment=payment)
    assert (record.outcome, record.reasons) == ("not_screened", ["screening_unavailable"])
    assert record.error and record.score is None
    assert not StoredCard.objects.using("vault").exists()


def test_slow_database_query_is_cut_off_by_the_budget(browser, make_payment, fake_sim,
                                                      monkeypatch):
    from apps.risk import features

    def slow_build(self, attempt):
        with connection.cursor() as cur:
            cur.execute("SELECT pg_sleep(2)")
        return {}

    monkeypatch.setattr(features.SqlFeatures, "build", slow_build)
    payment = make_payment()
    data = pay(browser, payment).json()
    assert (data["status"], data["result"]) == ("blocked", "unavailable")
    record = ScreeningEvent.objects.get(payment=payment)
    assert record.elapsed_ms < 1500  # cancelled at ~500 ms, not after the full 2 s
    # The connection is healthy and back to no statement timeout.
    with connection.cursor() as cur:
        cur.execute("SHOW statement_timeout")
        assert cur.fetchone()[0] == "0"


def test_outage_switch_only_works_in_debug(browser, make_payment, fake_sim, settings):
    settings.RISK_SIMULATE_OUTAGE = "error"
    settings.DEBUG = False
    assert pay(browser, make_payment()).json()["status"] == "authorised"


# --- data rules ------------------------------------------------------------------------------

def test_screening_runs_once_per_attempt(browser, make_payment, fake_sim):
    payment = make_payment(amount=BIG)
    pay(browser, payment)
    browser.post(url(payment, "/submit"), {}, format="json")
    assert ScreeningEvent.objects.filter(payment=payment).count() == 1


def test_client_ip_comes_from_the_trusted_proxy(browser, make_payment, fake_sim, settings):
    from apps.risk import hashing

    settings.TRUSTED_PROXY_HOPS = 1
    payment = make_payment()
    pay(browser, payment, HTTP_X_FORWARDED_FOR="10.9.9.9, 198.51.100.7")
    assert ScreeningEvent.objects.get(payment=payment).ip_hash == hashing.ip_hash("198.51.100.7")


def test_no_card_number_email_or_code_in_screening_or_verification_rows(
        browser, make_payment, fake_sim, mailoutbox):
    """CONTRIBUTING rule 1, extended to the new tables."""
    payment = make_payment(amount=BIG)
    pay(browser, payment)
    code = code_from(mailoutbox[0])
    browser.post(url(payment, "/verify"), {"code": code}, format="json")
    with connection.cursor() as cur:
        for table in ("risk_screeningevent", "checkout_verificationchallenge",
                      "audit_auditevent", "events_outboxevent", "payments_operation"):
            cur.execute(f"SELECT coalesce(string_agg(row_to_json(t)::text, ' '), '') "
                        f"FROM {table} t")
            dump = cur.fetchone()[0]
            assert PAN not in dump, table
            if table in ("risk_screeningevent", "checkout_verificationchallenge"):
                assert "alex@example.com" not in dump, table
            assert f'"{code}"' not in dump, table
    assert Operation.objects.get(payment=payment).status == "succeeded"
