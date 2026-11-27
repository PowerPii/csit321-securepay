"""POST /v1/payments and GET /v1/payments/{id} against the contract (UC-53, UC-27)."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.events.signing import verify
from apps.payments.models import IdempotencyRecord, Payment
from conftest import valid_body

pytestmark = pytest.mark.django_db


def test_create_payment_returns_a_contract_payment(shop, payment_schema):
    response = shop.create_payment()
    assert response.status_code == 201
    body = response.json()
    payment_schema.validate(body)
    assert body["status"] == "created"
    assert body["amount"] == 4590 and body["currency"] == "SGD"
    assert body["sequence"] == 1
    assert body["captured_amount"] == 0
    assert body["checkout_url"].endswith("/" + Payment.objects.get().checkout_session_id)
    assert "Idempotent-Replayed" not in response


def test_reply_is_signed_with_the_merchant_secret(shop):
    response = shop.create_payment()
    assert verify(response.content, response["SecurePay-Signature"], shop.secret)
    assert not verify(response.content, response["SecurePay-Signature"], "whsec_other")
    assert response["SecurePay-Request-Id"].startswith("req_")


def test_merchant_comes_from_the_key_and_creation_is_audited(shop):
    response = shop.create_payment()
    payment = Payment.objects.get(public_id=response.json()["id"])
    assert payment.merchant == shop.merchant
    event = AuditEvent.objects.get(action="payment.created")
    assert event.target_id == payment.public_id
    assert event.merchant_id == shop.merchant.public_id
    assert event.actor_id == shop.api_key.public_id
    assert event.request_id == response["SecurePay-Request-Id"]


def test_get_payment(shop, payment_schema):
    created = shop.create_payment().json()
    response = shop.get(f"/v1/payments/{created['id']}")
    assert response.status_code == 200
    payment_schema.validate(response.json())
    assert response.json()["id"] == created["id"]
    assert verify(response.content, response["SecurePay-Signature"], shop.secret)


def test_another_merchants_payment_is_404_like_a_missing_one(shop, other_shop, error_schema):
    created = shop.create_payment().json()
    theirs = other_shop.get(f"/v1/payments/{created['id']}")
    missing = other_shop.get("/v1/payments/pay_" + "Z" * 24)
    for response in (theirs, missing):
        assert response.status_code == 404
        error_schema.validate(response.json())
        assert response.json()["error"]["code"] == "resource_not_found"
        assert verify(response.content, response["SecurePay-Signature"], other_shop.secret)
    assert theirs.json()["error"]["message"] == missing.json()["error"]["message"]


def test_checkout_url_is_null_after_the_session_expires(shop):
    created = shop.create_payment().json()
    Payment.objects.update(checkout_expires_at=timezone.now() - timedelta(seconds=1))
    assert shop.get(f"/v1/payments/{created['id']}").json()["checkout_url"] is None


@pytest.mark.parametrize(("overrides", "code", "param"), [
    ({"amount": ...}, "missing_field", "amount"),
    ({"return_url": ...}, "missing_field", "return_url"),
    ({"amount": 49}, "amount_too_small", "amount"),
    ({"amount": 1_000_001}, "amount_too_large", "amount"),
    ({"amount": "4590"}, "invalid_field", "amount"),
    ({"amount": 45.9}, "invalid_field", "amount"),
    ({"amount": True}, "invalid_field", "amount"),
    ({"currency": "GBP"}, "currency_not_supported", "currency"),  # v2: USD is supported
    ({"return_url": "http://shop.localhost/return"}, "return_url_not_https", "return_url"),
    ({"return_url": "not a url"}, "invalid_field", "return_url"),
    ({"order_reference": ""}, "invalid_field", "order_reference"),
    ({"order_reference": "x" * 65}, "invalid_field", "order_reference"),
    ({"customer_email": "nope"}, "invalid_field", "customer_email"),
    ({"description": "x" * 201}, "invalid_field", "description"),
    ({"merchant_id": "mer_x"}, "invalid_field", "merchant_id"),  # never taken from the body
])
def test_invalid_bodies(shop, overrides, code, param, error_schema):
    response = shop.create_payment(valid_body(**overrides))
    assert response.status_code == 400, response.json()
    error_schema.validate(response.json())
    assert response.json()["error"] == {
        **response.json()["error"], "type": "invalid_request", "code": code, "param": param,
    }
    assert Payment.objects.count() == 0


def test_limits_come_from_gateway_rules(shop):
    assert shop.create_payment(valid_body(amount=50)).status_code == 201
    assert shop.create_payment(valid_body(amount=1_000_000)).status_code == 201


def test_merchant_limit_is_422(make_merchant):
    shop = make_merchant(max_amount_cents=10_000)
    response = shop.create_payment(valid_body(amount=10_001))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "amount_above_merchant_limit"
    assert response.json()["error"]["type"] == "business_rule"


def test_invalid_json_is_400(shop):
    response = shop.post("/v1/payments", "{not json", content_type="application/json",
                         headers={"Idempotency-Key": "k" * 16})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_json"


def test_card_numbers_are_refused_and_never_stored(shop, caplog):
    body = valid_body(description="card 4111 1111 1111 1111")
    response = shop.create_payment(body, key="card-key-0001")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "card_data_not_allowed"
    assert Payment.objects.count() == 0
    assert not IdempotencyRecord.objects.exists()
    assert not AuditEvent.objects.exists()
    assert "4111" not in caplog.text


# --- line items (added 13 Nov 2026) ----------------------------------------------------------

ITEMS = [{"name": "Wireless mouse", "quantity": 1, "unit_amount": 2990},
         {"name": "Cable tie (pack)", "quantity": 2, "unit_amount": 800}]


def test_line_items_are_stored_and_returned(shop, payment_schema):
    r = shop.create_payment(valid_body(line_items=ITEMS))
    assert r.status_code == 201, r.content
    payment_schema.validate(r.json())
    assert r.json()["line_items"] == ITEMS
    got = shop.get(f"/v1/payments/{r.json()['id']}").json()
    assert got["line_items"] == ITEMS


def test_line_items_are_optional(shop, payment_schema):
    r = shop.create_payment(valid_body())
    payment_schema.validate(r.json())
    assert r.json()["line_items"] is None


def test_line_items_must_add_up_to_the_amount(shop, error_schema):
    r = shop.create_payment(valid_body(amount=4591, line_items=ITEMS))
    assert r.status_code == 400
    error_schema.validate(r.json())
    assert r.json()["error"]["code"] == "line_items_total_mismatch"



@pytest.mark.parametrize(("items", "param"), [
    ([], "line_items"),
    ("mouse", "line_items"),
    ([{"name": "x", "quantity": 1}], "line_items[0]"),
    ([{"name": "", "quantity": 1, "unit_amount": 4590}], "line_items[0].name"),
    ([{"name": "x", "quantity": 0, "unit_amount": 4590}], "line_items[0].quantity"),
    ([{"name": "x", "quantity": True, "unit_amount": 4590}], "line_items[0].quantity"),
    ([{"name": "x", "quantity": 1, "unit_amount": -1}], "line_items[0].unit_amount"),
    ([{"name": "x" * 101, "quantity": 1, "unit_amount": 4590}], "line_items[0].name"),
])
def test_bad_line_items(shop, items, param):
    r = shop.create_payment(valid_body(line_items=items))
    assert r.status_code == 400
    assert (r.json()["error"]["code"], r.json()["error"]["param"]) == ("invalid_field", param)
