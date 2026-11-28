"""The acquirer's side: turn an authorise request into a 0100 and read the 0110.

The simulator's /sim API is the acquirer. It no longer decides; it asks the issuer through
the switch and records what came back, with the trace (contracts/NETWORK-CONTRACT.md 5).
"""
import time
from dataclasses import dataclass

from apps.network import iso, nodes, transport
from apps.operations.models import OperationRecord

ACQUIRER = "acquirer"
FOLLOW_UP_MTI = {OperationRecord.Type.CAPTURE: iso.ADVICE,
                 OperationRecord.Type.CANCEL: iso.REVERSAL,
                 OperationRecord.Type.REFUND: iso.FINANCIAL_REQUEST}


@dataclass(frozen=True)
class Decision:
    approved: bool
    decline_reason: str
    network: dict


def _exchange(request: dict, started: float, three_ds: dict | None = None) -> Decision:
    """Send to the switch and turn the response into a decision. Unreachable = 91."""
    try:
        reply = transport.to_switch(request)
        response, downstream = reply["message"], list(reply.get("trace", []))
    except nodes.NetworkError:
        response, downstream = iso.response_to(request, iso.ISSUER_UNAVAILABLE), []
    code = iso.response_code(response)
    fields = response.get("fields", {})
    network = {
        "stan": request["fields"]["11"],
        "rrn": request["fields"]["37"],
        "auth_code": fields.get("38") if code == iso.APPROVED else None,
        "response_code": code,
        "response_text": iso.RESPONSE_TEXT.get(code, "Unknown"),
        "trace": [nodes.hop(ACQUIRER, request, response, started), *downstream],
    }
    if three_ds is not None:  # v2: the 0100 carried the bank's 3-D Secure proof
        network["three_ds"] = three_ds
    if code == iso.APPROVED:
        return Decision(True, "", network)
    return Decision(False, iso.DECLINE_REASON.get(code, "card_declined"), network)


def authorise(body: dict) -> Decision:
    started = time.monotonic()
    card = body["card"]
    request = iso.authorisation_request(
        pan=card["number"], expiry_month=card["expiry_month"], expiry_year=card["expiry_year"],
        amount=body["amount"], merchant_id=body["merchant_id"],
        authentication=body.get("authentication"))
    authentication = body.get("authentication")
    three_ds = ({"three_ds_id": authentication["three_ds_id"], "eci": authentication["eci"]}
                if authentication else None)
    return _exchange(request, started, three_ds)


def follow_up(op_type: str, *, authorisation_id: str, amount: int, merchant_id: str
              ) -> Decision | None:
    """Capture, cancel or refund over the network, once the acquirer's own checks passed.

    None for an authorisation made before v2 (it has no STAN or RRN to point at): the
    acquirer then decides alone, as in v1.
    """
    started = time.monotonic()
    original = (OperationRecord.objects.filter(operation_id=authorisation_id)
                .values_list("network", flat=True).first())
    if not original:
        return None
    request = iso.follow_up(mti=FOLLOW_UP_MTI[op_type], amount=amount,
                            merchant_id=merchant_id, original=original)
    return _exchange(request, started)
