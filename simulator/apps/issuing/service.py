"""SecurePay Bank issuing: create virtual cards and authorise against their limits.

docs/v2/ISSUING.md and contracts/NETWORK-CONTRACT.md section 9. Issued cards are 16-digit
Visa numbers in the bank's own range, `4571 20xx xxxx xxxx`. The bank authorises a 0100 on
one of them in this order: known card, expiry, status, then limit:

- unknown number in the range: 14 (invalid card number), as `card_declined`;
- expiry not the card's own, or in the past: 54;
- cancelled, or a single-use card already used: 62 (restricted card);
- amount over what is left of the spending limit: 61 (exceeds amount limit);
- otherwise 00, and the amount counts against the limit. A single-use card is used up.

A cancel (0400) gives the amount back to the limit, and a single-use card can pay again. A
capture (0220) for less than the authorisation gives back the difference. A refund (0200)
does not change the limit, as with a real card's spend controls.
"""
import hashlib
import hmac
import re
import secrets
from datetime import date

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.common.http import ApiError
from apps.issuing.models import IssuedCard, IssuerAuthorisation
from apps.network import iso
from apps.operations.test_cards import luhn_ok

ISSUED_BIN = "457120"  # SecurePay Bank's virtual card range
CARD_ID = re.compile(r"^ic_[0-9A-Za-z]{24}$")
MERCHANT_ID = re.compile(r"^mer_[0-9A-Za-z]{24}$")
MAX_LIMIT = 1_000_000  # S$10,000 in cents
VALID_YEARS = 3


def is_issued(pan: str) -> bool:
    return len(pan) == 16 and pan.startswith(ISSUED_BIN)


def _key() -> bytes:
    secret = settings.SIMULATOR_SIGNING_SECRET
    if not secret:
        raise RuntimeError("SIMULATOR_SIGNING_SECRET is not set; issuing cannot hash cards.")
    return hashlib.sha256(f"issuing|{secret}".encode()).digest()


def pan_hash(pan: str) -> str:
    return hmac.new(_key(), pan.encode(), hashlib.sha256).hexdigest()


def _new_number() -> str:
    body = ISSUED_BIN + "".join(str(secrets.randbelow(10)) for _ in range(9))
    return next(body + d for d in "0123456789" if luhn_ok(body + d))


