"""v2 network split, slice 2: capture, cancel and refund cross the network too."""
import httpx

from apps.ledger.models import LedgerEntry
from apps.network import iso, nodes, transport
from apps.operations.models import Authorisation, OperationRecord
from apps.operations.test_settlement import AUTH, CAN, CAP, MER, authorised  # noqa: F401

RFD_OP = "op_" + "R" * 24
RFD = "rfd_" + "D" * 24


def capture(gw, amount=4000, op=CAP):
    return gw.post_json("/sim/capture", {"operation_id": op, "authorisation_id": AUTH,
                                         "amount": amount}).json()


def nodes_of(data):
    return [(h["node"], h["mti_in"], h["mti_out"]) for h in data["network"]["trace"]]


def test_capture_is_a_completion_advice(gw, authorised, operation_schema):  # noqa: F811
    data = capture(gw)
    operation_schema.validate(data)
    assert data["status"] == "approved"
    assert nodes_of(data) == [("acquirer", "0220", "0230"), ("switch", "0220", "0230"),
                              ("issuer", "0220", "0230")]
    net = data["network"]
    assert net["response_code"] == "00" and net["auth_code"] is None
    assert net["stan"] != authorised["network"]["stan"]  # a new message, a new STAN


def test_cancel_is_a_reversal(gw, authorised,  # noqa: F811
                               operation_schema):
    cancelled = gw.post_json("/sim/cancel", {"operation_id": CAN,
                                             "authorisation_id": AUTH}).json()
    operation_schema.validate(cancelled)
    assert cancelled["status"] == "approved"
    assert nodes_of(cancelled)[0] == ("acquirer", "0400", "0410")


def test_refund_is_a_financial_request(gw, authorised, operation_schema):  # noqa: F811
    capture(gw)
    refunded = gw.post_json("/sim/refund", {"operation_id": RFD_OP, "authorisation_id": AUTH,
                                            "refund_id": RFD, "amount": 1000}).json()
    operation_schema.validate(refunded)
    assert refunded["status"] == "approved"
    assert nodes_of(refunded)[-1] == ("issuer", "0200", "0210")


def test_acquirer_rule_declines_are_never_sent(gw, authorised, operation_schema):  # noqa: F811
    data = capture(gw, amount=4591)
    operation_schema.validate(data)
    assert data["decline_reason"] == "capture_exceeds_authorised" and data["network"] is None


def test_authorisation_from_before_v2_settles_without_the_network(gw, authorised):  # noqa: F811
    OperationRecord.objects.filter(operation_id=AUTH).update(network=None)
    data = capture(gw)
    assert data["status"] == "approved" and data["network"] is None


def test_network_down_at_capture_declines_and_keeps_the_hold(gw, authorised, settings,  # noqa: F811
                                                            monkeypatch, operation_schema):
    settings.SIM_SWITCH_URL = "http://network-switch:8001/net/switch"

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(transport, "TRANSPORT", httpx.MockTransport(down))
    data = capture(gw)
    operation_schema.validate(data)
    assert data["status"] == "declined" and data["decline_reason"] == "issuer_unavailable"
    assert data["network"]["response_code"] == "91"
    assert Authorisation.objects.get().state == "open"
    assert not LedgerEntry.objects.filter(operation_id=CAP).exists()


def test_follow_up_without_valid_original_data_is_refused():
    original = {"stan": "123456", "rrn": "ABCDEFGHIJKL"}
    message = iso.follow_up(mti=iso.ADVICE, amount=100, merchant_id=MER, original=original)
    assert message["fields"]["90"] == "0100123456ABCDEFGHIJKL" and "2" not in message["fields"]
    assert iso.response_code(nodes.issuer(message)["message"]) == iso.APPROVED

    message["fields"]["90"] = "garbage"
    assert iso.response_code(nodes.issuer(message)["message"]) == iso.SYSTEM_MALFUNCTION
    switched = nodes.switch(message, nodes.issuer)
    assert iso.response_code(switched["message"]) == iso.NO_SUCH_ISSUER
    assert switched["message"]["mti"] == "0230"
