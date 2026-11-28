"""v2 issuing: merchants make virtual cards at SecurePay Bank (docs/v2/ISSUING.md)."""
import pytest

from apps.audit.models import AuditEvent
from apps.issuing.models import IssuedCard
from conftest import _validator

pytestmark = pytest.mark.django_db

KEY = {"Idempotency-Key": "issue-card-0001"}


def create(shop, headers=KEY, **body):
    return shop.post("/v1/issuing/cards", {"spending_limit": 3000, "single_use": True, **body},
                     format="json", headers=headers)


def test_a_merchant_makes_a_single_use_card(shop, fake_sim):
    r = create(shop, description="Agent shopping")
    assert r.status_code == 201, r.content
    card = r.json()
    _validator("issued-card.schema.json").validate(card)
    assert card["id"].startswith("ic_") and card["object"] == "issued_card"
    assert card["number"] == "4571200000004242" and card["cvc"] == "321"
    assert (card["spending_limit"], card["remaining"], card["single_use"]) == (3000, 3000, True)
    assert card["livemode"] is False and r["Cache-Control"] == "no-store"
    stored = IssuedCard.objects.get(public_id=card["id"])
    assert "4571200000004242" not in str(stored.__dict__)  # the number is never stored
    event = AuditEvent.objects.get(action="issued_card.created")
    assert event.target_id == card["id"] and "4571200000004242" not in str(event.details)
    sent = next(b for m, p, b in fake_sim.requests if p == "/sim/issuing/cards")
    assert sent == {"card_id": card["id"], "merchant_id": shop.merchant.public_id,
                    "spending_limit": 3000, "single_use": True}


def test_the_same_key_finds_the_same_card_without_the_number(shop, fake_sim):
    first = create(shop).json()
    again = create(shop)
    assert again.status_code == 200 and again["Idempotent-Replayed"] == "true"
    assert again.json()["id"] == first["id"] and "number" not in again.json()
    assert create(shop, spending_limit=5000).status_code == 409
    assert IssuedCard.objects.count() == 1


@pytest.mark.parametrize("body", [{"spending_limit": 50}, {"spending_limit": 30.5},
                                  {"single_use": "yes"}, {"description": "x" * 101},
                                  {"colour": "red"}])
def test_bad_requests_are_refused(shop, fake_sim, body):
    assert create(shop, **body).status_code == 400
    assert not IssuedCard.objects.exists()


def test_an_idempotency_key_is_required(shop, fake_sim):
    r = create(shop, headers={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "idempotency_key_missing"


def test_no_card_without_the_bank(shop, fake_sim):
    fake_sim.mode = "bad_signature"
    r = create(shop)
    assert r.status_code == 503 and r.json()["error"]["code"] == "issuer_unavailable"
    assert not IssuedCard.objects.exists()


def test_read_list_and_cancel(shop, other_shop, fake_sim):
    card = create(shop).json()
    fake_sim.cards[card["id"]].update(spent=3000, remaining=0, status="used", authorisations=[
        {"stan": "123456", "rrn": "ABCDEFGHIJKL", "amount": 3000, "response_code": "00",
         "response_text": "Approved", "captured": None, "reversed": False,
         "created_at": "2026-11-28T03:00:00Z"}])
    detail = shop.get(f"/v1/issuing/cards/{card['id']}").json()
    _validator("issued-card.schema.json").validate(detail)
    assert (detail["status"], detail["spent"], detail["remaining"]) == ("used", 3000, 0)
    assert detail["authorisations"][0]["approved"] is True and "number" not in detail
    assert "stan" not in detail["authorisations"][0]
    listing = shop.get("/v1/issuing/cards").json()
    assert [c["id"] for c in listing["data"]] == [card["id"]]
    assert other_shop.get(f"/v1/issuing/cards/{card['id']}").status_code == 404
    assert other_shop.post(f"/v1/issuing/cards/{card['id']}/cancel").status_code == 404
    r = shop.post(f"/v1/issuing/cards/{card['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert AuditEvent.objects.filter(action="issued_card.cancelled").count() == 1


def test_a_detail_still_answers_when_the_bank_does_not(shop, fake_sim):
    card = create(shop).json()
    fake_sim.mode = "http_500"
    detail = shop.get(f"/v1/issuing/cards/{card['id']}")
    assert detail.status_code == 200 and detail.json()["authorisations"] == []


# --- dashboard ---------------------------------------------------------------------------------

def test_an_operator_makes_and_cancels_cards_on_the_dashboard(shop, fake_sim, staff_client):
    operator = staff_client("merchant_operator", merchant=shop.merchant)
    r = operator.post("/internal/issuing/cards", {"spending_limit": 3000, "single_use": True},
                      content_type="application/json",
                      headers={"Idempotency-Key": "dash-card-0001"})
    assert r.status_code == 201, r.content
    card = r.json()
    assert card["number"].startswith("457120") and card["merchant_name"] == shop.merchant.name
    assert operator.get("/internal/issuing/cards").json()["count"] == 1
    assert operator.get("/internal/issuing/cards?mode=live").json()["count"] == 0
    detail = operator.get(f"/internal/issuing/cards/{card['id']}").json()
    assert detail["bank_reachable"] is True and "number" not in detail
    r = operator.post(f"/internal/issuing/cards/{card['id']}/cancel", {},
                      content_type="application/json")
    assert r.json()["status"] == "cancelled"


def test_administrators_only_look_and_analysts_are_refused(shop, fake_sim, staff_client):
    create(shop)
    admin = staff_client("administrator")
    assert admin.get("/internal/issuing/cards").json()["count"] == 1
    r = admin.post("/internal/issuing/cards", {"spending_limit": 3000},
                   content_type="application/json", headers={"Idempotency-Key": "dash-card-0002"})
    assert r.status_code == 403
    assert staff_client("fraud_analyst").get("/internal/issuing/cards").status_code == 403
