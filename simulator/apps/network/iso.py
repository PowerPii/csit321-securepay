"""ISO 8583-style messages of the SecurePay Network (v2, contracts/NETWORK-CONTRACT.md).

Real card networks send binary ISO 8583. We keep its vocabulary (message type indicators,
numbered data elements, two-digit response codes) and carry it as JSON, so a trace can be
read without a decoder:

    {"mti": "0100", "fields": {"2": "4242...", "3": "000000", "4": "000000004590", ...}}

Data elements used (all strings, as in ISO 8583):
  2 PAN, 3 processing code, 4 amount (12 digits, minor units), 7 transmission time
  (MMDDhhmmss UTC), 11 STAN (6 digits), 14 expiry (YYMM), 32 acquirer ID, 37 retrieval
  reference number (12), 38 authorisation code (6, approvals only), 39 response code,
  41 terminal ID, 42 card acceptor (merchant) ID, 48 additional data (v2 3-D Secure: "3DS2"
  + ECI + three_ds_id + CAVV, the bank's proof that the cardholder passed its challenge),
  49 currency (numeric, 702 = SGD),
  90 original data (follow-ups only: original MTI + STAN + RRN of the authorisation).

Message types: 0100/0110 authorisation, 0220/0230 capture (completion advice), 0400/0410
cancel (reversal of the authorisation), 0200/0210 refund (financial request, DE3 200000).
Follow-ups carry no card number: they point at the authorisation through DE 90.

The security code (CVC) never leaves the acquirer, and no service stores the PAN.
"""
import secrets
import string
from datetime import UTC, datetime

AUTH_REQUEST = "0100"
AUTH_RESPONSE = "0110"
FINANCIAL_REQUEST = "0200"  # refund
ADVICE = "0220"  # capture: completion of an earlier authorisation
REVERSAL = "0400"  # cancel: reverses the authorisation and releases the hold
FOLLOW_UPS = (ADVICE, REVERSAL, FINANCIAL_REQUEST)
RESPONSE_MTI = {AUTH_REQUEST: AUTH_RESPONSE, FINANCIAL_REQUEST: "0210", ADVICE: "0230",
                REVERSAL: "0410"}
PURCHASE = "000000"  # DE3: goods and services, default accounts
REFUND = "200000"  # DE3: refund (returns)
SGD = "702"
ACQUIRER_ID = "SPACQ01"  # DE32: the SecurePay acquirer
TERMINAL_ID = "SPGW0001"  # DE41: the gateway

APPROVED = "00"
DO_NOT_HONOUR = "05"
INSUFFICIENT_FUNDS = "51"
EXPIRED_CARD = "54"
INVALID_CARD_NUMBER = "14"  # v2 issuing: no such card in the bank's range
EXCEEDS_LIMIT = "61"  # v2 issuing: over the issued card's spending limit
RESTRICTED_CARD = "62"  # v2 issuing: cancelled, or a single-use card already used
NO_SUCH_ISSUER = "15"
ISSUER_UNAVAILABLE = "91"
SYSTEM_MALFUNCTION = "96"

RESPONSE_TEXT = {
    APPROVED: "Approved",
    DO_NOT_HONOUR: "Do not honour",
    INSUFFICIENT_FUNDS: "Insufficient funds",
    EXPIRED_CARD: "Expired card",
    INVALID_CARD_NUMBER: "Invalid card number",
    EXCEEDS_LIMIT: "Exceeds amount limit",
    RESTRICTED_CARD: "Restricted card",
    NO_SUCH_ISSUER: "No such issuer",
    ISSUER_UNAVAILABLE: "Issuer unavailable",
    SYSTEM_MALFUNCTION: "System malfunction",
}

# What the simulator's operation record says for each decline (simulator-operation schema).
DECLINE_REASON = {
    DO_NOT_HONOUR: "card_declined",
    INSUFFICIENT_FUNDS: "insufficient_funds",
    EXPIRED_CARD: "expired_card",
    INVALID_CARD_NUMBER: "card_declined",
    EXCEEDS_LIMIT: "insufficient_funds",
    RESTRICTED_CARD: "card_declined",
    NO_SUCH_ISSUER: "card_declined",
    ISSUER_UNAVAILABLE: "issuer_unavailable",
    SYSTEM_MALFUNCTION: "issuer_unavailable",
}

_DIGITS = string.digits
_ALNUM = string.digits + string.ascii_uppercase


def _random(alphabet: str, n: int) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(n))


def masked(pan: str) -> str:
    """First six and last four, as a trace may show them."""
    return f"{pan[:6]}{'*' * max(len(pan) - 10, 0)}{pan[-4:]}" if len(pan) > 10 else "****"


def authorisation_request(*, pan: str, expiry_month: int, expiry_year: int, amount: int,
                          merchant_id: str, now: datetime | None = None,
                          authentication: dict | None = None) -> dict:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    message = {
        "mti": AUTH_REQUEST,
        "fields": {
            "2": pan,
            "3": PURCHASE,
            "4": f"{amount:012d}",
            "7": now.strftime("%m%d%H%M%S"),
            "11": _random(_DIGITS, 6),
            "14": f"{expiry_year % 100:02d}{expiry_month:02d}",
            "32": ACQUIRER_ID,
            "37": _random(_ALNUM, 12),
            "41": TERMINAL_ID,
            "42": merchant_id[-15:],
            "49": SGD,
        },
    }
    if authentication:
        message["fields"]["48"] = (f"3DS2{authentication['eci']}"
                                   f"{authentication['three_ds_id']}{authentication['cavv']}")
    return message


def original_data(network: dict) -> str:
    """DE 90 for a follow-up: the authorisation's MTI, STAN and RRN (22 characters)."""
    return f"{AUTH_REQUEST}{network['stan']}{network['rrn']}"


def valid_original_data(value) -> bool:
    return (isinstance(value, str) and len(value) == 22 and value.startswith(AUTH_REQUEST)
            and value[4:10].isdigit() and all(c in _ALNUM for c in value[10:]))


def follow_up(*, mti: str, amount: int, merchant_id: str, original: dict,
              now: datetime | None = None) -> dict:
    """A capture (0220), cancel (0400) or refund (0200) for an earlier authorisation."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    return {
        "mti": mti,
        "fields": {
            "3": REFUND if mti == FINANCIAL_REQUEST else PURCHASE,
            "4": f"{amount:012d}",
            "7": now.strftime("%m%d%H%M%S"),
            "11": _random(_DIGITS, 6),
            "32": ACQUIRER_ID,
            "37": _random(_ALNUM, 12),
            "41": TERMINAL_ID,
            "42": merchant_id[-15:],
            "49": SGD,
            "90": original_data(original),
        },
    }


def response_to(request: dict, code: str, auth_code: str | None = None) -> dict:
    """The response for a request: echoes the keys the acquirer matches on, never the PAN.

    Only an approved authorisation gets an authorisation code (DE 38).
    """
    fields = request.get("fields", {})
    out = {k: fields[k] for k in ("3", "4", "7", "11", "32", "37", "41", "42", "49", "90")
           if k in fields}
    out["39"] = code
    mti = request.get("mti")
    if code == APPROVED and mti == AUTH_REQUEST:
        out["38"] = auth_code or _random(_ALNUM, 6)
    return {"mti": RESPONSE_MTI.get(mti, AUTH_RESPONSE), "fields": out}


def response_code(message: dict | None) -> str:
    try:
        code = message["fields"]["39"]
    except (KeyError, TypeError):
        return SYSTEM_MALFUNCTION
    return code if isinstance(code, str) and len(code) == 2 else SYSTEM_MALFUNCTION
