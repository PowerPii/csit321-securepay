"""v2 multi-currency and FX (docs/v2/FX.md)."""
import json
import uuid
from decimal import Decimal

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.fx import rates
from apps.fx.currencies import format_amount
from apps.fx.models import RateSheet
from apps.ledger import checks, posting
from apps.ledger.models import JournalEntry
from apps.payments.models import Payment
from apps.refunds.tests import refund, refund_schema  # noqa: F401
from conftest import CONTRACTS, valid_body

pytestmark = pytest.mark.django_db(databases=["default", "vault"])

THB_BODY = {"amount": 119200, "currency": "THB"}  # ฿1,192.00 at 0.0385 = S$45.89


def schema(name):
    from jsonschema import Draft202012Validator

    return Draft202012Validator(json.loads((CONTRACTS / "schemas" / name).read_text()))


# --- conversion ---------------------------------------------------------------------------

@pytest.mark.parametrize(("amount", "currency", "rate", "sgd"), [
    (119200, "THB", "0.0385", 4589),  # 45.892 rounds down
    (5000, "JPY", "0.0086", 4300),  # no minor unit
    (2_000_000, "IDR", "0.000083", 16600),  # rupiah are whole
    (1999, "USD", "1.3", 2599),  # 25.987 rounds up
    (5, "THB", "0.0385", 0),
    (4590, "SGD", "1", 4590),
])
def test_conversion_rounds_half_up_to_the_sgd_cent(amount, currency, rate, sgd):
    assert rates.to_sgd(amount, currency, Decimal(rate)) == sgd


def test_formatting_uses_codes_outside_sgd():
    assert format_amount(4590, "SGD") == "S$45.90"
    assert format_amount(119200, "THB") == "THB 1,192.00"
    assert format_amount(5000, "JPY") == "JPY 5,000"
    assert format_amount(-1500, "EUR") == "-EUR 15.00"


def test_rate_sheets_are_checked():
    good = rates.reference_rates()
    with pytest.raises(rates.RateError, match="Missing"):
        rates.clean_rates({k: v for k, v in good.items() if k != "JPY"})
    with pytest.raises(rates.RateError, match="Unknown"):
        rates.clean_rates({**good, "GBP": "1.7"})
    with pytest.raises(rates.RateError, match="positive"):
        rates.clean_rates({**good, "THB": "-1"})
    with pytest.raises(rates.RateError, match="not a number"):
        rates.clean_rates({**good, "THB": "cheap"})


def test_publish_command_and_rates_endpoint(shop):
    call_command("publish_fx_rates")
    call_command("publish_fx_rates", "--set", "THB=0.0391")
    sheet = RateSheet.objects.first()
    assert sheet.rates["THB"] == "0.0391" and sheet.rates["USD"] == "1.300000"
    r = shop.get("/v1/fx_rates")
    assert r.status_code == 200
    schema("fx-rates.schema.json").validate(r.json())
    assert r.json()["id"] == sheet.public_id and r.json()["fee_percent"] == "1"
    assert APIClient().get("/v1/fx_rates").status_code == 401


# --- payments in another currency -----------------------------------------------------------

def thb_payment(shop, **extra):
    r = shop.create_payment(valid_body(**THB_BODY, **extra))
    assert r.status_code == 201, r.content
    return r


def test_payment_locks_the_rate_and_shows_its_sgd_value(shop, payment_schema):
    call_command("publish_fx_rates", "--set", "THB=0.0385")
    data = thb_payment(shop).json()
    payment_schema.validate(data)
    assert (data["amount"], data["currency"]) == (119200, "THB")
    assert data["settlement"]["amount"] == 4589 and data["settlement"]["currency"] == "SGD"
    assert data["settlement"]["fx"]["rate"] == "0.0385"
    assert data["settlement"]["fx"]["fee_percent"] == "1"

    call_command("publish_fx_rates", "--set", "THB=0.05")  # later sheets change nothing
    again = shop.get(f"/v1/payments/{data['id']}").json()
    assert again["settlement"] == data["settlement"]


