"""SecurePay Bank's access control server (ACS) for 3-D Secure (docs/v2/THREE-DS.md).

The gateway asks the bank to authenticate a risky payment before it authorises it:

1. `authenticate`: is the card enrolled? If not, `not_enrolled` and the gateway uses its
   own check (email code or passkey). If so, the bank starts a challenge: it "texts" a
   six-digit code to the cardholder's phone. There is no phone in the simulator, so the
   reply carries the code as `test_code`, and the checkout shows it as a simulated text.
2. `challenge`: the cardholder's code. Right: `authenticated` (transStatus Y) with an ECI
   and a CAVV, the bank's proof. Wrong: the attempts left; the third wrong code is
   `failed` (transStatus N). Ten minutes after the start the challenge has expired.
3. The 0100 then carries the proof in DE 48. The issuer checks it (`check_proof`): the
   right card, the same amount, a CAVV the bank made, and used once. Anything else is 05.

Enrolled: SecurePay Bank's issued cards (4571 20), the 3-D Secure test card
4000 0000 0000 3220 and the demo range 4000 3220 (SIMULATOR-CONTRACT.md section 5).
"""
import base64
import hashlib
import hmac
import re
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.http import ApiError, rfc3339
from apps.common.ids import new_id
from apps.common.validation import is_integer
from apps.issuing import service as issuing
from apps.operations.test_cards import THREE_DS_CARD, THREE_DS_PREFIX, digits_only, luhn_ok
from apps.threeds.models import Authentication

THREE_DS_ID = re.compile(r"^3ds_[0-9A-Za-z]{24}$")
MERCHANT_ID = re.compile(r"^mer_[0-9A-Za-z]{24}$")
PAYMENT_ID = re.compile(r"^pay_[0-9A-Za-z]{24}$")
CARD_NUMBER = re.compile(r"^[0-9]{13,19}$")
FIELDS = {"payment_id", "merchant_id", "amount", "card"}
CARD_FIELDS = {"number", "expiry_month", "expiry_year"}
MAX_ATTEMPTS = 3
LIFETIME = timedelta(minutes=10)
ECI_AUTHENTICATED = "05"  # Visa: fully authenticated
DE48_PREFIX = "3DS2"  # DE 48 = "3DS2" + ECI (2) + three_ds_id (28) + CAVV (28)
BANK = "SecurePay Bank"


def enrolled(pan: str) -> bool:
    digits = digits_only(pan)
    if digits == THREE_DS_CARD or issuing.is_issued(digits):
        return True
    return len(digits) == 16 and digits.startswith(THREE_DS_PREFIX) and luhn_ok(digits)


def _key() -> bytes:
    secret = settings.SIMULATOR_SIGNING_SECRET
    if not secret:
        raise RuntimeError("SIMULATOR_SIGNING_SECRET is not set; 3-D Secure cannot run.")
    return hashlib.sha256(f"threeds|{secret}".encode()).digest()


def _mac(message: str) -> bytes:
    return hmac.new(_key(), message.encode(), hashlib.sha256).digest()


def pan_hash(pan: str) -> str:
    return _mac("pan|" + digits_only(pan)).hex()


def one_time_code(three_ds_id: str) -> str:
    return f"{int.from_bytes(_mac('otp|' + three_ds_id)[:8], 'big') % 1_000_000:06d}"


def cavv(auth: Authentication) -> str:
    """The bank's authentication value: 28 base64 characters, bound to card and amount."""
    raw = _mac(f"cavv|{auth.three_ds_id}|{auth.pan_hash}|{auth.amount}")[:20]
    return base64.b64encode(raw).decode()


def phone_hint(auth: Authentication) -> str:
    return "•••• " + str(int(auth.pan_hash[:8], 16) % 100).zfill(2)


def parse(body) -> dict:
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_request", "Body must be a JSON object.")
    for name in sorted(FIELDS):
        if name not in body:
            raise ApiError(400, "missing_field", f"'{name}' is required.", name)
    for name in body:
        if name not in FIELDS:
            raise ApiError(400, "invalid_field", f"Unknown field '{name}'.", name)
    if not PAYMENT_ID.match(str(body["payment_id"])):
        raise ApiError(400, "invalid_field", "'payment_id' is not a payment ID.", "payment_id")
    if not MERCHANT_ID.match(str(body["merchant_id"])):
        raise ApiError(400, "invalid_field", "'merchant_id' is not a merchant ID.",
                       "merchant_id")
    if not is_integer(body["amount"]) or not 1 <= body["amount"] <= 1_000_000:
        raise ApiError(400, "invalid_field", "'amount' must be 1 to 1000000 cents.", "amount")
    card = body["card"]
    if not isinstance(card, dict) or set(card) != CARD_FIELDS:
        raise ApiError(400, "invalid_field", "'card' needs number, expiry_month and "
                       "expiry_year.", "card")
    if not isinstance(card["number"], str) or not CARD_NUMBER.match(card["number"]) \
            or not luhn_ok(card["number"]):
        raise ApiError(400, "invalid_field", "'card.number' is not a card number.",
                       "card.number")
    return body


