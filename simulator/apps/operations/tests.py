import hashlib
import hmac
import json
import threading
from datetime import date

import pytest
from django.db import connection

from apps.ledger.models import LedgerEntry
from apps.operations.models import Authorisation, OperationRecord
from apps.operations.service import card_expired
from apps.operations.test_cards import Behaviour, behaviour_for, brand_for

OP = "op_" + "A" * 24
MER = "mer_" + "B" * 24
PAY = "pay_" + "C" * 24


def body(number="4242424242424242", op=OP, amount=4590, month=12, year=2030, **extra):
    data = {"operation_id": op, "merchant_id": MER, "payment_id": PAY, "amount": amount,
            "currency": "SGD",
            "card": {"number": number, "expiry_month": month, "expiry_year": year, "cvc": "123"}}
    data.update(extra)
    return data


def signature_ok(response, secret):
    header = response["Simulator-Signature"]
    parts = dict(p.split("=", 1) for p in header.split(","))
    mac = hmac.new(secret.encode(), f"{parts['t']}.".encode() + response.content,
                   hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, parts["v1"])


# --- test cards -------------------------------------------------------------------------

def test_known_cards():
    assert behaviour_for("4242 4242 4242 4242") is Behaviour.APPROVE
    assert behaviour_for("4000000000000119") is Behaviour.TIMEOUT
    assert behaviour_for("4000000000000135") is Behaviour.DROP_REQUEST


def test_unknown_card_is_declined():
    assert behaviour_for("4111111111111111") is Behaviour.DECLINE_GENERIC


def test_brands():
    assert brand_for("4242424242424242") == "visa"
    assert brand_for("5555555555554444") == "mastercard"
    assert brand_for("2223003122003222") == "mastercard"
    assert brand_for("378282246310005") == "unknown"


def test_card_expiry_is_end_of_month():
    assert not card_expired(11, 2026, date(2026, 11, 30))
    assert card_expired(10, 2026, date(2026, 11, 1))


# --- authorise --------------------------------------------------------------------------

def test_approve_records_authorisation_and_balanced_hold(gw, settings, operation_schema):
    r = gw.post_json("/sim/authorise", body())
    assert r.status_code == 200
    data = r.json()
    operation_schema.validate(data)
    assert data["status"] == "approved"
    assert data["card"] == {"brand": "visa", "last4": "4242"}
    assert data["authorisation"] == {"state": "open", "authorised_amount": 4590,
                                     "captured_amount": 0, "refunded_amount": 0}
    assert signature_ok(r, settings.SIMULATOR_SIGNING_SECRET)
    entries = LedgerEntry.objects.filter(operation_id=OP)
    assert entries.count() == 2 and sum(e.amount for e in entries) == 0


@pytest.mark.parametrize(("number", "reason"), [
    ("4000000000009995", "insufficient_funds"),
    ("4000000000000002", "card_declined"),
    ("4111111111111111", "card_declined"),
])
def test_declines_are_200_without_authorisation(gw, operation_schema, number, reason):
    r = gw.post_json("/sim/authorise", body(number))
    assert r.status_code == 200
    data = r.json()
    operation_schema.validate(data)
    assert (data["status"], data["decline_reason"], data["authorisation"]) == (
        "declined", reason, None)
    assert not LedgerEntry.objects.exists()


def test_expired_card_is_declined(gw):
    r = gw.post_json("/sim/authorise", body(month=1, year=2020))
    assert r.json()["decline_reason"] == "expired_card"


def test_card_number_and_cvc_are_never_stored(gw):
    gw.post_json("/sim/authorise", body())
    with connection.cursor() as cur:
        cur.execute("SELECT row_to_json(o)::text FROM operations_operationrecord o")
        rows = " ".join(r[0] for r in cur.fetchall())
    assert "4242424242424242" not in rows and '"123"' not in rows


