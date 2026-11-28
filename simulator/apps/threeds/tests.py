"""v2 3-D Secure: SecurePay Bank challenges risky payments (docs/v2/THREE-DS.md)."""
import pytest

from apps.operations.tests import MER, PAY, body, signature_ok
from apps.threeds import service
from apps.threeds.models import Authentication

CARD = "4000000000003220"


def op(n: int) -> str:
    return "op_" + f"{n:024d}"


def start(gw, number=CARD, amount=4590, payment=PAY):
    return gw.post_json("/sim/3ds/authenticate", {
        "payment_id": payment, "merchant_id": MER, "amount": amount,
        "card": {"number": number, "expiry_month": 12, "expiry_year": 2030}})


def answer(gw, three_ds_id, code):
    return gw.post_json(f"/sim/3ds/{three_ds_id}/challenge", {"code": code}).json()


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def proof(auth: dict) -> dict:
    return {"three_ds_id": auth["three_ds_id"], "eci": auth["eci"], "cavv": auth["cavv"]}


def test_a_card_not_enrolled_is_left_to_the_gateway(gw):
    r = start(gw, number="4242424242424242")
    assert r.status_code == 200 and r.json()["status"] == "not_enrolled"
    assert not Authentication.objects.exists()


def test_the_right_code_authenticates_and_the_payment_is_approved(gw, settings):
    r = start(gw)
    assert r.status_code == 201 and signature_ok(r, settings.SIMULATOR_SIGNING_SECRET)
    started = r.json()
    assert started["status"] == "pending" and started["acs"] == "SecurePay Bank"
    assert len(started["test_code"]) == 6 and started["attempts_remaining"] == 3
    assert started["phone_hint"].startswith("•••• ") and started["cavv"] is None
    again = start(gw)  # a retry for the same payment gets the same challenge
    assert again.status_code == 200 and again.json()["three_ds_id"] == started["three_ds_id"]
    assert start(gw, amount=5000).status_code == 409
    done = answer(gw, started["three_ds_id"], started["test_code"])
    assert (done["status"], done["trans_status"], done["eci"]) == ("authenticated", "Y", "05")
    assert len(done["cavv"]) == 28 and done["test_code"] is None
    paid = gw.post_json("/sim/authorise", body(CARD, authentication=proof(done))).json()
    assert paid["status"] == "approved"
    assert paid["network"]["three_ds"] == {"three_ds_id": done["three_ds_id"], "eci": "05"}
    assert Authentication.objects.get().used_at is not None


def test_three_wrong_codes_fail_the_challenge(gw):
    started = start(gw).json()
    bad = wrong(started["test_code"])
    assert answer(gw, started["three_ds_id"], bad)["attempts_remaining"] == 2
    assert answer(gw, started["three_ds_id"], bad)["status"] == "pending"
    failed = answer(gw, started["three_ds_id"], bad)
    assert (failed["status"], failed["trans_status"], failed["test_code"]) == ("failed", "N", None)
    # the right code is too late now
    assert answer(gw, started["three_ds_id"], started["test_code"])["status"] == "failed"


def test_an_expired_challenge_takes_no_code(gw):
    started = start(gw).json()
    Authentication.objects.update(expires_at="2020-01-01T00:00:00Z")
    assert answer(gw, started["three_ds_id"], started["test_code"])["status"] == "expired"


def test_the_issuer_refuses_a_proof_it_did_not_make_or_has_seen(gw):
    started = start(gw).json()
    done = answer(gw, started["three_ds_id"], started["test_code"])
    forged = {**proof(done), "cavv": "A" * 27 + "="}
    r = gw.post_json("/sim/authorise", body(CARD, op=op(1), authentication=forged)).json()
    assert r["status"] == "declined" and r["network"]["response_code"] == "05"
    other_amount = gw.post_json("/sim/authorise", body(CARD, op=op(2), amount=9999,
                                                       authentication=proof(done))).json()
    assert other_amount["network"]["response_code"] == "05"
    assert gw.post_json("/sim/authorise", body(CARD, op=op(3),
                                               authentication=proof(done))).json()[
        "status"] == "approved"
    replay = gw.post_json("/sim/authorise", body(CARD, op=op(4), authentication=proof(done)))
    assert replay.json()["network"]["response_code"] == "05"


def test_issued_cards_and_the_demo_range_are_enrolled():
    assert service.enrolled("4571200000004242") is True
    assert service.enrolled("4000322012345674") is True
    assert service.enrolled("4242424242424242") is False


@pytest.mark.parametrize("bad", [{"amount": 0}, {"card": {"number": CARD}},
                                 {"payment_id": "nope"}, {"colour": "red"}])
def test_bad_requests_are_refused(gw, bad):
    data = {"payment_id": PAY, "merchant_id": MER, "amount": 100,
            "card": {"number": CARD, "expiry_month": 12, "expiry_year": 2030}, **bad}
    assert gw.post_json("/sim/3ds/authenticate", data).status_code == 400


def test_a_malformed_authentication_is_refused(gw):
    r = gw.post_json("/sim/authorise", body(CARD, authentication={"eci": "05"}))
    assert r.status_code == 400 and r.json()["error"]["param"] == "authentication"


def test_3ds_needs_the_gateway_key(client):
    assert client.post("/sim/3ds/authenticate", {}).status_code == 401
