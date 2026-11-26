"""Demo core-only mode (settings.DEMO_CORE_ONLY) for the prototype demonstration.

Off by default. When on, the staff and checkout replies say so (the pages hide API keys,
Webhooks and the guest status link) and customer accounts answer 404. Nothing else changes.
"""
import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db(databases=["default", "vault"])


@pytest.fixture
def operator(staff_client, shop):
    return staff_client("merchant_operator", merchant=shop.merchant)


def checkout_session(shop):
    from apps.payments.models import Payment
    from conftest import valid_body

    payment = Payment.objects.get(public_id=shop.create_payment(valid_body()).json()["id"])
    return APIClient().get(f"/checkout/{payment.checkout_session_id}")


def test_off_by_default(settings, operator, shop):
    assert settings.DEMO_CORE_ONLY is False
    assert operator.get("/internal/me").json()["demo_core_only"] is False
    assert checkout_session(shop).json()["demo_core_only"] is False
    r = APIClient().get("/customer/me")
    assert r.status_code == 401 or r.json()["error"]["code"] != "accounts_unavailable"


def test_on_flags_staff_and_checkout_replies(settings, operator, shop):
    settings.DEMO_CORE_ONLY = True
    assert operator.get("/internal/me").json()["demo_core_only"] is True
    r = checkout_session(shop)
    assert r.status_code == 200 and r.json()["demo_core_only"] is True


@pytest.mark.parametrize("method,path", [
    ("get", "/customer/me"),
    ("get", "/customer/insights"),
    ("post", "/customer/sign-in"),
    ("post", "/customer/register"),
    ("post", "/customer/password-reset/request"),
])
def test_on_pauses_every_customer_account_endpoint(settings, method, path):
    settings.DEMO_CORE_ONLY = True
    client = APIClient()
    r = getattr(client, method)(path, {}, format="json") if method == "post" else client.get(path)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "accounts_unavailable"


def test_on_leaves_the_core_story_and_hidden_pages_working(settings, operator, shop):
    """Only the pages are hidden: payments, API keys and webhooks still answer as before."""
    settings.DEMO_CORE_ONLY = True
    assert operator.get("/internal/payments").status_code == 200
    assert operator.get("/internal/webhooks").status_code == 200
    assert operator.get("/internal/merchant").status_code == 200