def test_same_request_replays_without_second_effect(gw):
    first = gw.post_json("/sim/authorise", body())
    again = gw.post_json("/sim/authorise", body())
    assert again.status_code == 200 and again["Simulator-Replayed"] == "true"
    assert again.json() == first.json()
    assert OperationRecord.objects.count() == 1 and Authorisation.objects.count() == 1
    assert LedgerEntry.objects.count() == 2


def test_different_request_same_id_is_conflict(gw, settings):
    gw.post_json("/sim/authorise", body())
    r = gw.post_json("/sim/authorise", body(amount=9999))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "operation_id_conflict"
    assert r.json()["operation"]["amount"] == 4590
    assert signature_ok(r, settings.SIMULATOR_SIGNING_SECRET)


def test_luhn_failure_is_invalid_card_and_not_recorded(gw):
    r = gw.post_json("/sim/authorise", body("4242424242424241"))
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_card"
    assert "4242424242424241" not in r.content.decode()
    assert not OperationRecord.objects.exists()


@pytest.mark.parametrize(("change", "code"), [
    ({"currency": "USD"}, "currency_not_supported"),
    ({"amount": 0}, "invalid_field"),
    ({"operation_id": "op_short"}, "invalid_field"),
    ({"extra": 1}, "invalid_field"),
])
def test_bad_requests(gw, change, code):
    r = gw.post_json("/sim/authorise", body(**change))
    assert r.status_code == 400 and r.json()["error"]["code"] == code


def test_missing_or_wrong_key_is_401_unsigned(gw):
    r = gw.post_json("/sim/authorise", body(), HTTP_AUTHORIZATION="Bearer simk_wrong")
    assert r.status_code == 401 and "Simulator-Signature" not in r
    r = gw.post_json("/sim/authorise", body(), HTTP_AUTHORIZATION="")
    assert r.status_code == 401


def test_lost_response_card_records_but_does_not_answer(gw):
    r = gw.post_json("/sim/authorise", body("4000000000000119"))
    assert r.status_code == 504 and "Simulator-Signature" not in r
    assert OperationRecord.objects.get(operation_id=OP).status == "approved"


def test_slow_card_records_and_answers_late(gw):
    r = gw.post_json("/sim/authorise", body("4000000000000127"))
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_lost_request_card_records_nothing(gw):
    r = gw.post_json("/sim/authorise", body("4000000000000135"))
    assert r.status_code == 504
    assert not OperationRecord.objects.exists()


# --- status query (section 6) -------------------------------------------------------------

def test_status_query_returns_the_record(gw, operation_schema):
    gw.post_json("/sim/authorise", body("4000000000000119"))  # S4: answer lost
    r = gw.get(f"/sim/operations/{OP}?type=authorise")
    assert r.status_code == 200
    operation_schema.validate(r.json())
    assert r.json()["status"] == "approved"


def test_status_query_retires_unknown_id(gw, operation_schema):
    r = gw.get(f"/sim/operations/{OP}?type=authorise")
    data = r.json()
    operation_schema.validate(data)
    assert data["status"] == "no_record" and data["payment_id"] is None
    # The late arrival is rejected and records nothing.
    late = gw.post_json("/sim/authorise", body())
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "operation_retired"
    assert not Authorisation.objects.exists()
    # The no_record entry never changes.
    assert gw.get(f"/sim/operations/{OP}").json() == data


def test_status_query_rejects_bad_id_and_type(gw):
    assert gw.get("/sim/operations/nope").status_code == 400
    assert gw.get(f"/sim/operations/{OP}?type=teleport").status_code == 400


