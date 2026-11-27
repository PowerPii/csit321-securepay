"""Double-entry ledger (v2, Sprint 1): postings, balances and the database rules."""
import uuid
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction

from apps.events.models import OutboxEvent
from apps.ledger import checks, fees, posting
from apps.ledger.models import JournalEntry, JournalLine, LedgerAccount
from apps.payments.models import Payment
from apps.refunds.tests import captured_payment, refund

pytestmark = pytest.mark.django_db(databases=["default", "vault"])

Kind = LedgerAccount.Kind


def lines_of(entry):
    return {(line.account.kind, line.memo): line.amount for line in entry.lines.all()}


# --- the split ------------------------------------------------------------------------------

@pytest.mark.parametrize(("gross", "fee", "reserve", "net"), [
    (12000, 378, 1200, 10422),  # S$120: 2.9% = 348, + 30
    (4590, 163, 459, 3968),  # 133.11 rounds to 133
    (50, 31, 5, 14),  # the smallest payment: 1.45 rounds to 1
    (1_000_000, 29030, 100000, 870970),  # the largest
])
def test_split_adds_up(gross, fee, reserve, net):
    s = fees.split(gross)
    assert (s.fee, s.reserve, s.net) == (fee, reserve, net)
    assert s.fee + s.reserve + s.net == gross


def test_split_never_goes_negative(settings):
    settings.GATEWAY_RULES = {**settings.GATEWAY_RULES, "FEE_FIXED_CENTS": 100}
    s = fees.split(60)
    assert (s.fee, s.reserve, s.net) == (60, 0, 0)
    with pytest.raises(ValueError):
        fees.split(0)


def test_split_reads_gateway_rules(settings):
    settings.GATEWAY_RULES = {**settings.GATEWAY_RULES, "FEE_PERCENT_BPS": 0,
                              "FEE_FIXED_CENTS": 0, "RESERVE_BPS": 0}
    assert fees.split(4590) == fees.Split(4590, 0, 0, 4590)


# --- captures and refunds post entries ------------------------------------------------------

def test_capture_posts_a_balanced_entry(shop, fake_sim):
    payment = captured_payment(shop)
    entry = JournalEntry.objects.get(kind="capture", source_id=payment.public_id)
    assert entry.payment == payment and entry.merchant == shop.merchant
    assert entry.public_id.startswith("jrn_") and not entry.backfilled
    assert entry.occurred_at == payment.captured_at
    assert lines_of(entry) == {
        ("network_clearing", "gross"): 4590,
        ("fee_revenue", "fee"): -163,
        ("merchant_reserve", "reserve"): -459,
        ("merchant_available", "net"): -3968,
    }
    assert sum(lines_of(entry).values()) == 0
    assert posting.merchant_balances(shop.merchant) == {"available": 3968, "reserve": 459}
    # Same transaction as the outbox row: both exist together.
    assert OutboxEvent.objects.filter(payment=payment, type="payment.captured").exists()
    assert checks.problems() == []


def test_partial_capture_posts_the_captured_amount(shop, fake_sim):
    payment = captured_payment(shop, amount=2000)
    entry = JournalEntry.objects.get(kind="capture", payment=payment)
    assert lines_of(entry)[("network_clearing", "gross")] == 2000


def test_refund_reverses_from_available(shop, fake_sim):
    payment = captured_payment(shop)
    r = refund(shop, payment, 1500)
    assert r.status_code == 201, r.content
    entry = JournalEntry.objects.get(kind="refund", source_id=r.json()["id"])
    assert entry.refund.public_id == r.json()["id"] and entry.payment == payment
    assert lines_of(entry) == {("merchant_available", "refund"): 1500,
                               ("network_clearing", "refund"): -1500}
    # The fee is not given back; the reserve is untouched.
    assert posting.merchant_balances(shop.merchant) == {"available": 3968 - 1500,
                                                        "reserve": 459}
    clearing = LedgerAccount.objects.get(kind=Kind.NETWORK_CLEARING)
    assert clearing.balance() == 4590 - 1500
    assert LedgerAccount.objects.get(kind=Kind.FEE_REVENUE).balance() == 163
    assert checks.problems() == []



