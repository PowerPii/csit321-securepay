"""Synthetic test cards: the card number decides what the simulated bank does.

Must match SIMULATOR-CONTRACT.md section 5. Never accept real card numbers. Anything not
listed here is declined as `card_declined`.
"""
from enum import StrEnum


class Behaviour(StrEnum):
    APPROVE = "approve"
    DECLINE_FUNDS = "decline_insufficient_funds"
    DECLINE_GENERIC = "decline_generic"
    TIMEOUT = "timeout"  # drop_response: record the approval, never reply (lost response, S4)
    SLOW = "slow"  # delay_response: record the approval, reply after the gateway gave up
    DROP_REQUEST = "drop_request"  # lost request: record nothing, never reply (no_record)


TEST_CARDS: dict[str, Behaviour] = {
    "4242424242424242": Behaviour.APPROVE,
    "5555555555554444": Behaviour.APPROVE,
    "4000000000009995": Behaviour.DECLINE_FUNDS,
    "4000000000000002": Behaviour.DECLINE_GENERIC,
    "4000000000000119": Behaviour.TIMEOUT,
    "4000000000000127": Behaviour.SLOW,
    "4000000000000135": Behaviour.DROP_REQUEST,
}


def digits_only(pan: str) -> str:
    return "".join(c for c in pan if c.isdigit())


# Demo card ranges (demo-day polish): any Luhn-valid 16-digit number with one of these
# prefixes behaves like the matching fixed card above. Fraud screening counts a card's
# history over 30 days across the whole gateway, so rehearsals with 4242... or the S4 card
# 4000 0000 0000 0119 slowly make it look risky; a fresh number from
# `manage.py new_demo_card` has no history and behaves the same on every demo run.
DEMO_RANGES: dict[str, Behaviour] = {
    "42424200": Behaviour.APPROVE,  # Visa
    "55555500": Behaviour.APPROVE,  # Mastercard
    "40000099": Behaviour.DECLINE_FUNDS,  # like 4000 0000 0000 9995
    "40000119": Behaviour.TIMEOUT,  # like 4000 0000 0000 0119 (lost response, S4)
}
DEMO_APPROVE_PREFIXES = ("42424200", "55555500")
# v2 red team: 10,000 synthetic "shopper banks", 51BB BB00 xxxx xxxx with BBBB = 0000-9999,
# all approving. Background shoppers draw from many card ranges, as real shoppers do, so the
# learning loop's card-range features can tell them from a BIN attack on one range.


def shopper_bank_card(prefix8: str) -> bool:
    return len(prefix8) == 8 and prefix8.startswith("51") and prefix8.endswith("00")
# Names for `new_demo_card --kind`.
DEMO_KINDS = {"approve": "42424200", "approve-mastercard": "55555500",
              "decline": "40000099", "lost-response": "40000119"}


def behaviour_for(pan: str) -> Behaviour:
    digits = digits_only(pan)
    if digits in TEST_CARDS:
        return TEST_CARDS[digits]
    if len(digits) == 16 and luhn_ok(digits):
        behaviour = DEMO_RANGES.get(digits[:8])
        if behaviour is not None:
            return behaviour
        if shopper_bank_card(digits[:8]):
            return Behaviour.APPROVE
    return Behaviour.DECLINE_GENERIC


def new_demo_card(prefix: str = DEMO_APPROVE_PREFIXES[0]) -> str:
    """A random Luhn-valid number in a demo range."""
    import secrets

    body = prefix + "".join(str(secrets.randbelow(10)) for _ in range(15 - len(prefix)))
    for check in "0123456789":
        if luhn_ok(body + check):
            return body + check
    raise AssertionError("unreachable")


def brand_for(pan: str) -> str:
    """Brand as the operation schema allows it: visa, mastercard or unknown."""
    digits = digits_only(pan)
    if digits.startswith("4"):
        return "visa"
    if digits[:2] in {"51", "52", "53", "54", "55"} or (
        len(digits) >= 4 and 2221 <= int(digits[:4]) <= 2720
    ):
        return "mastercard"
    return "unknown"


def luhn_ok(digits: str) -> bool:
    if not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0