@pytest.mark.django_db(transaction=True)
def test_status_query_and_authorise_race_has_one_winner(settings):
    """Run both at once many times: either approved and reported, or retired and rejected."""
    from conftest import GatewayClient

    for i in range(10):
        op = f"op_{i:024d}"
        results = {}

        def call_authorise(op=op, results=results):
            results["auth"] = GatewayClient(settings.SIMULATOR_API_KEY).post_json(
                "/sim/authorise", body(op=op))
            connection.close()

        def call_query(op=op, results=results):
            results["query"] = GatewayClient(settings.SIMULATOR_API_KEY).get(
                f"/sim/operations/{op}")
            connection.close()

        threads = [threading.Thread(target=call_authorise), threading.Thread(target=call_query)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        query = json.loads(results["query"].content)
        auth = results["auth"]
        if query["status"] == "no_record":
            assert auth.status_code == 409
            assert not Authorisation.objects.filter(operation_id=op).exists()
        else:
            assert auth.status_code == 200 and query["status"] == "approved"
        assert OperationRecord.objects.filter(operation_id=op).count() == 1



def test_demo_range_approves_only_luhn_valid_numbers():
    from apps.operations.test_cards import luhn_ok, new_demo_card

    for prefix in ("42424200", "55555500"):
        number = new_demo_card(prefix)
        assert number.startswith(prefix) and len(number) == 16 and luhn_ok(number)
        assert behaviour_for(number) is Behaviour.APPROVE
        bad = number[:-1] + str((int(number[-1]) + 1) % 10)
        assert behaviour_for(bad) is Behaviour.DECLINE_GENERIC
    assert behaviour_for("4242420000000000"[:15]) is Behaviour.DECLINE_GENERIC
    assert brand_for(new_demo_card("55555500")) == "mastercard"


def test_shopper_banks_approve_across_ten_thousand_ranges():
    from apps.operations.test_cards import new_demo_card

    for bank in ("0000", "3712", "9999"):
        number = new_demo_card(f"51{bank}00")
        assert behaviour_for(number) is Behaviour.APPROVE
        assert brand_for(number) == "mastercard"
    bad = new_demo_card("51000000")
    assert behaviour_for(bad[:-1] + str((int(bad[-1]) + 1) % 10)) is Behaviour.DECLINE_GENERIC
    assert behaviour_for(new_demo_card("51000110")) is Behaviour.DECLINE_GENERIC  # not 51BB BB00


def test_new_demo_card_command_prints_fresh_numbers():
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("new_demo_card", count=3, stdout=out)
    lines = out.getvalue().split("\n")[:3]
    assert len({*lines}) == 3
    assert all(behaviour_for(line) is Behaviour.APPROVE for line in lines)


def test_demo_ranges_behave_like_their_fixed_cards():
    from apps.operations.test_cards import DEMO_KINDS, luhn_ok, new_demo_card

    expected = {"approve": Behaviour.APPROVE, "approve-mastercard": Behaviour.APPROVE,
                "decline": Behaviour.DECLINE_FUNDS, "lost-response": Behaviour.TIMEOUT}
    for kind, behaviour in expected.items():
        number = new_demo_card(DEMO_KINDS[kind])
        assert luhn_ok(number) and behaviour_for(number) is behaviour
        bad = number[:-1] + str((int(number[-1]) + 1) % 10)
        assert behaviour_for(bad) is Behaviour.DECLINE_GENERIC
    # The fixed cards still win over the ranges.
    assert behaviour_for("4000000000000119") is Behaviour.TIMEOUT
    assert behaviour_for("4000000000009995") is Behaviour.DECLINE_FUNDS


def test_new_demo_card_set_prints_one_fresh_card_per_scene():
    from io import StringIO

    from django.core.management import call_command

    from apps.operations.management.commands.new_demo_card import DEMO_SET

    out = StringIO()
    call_command("new_demo_card", scene_set=True, stdout=out)
    lines = out.getvalue().strip().split("\n")
    cards = [line[:19] for line in lines[:len(DEMO_SET)]]
    assert len(set(cards)) == len(DEMO_SET)
    assert behaviour_for(cards[0]) is Behaviour.APPROVE
    assert behaviour_for(cards[3]) is Behaviour.TIMEOUT
    assert behaviour_for(cards[4]) is Behaviour.DECLINE_FUNDS
    assert "S4" in lines[3]
