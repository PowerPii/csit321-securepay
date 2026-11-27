"""The acquirer's side: turn an authorise request into a 0100 and read the 0110.

The simulator's /sim API is the acquirer. It no longer decides; it asks the issuer through
the switch and records what came back, with the trace (contracts/NETWORK-CONTRACT.md 5).
"""
import time
from dataclasses import dataclass

from apps.network import iso, nodes, transport

ACQUIRER = "acquirer"


@dataclass(frozen=True)
class Decision:
    approved: bool
    decline_reason: str
    network: dict


def authorise(body: dict) -> Decision:
    started = time.monotonic()
    card = body["card"]
    request = iso.authorisation_request(
        pan=card["number"], expiry_month=card["expiry_month"], expiry_year=card["expiry_year"],
        amount=body["amount"], merchant_id=body["merchant_id"])
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
    if code == iso.APPROVED:
        return Decision(True, "", network)
    return Decision(False, iso.DECLINE_REASON.get(code, "card_declined"), network)