def parse_create(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_request", "Body must be a JSON object.")
    if not CARD_ID.match(str(body.get("card_id", ""))):
        raise ApiError(400, "invalid_field", "'card_id' must be an ic_ ID.", "card_id")
    if not MERCHANT_ID.match(str(body.get("merchant_id", ""))):
        raise ApiError(400, "invalid_field", "'merchant_id' must be a mer_ ID.", "merchant_id")
    limit = body.get("spending_limit")
    if type(limit) is not int or not 100 <= limit <= MAX_LIMIT:
        raise ApiError(400, "invalid_field", "'spending_limit' must be 100 to 1000000 cents.",
                       "spending_limit")
    if type(body.get("single_use", False)) is not bool:
        raise ApiError(400, "invalid_field", "'single_use' must be true or false.",
                       "single_use")
    return {"card_id": body["card_id"], "merchant_id": body["merchant_id"],
            "spending_limit": limit, "single_use": body.get("single_use", False)}


def create(data: dict, today: date | None = None) -> tuple[IssuedCard, dict | None]:
    """A new card and its details, shown once. A repeated card_id returns the card only."""
    existing = IssuedCard.objects.filter(card_id=data["card_id"]).first()
    if existing is not None:
        if (existing.merchant_id, existing.spending_limit, existing.single_use) != (
                data["merchant_id"], data["spending_limit"], data["single_use"]):
            raise ApiError(409, "card_id_conflict",
                           "This card_id was already used with a different request.", "card_id")
        return existing, None
    today = today or date.today()
    for _ in range(5):  # a number collision in 10^9 is unlikely; try again if it happens
        number = _new_number()
        try:
            with transaction.atomic():
                card = IssuedCard.objects.create(
                    pan_hash=pan_hash(number), last4=number[-4:], expiry_month=today.month,
                    expiry_year=today.year + VALID_YEARS, **data)
        except IntegrityError:
            if IssuedCard.objects.filter(card_id=data["card_id"]).exists():
                return IssuedCard.objects.get(card_id=data["card_id"]), None
            continue
        secret = {"number": number, "cvc": f"{secrets.randbelow(1000):03d}"}
        return card, secret
    raise ApiError(503, "service_unavailable", "Could not create a card number; try again.")


def cancel(card_id: str) -> IssuedCard:
    with transaction.atomic():
        card = IssuedCard.objects.select_for_update().filter(card_id=card_id).first()
        if card is None:
            raise ApiError(404, "not_found", "No such card.", "card_id")
        if card.status != IssuedCard.Status.CANCELLED:
            card.status = IssuedCard.Status.CANCELLED
            card.save(update_fields=["status", "updated_at"])
    return card


def to_dict(card: IssuedCard, secret: dict | None = None) -> dict:
    data = {
        "object": "issued_card", "card_id": card.card_id, "merchant_id": card.merchant_id,
        "last4": card.last4, "expiry_month": card.expiry_month,
        "expiry_year": card.expiry_year, "spending_limit": card.spending_limit,
        "spent": card.spent, "remaining": max(0, card.spending_limit - card.spent),
        "single_use": card.single_use, "status": card.status,
        "authorisations": [
            {"stan": a.stan, "rrn": a.rrn, "amount": a.amount, "response_code": a.response_code,
             "response_text": iso.RESPONSE_TEXT.get(a.response_code, "Unknown"),
             "captured": a.captured, "reversed": a.reversed,
             "created_at": a.created_at.isoformat().replace("+00:00", "Z")}
            for a in card.authorisations.all()[:50]
        ],
    }
    if secret:
        data.update(secret)
    return data


def _expiry_matches(card: IssuedCard, yymm: str, today: tuple[int, int]) -> bool:
    if yymm != f"{card.expiry_year % 100:02d}{card.expiry_month:02d}":
        return False
    return (card.expiry_year, card.expiry_month) >= today


def authorise(fields: dict, today: tuple[int, int]) -> str:
    """The bank's answer to a 0100 on an issued card, recorded with the card."""
    with transaction.atomic():
        card = (IssuedCard.objects.select_for_update()
                .filter(pan_hash=pan_hash(fields["2"])).first())
        if card is None:
            return iso.INVALID_CARD_NUMBER
        try:
            amount = int(fields.get("4", ""))
        except ValueError:
            return iso.SYSTEM_MALFUNCTION
        if not _expiry_matches(card, fields.get("14", ""), today):
            code = iso.EXPIRED_CARD
        elif card.status != IssuedCard.Status.ACTIVE:
            code = iso.RESTRICTED_CARD
        elif amount > card.spending_limit - card.spent:
            code = iso.EXCEEDS_LIMIT
        else:
            code = iso.APPROVED
            card.spent += amount
            if card.single_use:
                card.status = IssuedCard.Status.USED
            card.save(update_fields=["spent", "status", "updated_at"])
        IssuerAuthorisation.objects.create(
            card=card, stan=fields.get("11", ""), rrn=fields.get("37", ""),
            acceptor_id=str(fields.get("42", ""))[:15], amount=amount, response_code=code)
    return code


def follow_up(mti: str, fields: dict) -> None:
    """Give back to the limit on a cancel, or on a capture for less than was authorised.

    Follow-ups for other cards, or for authorisations the bank declined, change nothing.
    """
    original = fields.get("90", "")
    stan, rrn = original[4:10], original[10:]
    try:
        amount = int(fields.get("4", ""))
    except ValueError:
        return
    with transaction.atomic():
        auth = (IssuerAuthorisation.objects.select_for_update().select_related("card")
                .filter(stan=stan, rrn=rrn, response_code=iso.APPROVED).first())
        if auth is None or auth.reversed:
            return
        card = IssuedCard.objects.select_for_update().get(pk=auth.card_id)
        if mti == iso.REVERSAL:
            give_back = auth.amount - (auth.captured or 0)
            auth.reversed = True
            if card.single_use and card.status == IssuedCard.Status.USED:
                card.status = IssuedCard.Status.ACTIVE
        elif mti == iso.ADVICE and auth.captured is None:
            auth.captured = min(amount, auth.amount)
            give_back = auth.amount - auth.captured
        else:
            return
        card.spent = max(0, card.spent - give_back)
        auth.save(update_fields=["reversed", "captured"])
        card.save(update_fields=["spent", "status", "updated_at"])
