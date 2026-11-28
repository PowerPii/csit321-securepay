"""v2 issuing: SecurePay Bank's virtual cards and their limits (docs/v2/ISSUING.md)."""
import pytest

from apps.issuing import service
from apps.issuing.models import IssuedCard, IssuerAuthorisation
from apps.operations.tests import MER, body, signature_ok

CARD = "ic_" + "C" * 24


def op(n: int) -> str:
    return "op_" + f"{n:024d}"


def create(gw, **extra):
    data = {"card_id": CARD, "merchant_id": MER, "spending_limit": 3000, "single_use": True,
            **extra}
    return gw.post_json("/sim/issuing/cards", data)


def pay(gw, card, amount, n):
    return gw.post_json("/sim/authorise", body(card["number"], op=op(n), amount=amount,
                                               month=card["expiry_month"],
                                               year=card["expiry_year"])).json()


def test_a_card_is_created_and_its_number_is_shown_once(gw, settings):
    r = create(gw)
    assert r.status_code == 201 and signature_ok(r, settings.SIMULATOR_SIGNING_SECRET)
    card = r.json()
    assert card["number"].startswith("457120") and len(card["number"]) == 16
    assert len(card["cvc"]) == 3 and card["last4"] == card["number"][-4:]
    assert card["remaining"] == 3000 and card["status"] == "active"
    stored = IssuedCard.objects.get(card_id=CARD)
    assert card["number"] not in str(stored.__dict__)  # only a keyed hash is kept
    again = create(gw)
    assert again.status_code == 200 and "number" not in again.json()
    assert create(gw, spending_limit=5000).status_code == 409


@pytest.mark.parametrize("bad", [{"spending_limit": 50}, {"spending_limit": "3000"},
                                 {"card_id": "nope"}, {"single_use": "yes"}])
def test_bad_requests_are_refused(gw, bad):
    assert create(gw, **bad).status_code == 400


def test_the_single_use_card_pays_once(gw):
    card = create(gw).json()
    first = pay(gw, card, 3000, 1)
    assert first["status"] == "approved"
    assert first["network"]["trace"][-1] == {**first["network"]["trace"][-1],
                                              "node": "issuer", "response_code": "00"}
    second = pay(gw, card, 500, 2)
    assert second["status"] == "declined" and second["decline_reason"] == "card_declined"
    assert second["network"]["response_code"] == "62"
    assert second["network"]["response_text"] == "Restricted card"
    state = gw.get(f"/sim/issuing/cards/{CARD}").json()
    assert state["status"] == "used" and state["spent"] == 3000
    assert [a["response_code"] for a in state["authorisations"]] == ["62", "00"]


def test_the_limit_is_enforced_across_payments(gw):
    card = create(gw, single_use=False, spending_limit=5000).json()
    assert pay(gw, card, 3000, 1)["status"] == "approved"
    over = pay(gw, card, 2500, 2)
    assert over["network"]["response_code"] == "61"
    assert over["decline_reason"] == "insufficient_funds"
    assert pay(gw, card, 2000, 3)["status"] == "approved"
    assert gw.get(f"/sim/issuing/cards/{CARD}").json()["remaining"] == 0


def test_a_cancel_gives_the_amount_back(gw):
    card = create(gw).json()
    auth = pay(gw, card, 3000, 1)
    r = gw.post_json("/sim/cancel", {"operation_id": op(9),
                                     "authorisation_id": auth["operation_id"]})
    assert r.json()["status"] == "approved"
    state = gw.get(f"/sim/issuing/cards/{CARD}").json()
    assert state["spent"] == 0 and state["status"] == "active"
    assert pay(gw, card, 3000, 2)["status"] == "approved"


def test_a_partial_capture_gives_back_the_rest(gw):
    card = create(gw, single_use=False).json()
    auth = pay(gw, card, 3000, 1)
    gw.post_json("/sim/capture", {"operation_id": op(8), "authorisation_id": auth["operation_id"],
                                  "amount": 1000})
    assert gw.get(f"/sim/issuing/cards/{CARD}").json()["spent"] == 1000


def test_a_cancelled_card_and_a_wrong_expiry_are_declined(gw):
    card = create(gw, single_use=False).json()
    wrong = gw.post_json("/sim/authorise", body(card["number"], op=op(1), amount=100,
                                                month=card["expiry_month"],
                                                year=card["expiry_year"] + 1)).json()
    assert wrong["network"]["response_code"] == "54"
    r = gw.post_json(f"/sim/issuing/cards/{CARD}/cancel", {})
    assert r.json()["status"] == "cancelled"
    assert pay(gw, card, 100, 2)["network"]["response_code"] == "62"


def test_an_unknown_number_in_the_range_is_declined(gw):
    number = service._new_number()
    r = gw.post_json("/sim/authorise", body(number, op=op(1), amount=100)).json()
    assert r["network"]["response_code"] == "14" and r["decline_reason"] == "card_declined"
    assert not IssuerAuthorisation.objects.exists()


def test_card_management_needs_the_gateway_key(client):
    assert client.post("/sim/issuing/cards", {}).status_code == 401
    assert client.get(f"/sim/issuing/cards/{CARD}").status_code == 401
