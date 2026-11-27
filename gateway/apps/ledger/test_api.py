"""Merchant API: GET /v1/balance and /v1/payouts, checked against the contract schemas."""
import json
import uuid

import pytest
from jsonschema import Draft202012Validator

from apps.events import signing
from apps.ledger import fees
from apps.ledger.models import Payout
from apps.refunds.tests import captured_payment
from conftest import CONTRACTS

pytestmark = pytest.mark.django_db(databases=["default", "vault"])

SPLIT = fees.split(4590)


def schema(name):
    return Draft202012Validator(json.loads((CONTRACTS / "schemas" / name).read_text()))


def pay_out(client, amount, key=None):
    return client.post("/v1/payouts", {"amount": amount}, format="json",
                       headers={"Idempotency-Key": key or str(uuid.uuid4())})


def test_balance_is_signed_and_matches_the_schema(shop, fake_sim):
    empty = shop.get("/v1/balance")
    assert empty.status_code == 200
    assert empty.json() == {"object": "balance", "livemode": False, "currency": "SGD",
                            "available": 0, "reserve": 0, "next_release": None}
    payment = captured_payment(shop)
    r = shop.get("/v1/balance")
    data = r.json()
    schema("balance.schema.json").validate(data)
    assert signing.verify(r.content, r["SecurePay-Signature"], shop.secret)
    assert (data["available"], data["reserve"]) == (SPLIT.net, SPLIT.reserve)
    assert data["next_release"]["amount"] == SPLIT.reserve
    assert data["next_release"]["payment_id"] == payment.public_id


def test_payout_create_list_and_read(shop, fake_sim):
    captured_payment(shop)
    r = pay_out(shop, 2000)
    assert r.status_code == 201, r.content
    payout = r.json()
    schema("payout.schema.json").validate(payout)
    assert signing.verify(r.content, r["SecurePay-Signature"], shop.secret)
    assert (payout["amount"], payout["status"]) == (2000, "paid")
    assert payout["merchant_id"] == shop.merchant.public_id
    assert shop.get("/v1/balance").json()["available"] == SPLIT.net - 2000

    pay_out(shop, 1000)
    listed = shop.get("/v1/payouts?limit=1").json()
    assert listed["object"] == "list" and listed["has_more"] is True
    assert [p["amount"] for p in listed["data"]] == [1000]  # newest first
    assert len(shop.get("/v1/payouts").json()["data"]) == 2
    assert shop.get(f"/v1/payouts/{payout['id']}").json() == payout


def test_payout_is_idempotent(shop, fake_sim):
    captured_payment(shop)
    key = str(uuid.uuid4())
    first = pay_out(shop, 1500, key)
    again = pay_out(shop, 1500, key)
    assert again.status_code == 201 and again.content == first.content
    assert again["Idempotent-Replayed"] == "true"
    assert Payout.objects.count() == 1
    reused = pay_out(shop, 1600, key)
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "idempotency_key_reused"
    missing = shop.post("/v1/payouts", {"amount": 100}, format="json")
    assert missing.json()["error"]["code"] == "idempotency_key_missing"


def test_payout_errors_follow_the_contract(shop, fake_sim):
    errors = schema("error.schema.json")
    captured_payment(shop)
    too_much = pay_out(shop, SPLIT.net + 1)
    assert too_much.status_code == 422
    errors.validate(too_much.json())
    assert too_much.json()["error"]["code"] == "insufficient_balance"
    small = pay_out(shop, 99)
    assert (small.status_code, small.json()["error"]["code"]) == (400, "amount_too_small")
    assert not Payout.objects.exists()


def test_other_merchants_payouts_are_not_found(shop, other_shop, fake_sim):
    captured_payment(shop)
    payout = pay_out(shop, 1000).json()
    assert other_shop.get(f"/v1/payouts/{payout['id']}").status_code == 404
    assert other_shop.get("/v1/payouts").json()["data"] == []
    assert other_shop.get("/v1/balance").json()["available"] == 0


def test_balance_needs_an_api_key(client):
    assert client.get("/v1/balance").status_code == 401
