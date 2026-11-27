"""ISO 8583-style messages of the SecurePay Network (v2, contracts/NETWORK-CONTRACT.md).

Real card networks send binary ISO 8583. We keep its vocabulary (message type indicators,
numbered data elements, two-digit response codes) and carry it as JSON, so a trace can be
read without a decoder:

    {"mti": "0100", "fields": {"2": "4242...", "3": "000000", "4": "000000004590", ...}}

Data elements used (all strings, as in ISO 8583):
  2 PAN, 3 processing code, 4 amount (12 digits, minor units), 7 transmission time
  (MMDDhhmmss UTC), 11 STAN (6 digits), 14 expiry (YYMM), 32 acquirer ID, 37 retrieval
  reference number (12), 38 authorisation code (6, approvals only), 39 response code,
  41 terminal ID, 42 card acceptor (merchant) ID, 49 currency (numeric, 702 = SGD).

The security code (CVC) never leaves the acquirer, and no service stores the PAN.
"""
import secrets
import string
from datetime import UTC, datetime

AUTH_REQUEST = "0100"
AUTH_RESPONSE = "0110"
PURCHASE = "000000"  # DE3: goods and services, default accounts
SGD = "702"
ACQUIRER_ID = "SPACQ01"  # DE32: the SecurePay acquirer
TERMINAL_ID = "SPGW0001"  # DE41: the gateway

APPROVED = "00"
DO_NOT_HONOUR = "05"
INSUFFICIENT_FUNDS = "51"
EXPIRED_CARD = "54"
NO_SUCH_ISSUER = "15"
ISSUER_UNAVAILABLE = "91"
SYSTEM_MALFUNCTION = "96"

RESPONSE_TEXT = {
    APPROVED: "Approved",
    DO_NOT_HONOUR: "Do not honour",
    INSUFFICIENT_FUNDS: "Insufficient funds",
    EXPIRED_CARD: "Expired card",
    NO_SUCH_ISSUER: "No such issuer",
    ISSUER_UNAVAILABLE: "Issuer unavailable",
    SYSTEM_MALFUNCTION: "System malfunction",
}

# What the simulator's operation record says for each decline (simulator-operation schema).
DECLINE_REASON = {
    DO_NOT_HONOUR: "card_declined",
    INSUFFICIENT_FUNDS: "insufficient_funds",
    EXPIRED_CARD: "expired_card",
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
                          merchant_id: str, now: datetime | None = None) -> dict:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    return {
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


def response_to(request: dict, code: str, auth_code: str | None = None) -> dict:
    """The 0110 for a 0100: echoes the keys the acquirer matches on, never the PAN."""
    fields = request.get("fields", {})
    out = {k: fields[k] for k in ("3", "4", "7", "11", "32", "37", "41", "42", "49")
           if k in fields}
    out["39"] = code
    if code == APPROVED:
        out["38"] = auth_code or _random(_ALNUM, 6)
    return {"mti": AUTH_RESPONSE, "fields": out}


def response_code(message: dict | None) -> str:
    try:
        code = message["fields"]["39"]
    except (KeyError, TypeError):
        return SYSTEM_MALFUNCTION
    return code if isinstance(code, str) and len(code) == 2 else SYSTEM_MALFUNCTION