def test_balances_are_per_merchant(shop, other_shop, fake_sim):
    captured_payment(shop)
    captured_payment(other_shop, amount=1000)
    assert posting.merchant_balances(shop.merchant)["available"] == 3968
    other = fees.split(1000)
    assert posting.merchant_balances(other_shop.merchant) == {"available": other.net,
                                                              "reserve": other.reserve}
    # Gateway-wide accounts are shared: one clearing and one fee account.
    assert LedgerAccount.objects.filter(kind=Kind.NETWORK_CLEARING).count() == 1
    assert LedgerAccount.objects.filter(merchant=shop.merchant).count() == 2


def test_posting_twice_for_the_same_source_returns_the_first_entry(shop, fake_sim):
    payment = captured_payment(shop)
    first = JournalEntry.objects.get(payment=payment)
    with transaction.atomic():
        again = posting.post_capture(payment)
    assert again.pk == first.pk
    assert JournalEntry.objects.filter(payment=payment).count() == 1


def test_a_failing_post_rolls_the_capture_back(shop, fake_sim, monkeypatch):
    from apps.payments import settlement

    def broken(payment):
        raise RuntimeError("ledger down")

    from conftest import valid_body

    monkeypatch.setattr(settlement, "post_capture", broken)
    payment = Payment.objects.get(public_id=shop.create_payment(valid_body()).json()["id"])
    from rest_framework.test import APIClient

    browser = APIClient()
    base = f"/checkout/{payment.checkout_session_id}"
    browser.post(f"{base}/card", {"number": "4242424242424242", "expiry_month": 12,
                                  "expiry_year": 2030, "cvc": "123",
                                  "privacy_consent": True}, format="json")
    browser.post(f"{base}/submit", {}, format="json")
    r = shop.post(f"/v1/payments/{payment.public_id}/capture", {}, format="json",
                  headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 500
    payment.refresh_from_db()
    assert payment.status == "authorised" and payment.captured_amount == 0
    assert not OutboxEvent.objects.filter(payment=payment, type="payment.captured").exists()
    assert not JournalEntry.objects.exists()
    # The simulator did capture, so the operation stays open and reconciliation settles it
    # (and posts the entry) later, exactly as for a lost answer.
    operation = payment.operations.get(type="capture")
    assert operation.status == "pending"

    monkeypatch.setattr(settlement, "post_capture", posting.post_capture)  # ledger back up
    from datetime import timedelta

    from django.utils import timezone

    from apps.payments import reconciliation

    reconciliation.recover_stale_pending(now=timezone.now() + timedelta(hours=1))
    reconciliation.check(operation.pk)
    payment.refresh_from_db()
    assert payment.status == "captured"
    assert JournalEntry.objects.filter(kind="capture", payment=payment).count() == 1
    assert checks.problems() == []


# --- the rules ------------------------------------------------------------------------------

def test_post_refuses_unbalanced_or_one_sided_entries(shop, fake_sim):
    payment = captured_payment(shop)
    clearing = posting.account(Kind.NETWORK_CLEARING)
    fee = posting.account(Kind.FEE_REVENUE)
    with transaction.atomic():
        with pytest.raises(ValueError, match="does not balance"):
            posting.post(kind="capture", source_id="pay_x", payment=payment,
                         merchant=shop.merchant, lines=[(clearing, 100, "gross"),
                                                        (fee, -99, "fee")])
        with pytest.raises(ValueError, match="two non-zero lines"):
            posting.post(kind="capture", source_id="pay_x", payment=payment,
                         merchant=shop.merchant, lines=[(clearing, 0, "gross"),
                                                        (fee, 0, "fee")])


def test_post_must_run_inside_a_transaction(shop, fake_sim):
    payment = captured_payment(shop)
    clearing = posting.account(Kind.NETWORK_CLEARING)
    fee = posting.account(Kind.FEE_REVENUE)
    lines = [(clearing, 1, "gross"), (fee, -1, "fee")]
    # pytest-django wraps each test in a transaction, so look like autocommit instead.
    conn = transaction.get_connection()
    atomic, conn.in_atomic_block = conn.in_atomic_block, False
    try:
        with pytest.raises(RuntimeError, match="inside the state change"):
            posting.post(kind="capture", source_id="pay_y", payment=payment,
                         merchant=shop.merchant, lines=lines)
    finally:
        conn.in_atomic_block = atomic


def _insert_entry(payment, merchant, source_id):
    entry = JournalEntry(public_id=f"jrn_{source_id}", kind="capture", source_id=source_id,
                         payment=payment, merchant=merchant, occurred_at=payment.captured_at)
    entry.save()
    return entry


def test_database_refuses_an_unbalanced_entry_at_commit(shop, fake_sim):
    payment = captured_payment(shop)
    clearing = posting.account(Kind.NETWORK_CLEARING)
    fee = posting.account(Kind.FEE_REVENUE)
    with pytest.raises(DatabaseError, match="does not balance"), transaction.atomic():
        entry = _insert_entry(payment, shop.merchant, "bad1")
        JournalLine.objects.create(entry=entry, account=clearing, amount=100, memo="gross")
        JournalLine.objects.create(entry=entry, account=fee, amount=-99, memo="fee")
        with connection.cursor() as cur:  # what COMMIT would do
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_database_refuses_an_entry_without_lines(shop, fake_sim):
    payment = captured_payment(shop)
    with pytest.raises(DatabaseError, match="fewer than two lines"), transaction.atomic():
        _insert_entry(payment, shop.merchant, "bad2")
        with connection.cursor() as cur:
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_balanced_entries_pass_the_database_check(shop, fake_sim):
    captured_payment(shop)
    with connection.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")  # raises if anything is unbalanced
        cur.execute("SET CONSTRAINTS ALL DEFERRED")


def test_entries_and_lines_are_append_only(shop, fake_sim):
    payment = captured_payment(shop)
    entry = JournalEntry.objects.get(payment=payment)
    line = entry.lines.first()
    for obj in (entry, line):
        with pytest.raises(PermissionError):
            obj.save()
        with pytest.raises(PermissionError):
            obj.delete()
    with pytest.raises(PermissionError):
        JournalLine.objects.filter(pk=line.pk).update(amount=1)
    with pytest.raises(PermissionError):
        JournalEntry.objects.all().delete()
    for sql in ("UPDATE ledger_journalline SET amount = amount + 1",
                "DELETE FROM ledger_journalentry"):
        with (pytest.raises(DatabaseError, match="append-only"), transaction.atomic(),
              connection.cursor() as cur):
            cur.execute(sql)


def test_account_kind_must_match_owner(shop):
    from django.db import IntegrityError

    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerAccount.objects.create(kind=Kind.MERCHANT_AVAILABLE)
    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerAccount.objects.create(kind=Kind.FEE_REVENUE, merchant=shop.merchant)


# --- check and backfill ---------------------------------------------------------------------

def test_check_and_backfill_cover_v1_payments(shop, fake_sim, monkeypatch):
    from apps.payments import settlement
    from apps.refunds import service

    # Payments captured and refunded before the ledger existed.
    monkeypatch.setattr(settlement, "post_capture", lambda payment: None)
    monkeypatch.setattr(service, "post_refund", lambda refund: None)
    payment = captured_payment(shop)
    rfd = refund(shop, payment, 590).json()["id"]

    found = checks.problems()
    assert f"{payment.public_id} was captured but has no ledger entry" in found
    assert f"{rfd} was completed but has no ledger entry" in found
    with pytest.raises(CommandError):
        call_command("ledger_check", stdout=StringIO(), stderr=StringIO())

    out = StringIO()
    call_command("ledger_backfill", "--dry-run", stdout=out)
    assert "Would post 1 capture(s) and 1 refund(s)" in out.getvalue()
    assert not JournalEntry.objects.exists()

    call_command("ledger_backfill", stdout=out)
    assert "Posted 1 capture(s) and 1 refund(s)" in out.getvalue()
    assert JournalEntry.objects.filter(backfilled=True).count() == 2
    assert posting.merchant_balances(shop.merchant) == {"available": 3968 - 590,
                                                        "reserve": 459}
    call_command("ledger_backfill", stdout=out)  # again: nothing left to post
    assert JournalEntry.objects.count() == 2

    out = StringIO()
    call_command("ledger_check", stdout=out)
    assert "Ledger OK: 2 entries" in out.getvalue()
