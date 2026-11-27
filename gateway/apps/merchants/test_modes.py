"""v2 test and live mode: a key works in one mode only, and the two modes never mix."""
import uuid

import pytest

from apps.ledger import checks, fees, posting
from apps.ledger.models import JournalEntry, LedgerAccount
from apps.merchants.models import ApiKey
from apps.refunds.tests import captured_payment, refund
from conftest import MerchantClient

pytestmark = pytest.mark.django_db(databases=["default", "vault"])

SPLIT = fees.split(4590)


@pytest.fixture
def live(shop):
    """The same merchant as `shop`, with a live key."""
    api_key, plaintext = ApiKey.issue(shop.merchant, livemode=True)
    return MerchantClient(shop.merchant, api_key, plaintext, shop.secret)


def test_live_key_has_its_own_prefix_and_authenticates(shop, live):
    assert live.key.startswith("sp_live_") and len(live.key) == 48
    assert live.api_key.livemode and not shop.api_key.livemode
    assert live.api_key.hint.startswith("sp_live_")
    assert live.get("/v1/balance").json()["livemode"] is True
    assert shop.get("/v1/balance").json()["livemode"] is False


def test_one_active_key_per_mode():
    from django.db import IntegrityError, transaction

    from apps.merchants.models import Merchant

    merchant = Merchant(name="Keys", status=Merchant.Status.ACTIVE)
    merchant.rotate_signing_secret()
    merchant.save()
    ApiKey.issue(merchant)
    ApiKey.issue(merchant, livemode=True)  # one of each is fine
    with pytest.raises(IntegrityError), transaction.atomic():
        ApiKey.issue(merchant, livemode=True)


def test_payments_belong_to_the_mode_of_their_key(shop, live, fake_sim):
    test_pay = shop.create_payment().json()
    live_pay = live.create_payment().json()
    assert test_pay["livemode"] is False and live_pay["livemode"] is True
    # Each key sees its own mode only; the other mode's payment is a plain 404.
    assert shop.get(f"/v1/payments/{test_pay['id']}").status_code == 200
    assert live.get(f"/v1/payments/{live_pay['id']}").status_code == 200
    assert shop.get(f"/v1/payments/{live_pay['id']}").status_code == 404
    assert live.get(f"/v1/payments/{test_pay['id']}").status_code == 404
    r = live.post(f"/v1/payments/{test_pay['id']}/cancel", {}, format="json",
                  headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 404


def test_idempotency_keys_are_kept_per_mode(shop, live):
    key = "same-key-in-both-modes"
    a = shop.create_payment(key=key)
    b = live.create_payment(key=key)
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]
    assert b.json()["livemode"] is True


def test_live_capture_refund_and_payout_use_live_books_only(shop, live, fake_sim):
    payment = captured_payment(live)
    assert payment.livemode
    entry = JournalEntry.objects.get(kind="capture", payment=payment)
    assert entry.livemode
    assert all(line.account.livemode for line in entry.lines.all())

    # Live money shows on the live balance only.
    assert posting.merchant_balances(shop.merchant, livemode=True) == {
        "available": SPLIT.net, "reserve": SPLIT.reserve}
    assert posting.merchant_balances(shop.merchant) == {"available": 0, "reserve": 0}
    assert shop.get("/v1/balance").json()["available"] == 0
    assert live.get("/v1/balance").json()["available"] == SPLIT.net

    # A refund of a live payment through the test key is refused as unknown.
    assert refund(shop, payment, 500).status_code == 404
    r = refund(live, payment, 500)
    assert r.status_code == 201 and r.json()["livemode"] is True
    assert shop.get(f"/v1/refunds/{r.json()['id']}").status_code == 404

    # The test balance is empty, so a test payout fails; the live one works.
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    assert shop.post("/v1/payouts", {"amount": 1000}, format="json",
                     headers=headers).status_code == 422
    paid = live.post("/v1/payouts", {"amount": 1000}, format="json",
                     headers={"Idempotency-Key": str(uuid.uuid4())})
    assert paid.status_code == 201 and paid.json()["livemode"] is True
    assert shop.get(f"/v1/payouts/{paid.json()['id']}").status_code == 404
    assert shop.get("/v1/payouts").json()["data"] == []
    assert live.get("/v1/payouts").json()["data"][0]["id"] == paid.json()["id"]

    # The gateway-wide accounts exist once per mode, and the books still check out.
    live_clearing = LedgerAccount.objects.get(kind="network_clearing", livemode=True)
    assert live_clearing.balance() == SPLIT.gross - 500 - 1000
    assert not LedgerAccount.objects.filter(kind="network_clearing", livemode=False,
                                            lines__isnull=False).exists()
    assert checks.problems() == []


def test_an_entry_cannot_mix_modes(shop, fake_sim):
    from django.db import transaction

    payment = captured_payment(shop)
    test_clearing = posting.account(LedgerAccount.Kind.NETWORK_CLEARING)
    live_fee = posting.account(LedgerAccount.Kind.FEE_REVENUE, livemode=True)
    with pytest.raises(ValueError, match="mix test and live"), transaction.atomic():
        posting.post(kind="capture", source_id="pay_mixed", payment=payment,
                     merchant=shop.merchant, lines=[(test_clearing, 100, "gross"),
                                                    (live_fee, -100, "fee")])


def test_events_carry_the_mode(live, fake_sim):
    from apps.events.models import OutboxEvent

    payment = captured_payment(live)
    events = OutboxEvent.objects.filter(payment=payment)
    assert events and all(e.payload["livemode"] is True for e in events)
    assert all(e.payload["data"]["object"]["livemode"] is True for e in events)
