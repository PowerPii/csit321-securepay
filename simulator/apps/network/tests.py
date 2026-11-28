"""v2 network split: acquirer -> switch -> issuer (contracts/NETWORK-CONTRACT.md)."""
import json

import httpx
import pytest
from django.test import Client

from apps.network import acquirer, iso, nodes, signing, transport
from apps.operations.models import OperationRecord
from apps.operations.tests import body

AMEX = "378282246310005"  # Luhn-valid, but no issuer on the SecurePay Network


def request_for(number="4242424242424242", month=12, year=2030):
    return iso.authorisation_request(pan=number, expiry_month=month, expiry_year=year,
                                     amount=4590, merchant_id="mer_" + "B" * 24)


# --- messages -----------------------------------------------------------------------------

def test_authorisation_request_carries_no_security_code():
    message = request_for()
    fields = message["fields"]
    assert message["mti"] == "0100"
    assert fields["4"] == "000000004590" and fields["49"] == "702" and fields["14"] == "3012"
    assert len(fields["11"]) == 6 and len(fields["37"]) == 12
    # The CVC from body() never gets here (a random STAN or RRN may contain "123").
    assert "123" not in fields.values() and "cvc" not in json.dumps(message)


def test_response_echoes_the_keys_but_never_the_card_number():
    request = request_for()
    approved = iso.response_to(request, iso.APPROVED)
    assert approved["mti"] == "0110" and approved["fields"]["39"] == "00"
    assert len(approved["fields"]["38"]) == 6
    assert approved["fields"]["11"] == request["fields"]["11"]
    assert "2" not in approved["fields"] and "14" not in approved["fields"]
    assert "38" not in iso.response_to(request, iso.DO_NOT_HONOUR)["fields"]
    assert iso.response_code({"nonsense": 1}) == iso.SYSTEM_MALFUNCTION
    assert iso.masked("4242424242424242") == "424242******4242"


# --- issuer and switch --------------------------------------------------------------------

@pytest.mark.parametrize(("number", "code"), [
    ("4242424242424242", "00"), ("5555555555554444", "00"), ("4000000000000119", "00"),
    ("4000000000009995", "51"), ("4000000000000002", "05"), ("4111111111111111", "05"),
])
def test_issuer_answers_like_the_v1_test_cards(number, code):
    reply = nodes.issuer(request_for(number))
    assert iso.response_code(reply["message"]) == code
    assert reply["trace"][0]["node"] == "issuer"


def test_issuer_declines_an_expired_card():
    reply = nodes.issuer(request_for(month=10, year=2026), today=(2026, 11))
    assert iso.response_code(reply["message"]) == iso.EXPIRED_CARD
    still_valid = nodes.issuer(request_for(month=11, year=2026), today=(2026, 11))
    assert iso.response_code(still_valid["message"]) == iso.APPROVED


def test_switch_routes_by_card_and_reports_an_unreachable_issuer():
    reply = nodes.switch(request_for(), nodes.issuer)
    assert [h["node"] for h in reply["trace"]] == ["switch", "issuer"]

    unknown = nodes.switch(request_for(AMEX), nodes.issuer)
    assert iso.response_code(unknown["message"]) == iso.NO_SUCH_ISSUER
    assert [h["node"] for h in unknown["trace"]] == ["switch"]

    def down(message):
        raise nodes.NetworkError("no route")

    lost = nodes.switch(request_for(), down)
    assert iso.response_code(lost["message"]) == iso.ISSUER_UNAVAILABLE


# --- signatures ---------------------------------------------------------------------------

def test_signatures_are_checked_and_differ_from_simulator_replies(settings):
    data = b'{"mti":"0100"}'
    header = signing.sign(data, timestamp=1_000_000)
    assert signing.verify(data, header, now=1_000_100)
    assert not signing.verify(data + b" ", header, now=1_000_100)
    assert not signing.verify(data, header, now=1_000_000 + 301)  # too old
    assert not signing.verify(data, None) and not signing.verify(data, "t=x,v1=y")
    from apps.common.http import sign as simulator_sign
    assert simulator_sign(data, settings.SIMULATOR_SIGNING_SECRET, 1_000_000) != header


