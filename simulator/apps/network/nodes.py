"""The switch and the issuer (contracts/NETWORK-CONTRACT.md section 2).

Each node takes a 0100 and returns `{"message": <0110>, "trace": [hops]}`, its own hop
first. A hop names the node and the response code it saw, never the card number.

- The switch ("SecurePay Switch") routes an authorisation by the card's first digit: 4
  (Visa), 5 or 2 (Mastercard) go to the one issuer, anything else is answered 15 (no such
  issuer). A capture, cancel or refund has no card number; it goes to the issuer of the
  authorisation its DE 90 names (there is one issuer).
- The issuer ("SecurePay Bank") holds the cardholder accounts. Its decisions are the test
  cards of SIMULATOR-CONTRACT.md section 5, so every card behaves as it did in v1. It
  accepts every well-formed follow-up: the acquirer has already checked the amounts and
  the authorisation's state, as in v1.
- v2 issuing: cards in the bank's own range (4571 20) are virtual cards merchants created;
  the bank checks them against their stored limits (apps/issuing, NETWORK-CONTRACT.md 9).
"""
import time
from collections.abc import Callable

from django.utils import timezone

from apps.issuing import service as issuing
from apps.network import iso
from apps.operations.test_cards import Behaviour, behaviour_for

SWITCH = "switch"
ISSUER = "issuer"
ROUTED_PREFIXES = ("4", "5", "2")

ISSUER_CODES = {
    Behaviour.APPROVE: iso.APPROVED,
    # TIMEOUT and SLOW are approvals whose reply the acquirer loses or delays on the way back.
    Behaviour.TIMEOUT: iso.APPROVED,
    Behaviour.SLOW: iso.APPROVED,
    Behaviour.DROP_REQUEST: iso.APPROVED,  # never sent: the acquirer drops it first
    Behaviour.DECLINE_FUNDS: iso.INSUFFICIENT_FUNDS,
    Behaviour.DECLINE_GENERIC: iso.DO_NOT_HONOUR,
}


def hop(node: str, request: dict, response: dict, started: float) -> dict:
    return {"node": node, "mti_in": request.get("mti"), "mti_out": response.get("mti"),
            "response_code": iso.response_code(response),
            "elapsed_ms": max(0, round((time.monotonic() - started) * 1000))}


def _expired(yymm: str, today: tuple[int, int]) -> bool:
    try:
        year, month = 2000 + int(yymm[:2]), int(yymm[2:])
    except (TypeError, ValueError):
        return True
    return (year, month) < today


def issuer(request: dict, today: tuple[int, int] | None = None) -> dict:
    started = time.monotonic()
    fields = request.get("fields", {})
    if today is None:
        now = timezone.now().date()
        today = (now.year, now.month)
    mti = request.get("mti")
    if mti in iso.FOLLOW_UPS:
        code = iso.APPROVED if iso.valid_original_data(fields.get("90")) else \
            iso.SYSTEM_MALFUNCTION
        if code == iso.APPROVED:
            issuing.follow_up(mti, fields)  # gives back to an issued card's limit
    elif mti != iso.AUTH_REQUEST or "2" not in fields:
        code = iso.SYSTEM_MALFUNCTION
    elif issuing.is_issued(str(fields["2"])):
        code = issuing.authorise(fields, today)  # SecurePay Bank's own virtual cards
    elif _expired(fields.get("14", ""), today):
        code = iso.EXPIRED_CARD
    else:
        code = ISSUER_CODES[behaviour_for(fields["2"])]
    response = iso.response_to(request, code)
    return {"message": response, "trace": [hop(ISSUER, request, response, started)]}


def switch(request: dict, forward: Callable[[dict], dict]) -> dict:
    """Route to the issuer through `forward`; an issuer that cannot be reached is a 91."""
    started = time.monotonic()
    fields = request.get("fields", {})
    if request.get("mti") in iso.FOLLOW_UPS:
        routable = iso.valid_original_data(fields.get("90"))
    else:
        routable = str(fields.get("2", "")).startswith(ROUTED_PREFIXES)
    if not routable:
        response, downstream = iso.response_to(request, iso.NO_SUCH_ISSUER), []
    else:
        try:
            reply = forward(request)
            response, downstream = reply["message"], list(reply.get("trace", []))
        except NetworkError:
            response, downstream = iso.response_to(request, iso.ISSUER_UNAVAILABLE), []
    return {"message": response, "trace": [hop(SWITCH, request, response, started), *downstream]}


class NetworkError(Exception):
    """A node did not answer with a signed, well-formed reply."""
