"""Ledger part 2: reserve release, payouts, and the staff API for balances and entries."""
import threading
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.ledger import checks, fees, jobs, payouts, posting
from apps.ledger.models import JournalEntry, LedgerAccount, Payout
from apps.refunds.tests import captured_payment, refund

pytestmark = pytest.mark.django_db(databases=["default", "vault"])

SPLIT = fees.split(4590)  # the default test payment: fee 163, reserve 459, net 3968


def later(days):
    return timezone.now() + timedelta(days=days)


# --- reserve release ------------------------------------------------------------------------

def test_reserve_is_held_until_the_hold_passes(shop, fake_sim, settings):
    payment = captured_payment(shop)
    hold = settings.GATEWAY_RULES["RESERVE_HOLD_DAYS"]
    assert jobs.release_reserves(now=later(hold - 1)) == 0
    assert posting.merchant_balances(shop.merchant) == {"available": SPLIT.net,
                                                        "reserve": SPLIT.reserve}

    assert jobs.release_reserves(now=later(hold) + timedelta(minutes=1)) == 1
    entry = JournalEntry.objects.get(kind="reserve_release")
    capture = JournalEntry.objects.get(kind="capture")
    assert entry.source_id == capture.public_id and entry.payment == payment
    assert {(line.account.kind, line.amount) for line in entry.lines.all()} == {
        ("merchant_reserve", SPLIT.reserve), ("merchant_available", -SPLIT.reserve)}
    assert posting.merchant_balances(shop.merchant) == {"available": SPLIT.net + SPLIT.reserve,
                                                        "reserve": 0}
    # Released once only, however often the job runs.
    assert jobs.release_reserves(now=later(hold + 30)) == 0
    assert JournalEntry.objects.filter(kind="reserve_release").count() == 1
    assert checks.problems() == []


def test_no_reserve_means_nothing_to_release(shop, fake_sim, settings):
    settings.GATEWAY_RULES = {**settings.GATEWAY_RULES, "RESERVE_BPS": 0}
    captured_payment(shop)
    assert jobs.release_reserves(now=later(365)) == 0
    assert not JournalEntry.objects.filter(kind="reserve_release").exists()


def test_the_worker_runs_the_release_job():
    from apps.events.management.commands import run_worker

    assert run_worker.release_reserves in run_worker.JOBS


# --- payouts --------------------------------------------------------------------------------

def test_payout_takes_from_available_and_posts_an_entry(shop, fake_sim):
    captured_payment(shop)
    payout = payouts.create(shop.merchant, 3000, actor_type="staff", actor_id="stf_x")
    assert payout.public_id.startswith("po_") and payout.status == "paid"
    entry = JournalEntry.objects.get(kind="payout", payout=payout)
    assert entry.payment is None and entry.source_id == payout.public_id
    assert {(line.account.kind, line.amount) for line in entry.lines.all()} == {
        ("merchant_available", 3000), ("network_clearing", -3000)}
    assert posting.merchant_balances(shop.merchant)["available"] == SPLIT.net - 3000
    # Clearing now holds exactly what we owe: our fees plus the merchant's balances.
    clearing = LedgerAccount.objects.get(kind="network_clearing").balance()
    assert clearing == SPLIT.fee + SPLIT.reserve + SPLIT.net - 3000
    assert AuditEvent.objects.filter(action="payout.paid", target_id=payout.public_id,
                                     details__amount=3000).exists()
    assert checks.problems() == []


@pytest.mark.parametrize(("amount", "code"), [
    (99, "amount_too_small"), (0, "amount_too_small"), ("100", "invalid_field"),
    (True, "invalid_field"), (None, "invalid_field"), (SPLIT.net + 1, "insufficient_balance"),
])
def test_payout_rules(shop, fake_sim, amount, code):
    from apps.common.errors import ApiError

    captured_payment(shop)
    with pytest.raises(ApiError) as err:
        payouts.create(shop.merchant, amount, actor_type="staff", actor_id="stf_x")
    assert err.value.code == code
    assert not Payout.objects.exists()


def test_payout_of_the_whole_balance_then_nothing_left(shop, fake_sim):
    from apps.common.errors import ApiError

    captured_payment(shop)
    payouts.create(shop.merchant, SPLIT.net, actor_type="staff", actor_id="stf_x")
    assert posting.merchant_balances(shop.merchant)["available"] == 0
    with pytest.raises(ApiError, match="Only S\\$0.00"):
        payouts.create(shop.merchant, 100, actor_type="staff", actor_id="stf_x")