def test_sgd_payment_has_no_fx(shop, payment_schema):
    data = shop.create_payment().json()
    payment_schema.validate(data)
    assert data["settlement"] == {"currency": "SGD", "amount": 4590, "captured_amount": 0,
                                  "fx": None}


@pytest.mark.parametrize(("body", "code"), [
    ({"amount": 1000, "currency": "THB"}, "amount_too_small"),  # S$0.39
    ({"amount": 1_200_000, "currency": "JPY"}, "amount_too_large"),  # S$10,320
    ({"amount": 100, "currency": "GBP"}, "currency_not_supported"),
])
def test_limits_apply_to_the_sgd_value(shop, body, code):
    call_command("publish_fx_rates")
    r = shop.create_payment(valid_body(**body))
    assert (r.status_code, r.json()["error"]["code"]) == (400, code)


def test_merchant_limit_is_in_sgd(shop):
    shop.merchant.max_amount_cents = 4000
    shop.merchant.save()
    call_command("publish_fx_rates")
    r = shop.create_payment(valid_body(**THB_BODY))
    assert r.json()["error"]["code"] == "amount_above_merchant_limit"


def pay_by_card(payment_id):
    payment = Payment.objects.get(public_id=payment_id)
    browser = APIClient()
    base = f"/checkout/{payment.checkout_session_id}"
    assert browser.get(base).json()["currency"] == "THB"
    browser.post(f"{base}/card", {"number": "4242424242424242", "expiry_month": 12,
                                  "expiry_year": 2030, "cvc": "123", "privacy_consent": True},
                 format="json")
    browser.post(f"{base}/submit", {}, format="json")
    return payment


def test_thb_payment_is_settled_in_sgd_with_an_fx_fee_line(shop, fake_sim,
                                                           refund_schema):  # noqa: F811
    call_command("publish_fx_rates")  # THB 0.0385
    payment = pay_by_card(thb_payment(shop).json()["id"])
    _, _, body = next(r for r in fake_sim.requests if r[1] == "/sim/authorise")
    assert (body["amount"], body["currency"]) == (4589, "SGD")  # the network sees SGD

    r = shop.post(f"/v1/payments/{payment.public_id}/capture", {"amount": 100000},
                  format="json", headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 200, r.content
    assert r.json()["captured_amount"] == 100000
    assert r.json()["settlement"]["captured_amount"] == 3850  # ฿1,000 = S$38.50
    _, _, capture = next(r for r in fake_sim.requests if r[1] == "/sim/capture")
    assert capture["amount"] == 3850

    entry = JournalEntry.objects.get(kind="capture", payment=payment)
    lines = {line.memo: line.amount for line in entry.lines.all()}
    assert lines["gross"] == 3850
    assert lines["fee"] == -(112 + 30)  # 2.9% + S$0.30
    assert lines["fx_fee"] == -39  # 1% of S$38.50
    assert lines["reserve"] == -385
    assert lines["net"] == -(3850 - 142 - 39 - 385)
    assert all(line.currency == "SGD" for line in entry.lines.all())
    assert posting.merchant_balances(shop.merchant)["available"] == 3850 - 142 - 39 - 385

    # Refunds in baht at the locked rate; their SGD adds up to the capture exactly.
    sgd = []
    for amount in (33333, 33333, 33334):
        r = refund(shop, payment, amount)
        assert r.status_code == 201, r.content
        refund_schema.validate(r.json())
        assert r.json()["currency"] == "THB"
        sgd.append(r.json()["settlement"]["amount"])
    assert sum(sgd) == 3850 and sgd == [1283, 1284, 1283]  # telescoped, never over
    assert refund(shop, payment, 1).json()["error"]["code"] == "refund_exceeds_refundable"
    assert checks.problems() == []


def test_jpy_payment_has_no_minor_unit(shop, fake_sim, payment_schema):
    call_command("publish_fx_rates")
    data = shop.create_payment(valid_body(amount=5000, currency="JPY")).json()
    payment_schema.validate(data)
    assert data["settlement"]["amount"] == 4300