@pytest.mark.django_db
def test_node_endpoints_refuse_unsigned_messages():
    client = Client()
    data = json.dumps(request_for())
    for path in ("/net/switch", "/net/issuer"):
        assert client.post(path, data, content_type="application/json").status_code == 401


# --- through the acquirer -----------------------------------------------------------------

def test_approved_authorisation_records_the_trace(gw, operation_schema):
    r = gw.post_json("/sim/authorise", body())
    data = r.json()
    operation_schema.validate(data)
    assert data["status"] == "approved"
    net = data["network"]
    assert net["response_code"] == "00" and net["response_text"] == "Approved"
    assert len(net["auth_code"]) == 6
    assert [h["node"] for h in net["trace"]] == ["acquirer", "switch", "issuer"]
    assert "4242424242424242" not in r.content.decode()
    stored = OperationRecord.objects.get(operation_id=data["operation_id"]).network
    assert stored == net and "4242424242424242" not in json.dumps(stored)


@pytest.mark.parametrize(("number", "code", "reason"), [
    ("4000000000009995", "51", "insufficient_funds"),
    ("4000000000000002", "05", "card_declined"),
    (AMEX, "15", "card_declined"),
])
def test_declines_carry_the_issuer_code(gw, operation_schema, number, code, reason):
    data = gw.post_json("/sim/authorise", body(number)).json()
    operation_schema.validate(data)
    assert data["status"] == "declined" and data["decline_reason"] == reason
    assert data["network"]["response_code"] == code and data["network"]["auth_code"] is None


def _route_to_django(request: httpx.Request) -> httpx.Response:
    """Deliver an httpx request to this process's /net views, as another container would."""
    reply = Client().post(request.url.path, request.content, content_type="application/json",
                          HTTP_NETWORK_SIGNATURE=request.headers[signing.SIGNATURE_HEADER])
    return httpx.Response(reply.status_code, content=reply.content,
                          headers={k: v for k, v in reply.items()})


def test_nodes_in_their_own_containers_talk_signed_http(gw, settings, monkeypatch,
                                                        operation_schema):
    settings.SIM_SWITCH_URL = "http://network-switch:8001/net/switch"
    settings.SIM_ISSUER_URL = "http://network-issuer:8001/net/issuer"
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return _route_to_django(request)

    monkeypatch.setattr(transport, "TRANSPORT", httpx.MockTransport(handler))
    data = gw.post_json("/sim/authorise", body()).json()
    operation_schema.validate(data)
    assert data["status"] == "approved"
    assert seen == ["network-switch", "network-issuer"]
    assert [h["node"] for h in data["network"]["trace"]] == ["acquirer", "switch", "issuer"]


def test_unreachable_or_forged_network_is_issuer_unavailable(gw, settings, monkeypatch,
                                                            operation_schema):
    settings.SIM_SWITCH_URL = "http://network-switch:8001/net/switch"

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(transport, "TRANSPORT", httpx.MockTransport(down))
    data = gw.post_json("/sim/authorise", body()).json()
    operation_schema.validate(data)
    assert data["status"] == "declined" and data["decline_reason"] == "issuer_unavailable"
    assert data["network"]["response_code"] == "91"
    assert [h["node"] for h in data["network"]["trace"]] == ["acquirer"]

    def forged(request):  # an approval nobody signed is not an approval
        return httpx.Response(200, json={"message": iso.response_to(
            json.loads(request.content), iso.APPROVED), "trace": []})

    monkeypatch.setattr(transport, "TRANSPORT", httpx.MockTransport(forged))
    decision = acquirer.authorise(body())
    assert not decision.approved and decision.network["response_code"] == "91"