def _expired(auth: Authentication, now=None) -> bool:
    return auth.status == Authentication.Status.PENDING and (now or timezone.now()) >= \
        auth.expires_at


def to_dict(auth: Authentication) -> dict:
    expired = _expired(auth)
    status = "expired" if expired else auth.status
    data = {
        "object": "three_ds", "three_ds_id": auth.three_ds_id, "payment_id": auth.payment_id,
        "status": status, "trans_status": {"authenticated": "Y", "failed": "N"}.get(status),
        "acs": BANK, "phone_hint": phone_hint(auth), "last4": auth.last4,
        "amount": auth.amount,
        "attempts_remaining": max(0, MAX_ATTEMPTS - auth.wrong_attempts),
        "expires_at": rfc3339(auth.expires_at),
        "eci": None, "cavv": None, "test_code": None,
    }
    if status == Authentication.Status.PENDING:
        data["test_code"] = one_time_code(auth.three_ds_id)  # the simulated text message
    elif status == Authentication.Status.AUTHENTICATED:
        data["eci"], data["cavv"] = ECI_AUTHENTICATED, cavv(auth)
    return data


def authenticate(body: dict) -> tuple[dict, bool]:
    """(reply, created). One authentication per payment: a retry gets the same one."""
    card = body["card"]
    if not enrolled(card["number"]):
        return {"object": "three_ds", "payment_id": body["payment_id"],
                "status": "not_enrolled"}, False
    hashed = pan_hash(card["number"])
    existing = Authentication.objects.filter(payment_id=body["payment_id"]).first()
    if existing is None:
        try:
            with transaction.atomic():
                auth = Authentication.objects.create(
                    three_ds_id=new_id("3ds"), merchant_id=body["merchant_id"],
                    payment_id=body["payment_id"], pan_hash=hashed,
                    last4=digits_only(card["number"])[-4:], amount=body["amount"],
                    expires_at=timezone.now() + LIFETIME)
            return to_dict(auth), True
        except IntegrityError:
            existing = Authentication.objects.get(payment_id=body["payment_id"])
    if (existing.merchant_id, existing.pan_hash, existing.amount) != (
            body["merchant_id"], hashed, body["amount"]):
        raise ApiError(409, "payment_id_conflict",
                       "This payment was already authenticated with other details.",
                       "payment_id")
    return to_dict(existing), False


def get(three_ds_id: str) -> Authentication:
    if not THREE_DS_ID.match(three_ds_id):
        raise ApiError(400, "invalid_field", "Not a 3-D Secure ID.", "three_ds_id")
    auth = Authentication.objects.filter(three_ds_id=three_ds_id).first()
    if auth is None:
        raise ApiError(404, "not_found", "No such authentication.", "three_ds_id")
    return auth


def challenge(three_ds_id: str, body) -> dict:
    """The cardholder's answer. Only a pending, unexpired challenge counts a code."""
    if not isinstance(body, dict) or set(body) != {"code"} or not isinstance(body["code"], str) \
            or not re.fullmatch(r"[0-9]{6}", body["code"]):
        raise ApiError(400, "invalid_field", "Send 'code', six digits.", "code")
    get(three_ds_id)
    with transaction.atomic():
        auth = Authentication.objects.select_for_update().get(three_ds_id=three_ds_id)
        if auth.status != Authentication.Status.PENDING or _expired(auth):
            return to_dict(auth)
        if hmac.compare_digest(body["code"], one_time_code(auth.three_ds_id)):
            auth.status = Authentication.Status.AUTHENTICATED
            auth.authenticated_at = timezone.now()
        else:
            auth.wrong_attempts += 1
            if auth.wrong_attempts >= MAX_ATTEMPTS:
                auth.status = Authentication.Status.FAILED
        auth.save(update_fields=["status", "authenticated_at", "wrong_attempts"])
    return to_dict(auth)


# --- the proof in the authorisation (NETWORK-CONTRACT.md section 10) ----------------------

def check_proof(fields: dict) -> bool:
    """The issuer's check of DE 48 on a 0100. True if there is none, or it is good.

    A good proof is used up, so the same CAVV cannot authorise a second payment.
    """
    value = fields.get("48")
    if value is None:
        return True
    if not isinstance(value, str) or not value.startswith(DE48_PREFIX) or len(value) != 62:
        return False
    eci, three_ds_id, given = value[4:6], value[6:34], value[34:]
    try:
        amount = int(fields.get("4", ""))
    except ValueError:
        return False
    with transaction.atomic():
        auth = (Authentication.objects.select_for_update()
                .filter(three_ds_id=three_ds_id).first())
        if (auth is None or auth.status != Authentication.Status.AUTHENTICATED
                or auth.used_at is not None or eci != ECI_AUTHENTICATED
                or auth.amount != amount or auth.pan_hash != pan_hash(str(fields.get("2", "")))
                or not hmac.compare_digest(given, cavv(auth))):
            return False
        auth.used_at = timezone.now()
        auth.save(update_fields=["used_at"])
    return True