@pytest.mark.django_db(transaction=True, databases=["default", "vault"])
def test_concurrent_payouts_cannot_overdraw(shop, fake_sim):
    from apps.common.errors import ApiError

    captured_payment(shop)
    barrier = threading.Barrier(2)
    results = []

    def pay():
        try:
            barrier.wait()
            payouts.create(shop.merchant, 3000, actor_type="staff", actor_id="stf_x")
            results.append("paid")
        except ApiError as exc:
            results.append(exc.code)
        finally:
            connection.close()

    threads = [threading.Thread(target=pay) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["insufficient_balance", "paid"]
    assert posting.merchant_balances(shop.merchant)["available"] == SPLIT.net - 3000


def test_payout_rows_are_checked(shop, fake_sim):
    captured_payment(shop)
    with transaction.atomic():
        Payout.objects.create(public_id="po_orphan", merchant=shop.merchant, amount=100)
    assert "po_orphan was paid but has no ledger entry" in checks.problems()


# --- staff API ------------------------------------------------------------------------------

def test_operator_sees_own_balance_and_entries(staff_client, shop, other_shop, fake_sim,
                                               settings):
    payment = captured_payment(shop)
    captured_payment(other_shop, amount=1000)
    refund(shop, payment, 500)
    operator = staff_client("merchant_operator", merchant=shop.merchant)

    b = operator.get("/internal/balance").json()
    assert b["scope"] == "merchant" and b["merchant_id"] == shop.merchant.public_id
    assert (b["available"], b["reserve"]) == (SPLIT.net - 500, SPLIT.reserve)
    assert b["next_release"]["amount"] == SPLIT.reserve
    assert b["next_release"]["payment_id"] == payment.public_id
    assert b["rules"] == {"fee_percent_bps": 290, "fee_fixed": 30, "reserve_bps": 1000,
                          "reserve_hold_days": settings.GATEWAY_RULES["RESERVE_HOLD_DAYS"],
                          "payout_minimum": 100}

    r = operator.get("/internal/ledger/entries").json()
    assert r["count"] == 2  # the capture and the refund; never the other shop's
    assert [e["kind"] for e in r["data"]] == ["refund", "capture"]
    capture = r["data"][1]
    assert capture["payment_reference"] == payment.reference
    assert sum(line["amount"] for line in capture["lines"]) == 0
    assert {line["memo"] for line in capture["lines"]} == {"gross", "fee", "reserve", "net"}
    # An operator cannot widen the scope with ?merchant=.
    other = operator.get(f"/internal/ledger/entries?merchant={other_shop.merchant.public_id}")
    assert other.json()["count"] == 2
    assert operator.get("/internal/ledger/entries?kind=refund").json()["count"] == 1
    assert operator.get("/internal/ledger/entries?kind=nope").status_code == 400


def test_operator_pays_out_through_the_api(staff_client, shop, other_shop, fake_sim):
    captured_payment(shop)
    operator = staff_client("merchant_operator", merchant=shop.merchant)
    r = operator.post("/internal/payouts", {"amount": 2500}, content_type="application/json")
    assert r.status_code == 201, r.content
    assert (r.json()["amount"], r.json()["status"]) == (2500, "paid")
    assert operator.get("/internal/balance").json()["available"] == SPLIT.net - 2500
    listed = operator.get("/internal/payouts").json()
    assert listed["count"] == 1 and listed["data"][0]["id"] == r.json()["id"]

    too_much = operator.post("/internal/payouts", {"amount": 10**6},
                             content_type="application/json")
    assert too_much.status_code == 422
    assert too_much.json()["error"]["code"] == "insufficient_balance"

    # Another merchant's operator sees none of it.
    other = staff_client("merchant_operator", merchant=other_shop.merchant)
    assert other.get("/internal/payouts").json()["count"] == 0
    assert other.get("/internal/balance").json()["available"] == 0


def test_administrator_sees_the_gateway_books_read_only(staff_client, shop, other_shop,
                                                        fake_sim):
    captured_payment(shop)
    captured_payment(other_shop, amount=1000)
    small = fees.split(1000)
    admin = staff_client("administrator")
    b = admin.get("/internal/balance").json()
    assert b["scope"] == "gateway" and b["check"] == {"ok": True, "problems": []}
    assert b["network_clearing"] == 4590 + 1000
    assert b["fee_revenue"] == SPLIT.fee + small.fee
    assert b["merchants_available"] == SPLIT.net + small.net
    assert b["merchants_reserve"] == SPLIT.reserve + small.reserve
    # The books balance: what came in equals fees plus what merchants are owed.
    assert b["network_clearing"] == (b["fee_revenue"] + b["merchants_available"]
                                     + b["merchants_reserve"])
    assert {m["merchant_id"] for m in b["merchants"]} == {shop.merchant.public_id,
                                                          other_shop.merchant.public_id}
    assert admin.get("/internal/ledger/entries").json()["count"] == 2
    one = admin.get(f"/internal/ledger/entries?merchant={shop.merchant.public_id}").json()
    assert one["count"] == 1

    r = admin.post("/internal/payouts", {"amount": 100}, content_type="application/json")
    assert r.status_code == 403
    assert not Payout.objects.exists()


def test_fraud_analyst_is_refused(staff_client):
    analyst = staff_client("fraud_analyst")
    for path in ("/internal/balance", "/internal/ledger/entries", "/internal/payouts"):
        assert analyst.get(path).status_code == 403


def test_signed_out_is_refused(client):
    assert client.get("/internal/balance").status_code == 401
