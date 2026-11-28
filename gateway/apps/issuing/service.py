"""Create, read and cancel issued cards (docs/v2/ISSUING.md), for the API and the dashboard.

Creating a card asks the bank first, with a card ID derived from the merchant, mode and
Idempotency-Key. A retry with the same key therefore reaches the same card at the bank, which
answers it without the number: the number is shown once, in the first reply. If that reply
was lost, the merchant cancels the card and makes another.
"""
import hashlib

from django.db import IntegrityError, transaction
from rest_framework import status

from apps.audit.models import record
from apps.common.errors import ApiError, invalid
from apps.common.ids import BASE62
from apps.common.timefmt import rfc3339
from apps.issuing import bank
from apps.issuing.models import IssuedCard
from apps.merchants.models import Merchant

MIN_LIMIT, MAX_LIMIT = 100, 1_000_000  # S$1 to S$10,000


def unavailable() -> ApiError:
    return ApiError(status.HTTP_503_SERVICE_UNAVAILABLE, "issuer_unavailable",
                    "The card issuer did not answer. Try again with the same Idempotency-Key.",
                    retry_after=2)


def _card_id(request_hash: str) -> str:
    n = int(request_hash, 16)
    chars = []
    for _ in range(24):
        n, r = divmod(n, 62)
        chars.append(BASE62[r])
    return "ic_" + "".join(chars)


def parse(body: dict) -> dict:
    limit = body.get("spending_limit")
    if type(limit) is not int or not MIN_LIMIT <= limit <= MAX_LIMIT:
        raise invalid("invalid_field", "'spending_limit' must be a whole number of cents from "
                      "100 to 1000000 (S$1 to S$10,000).", "spending_limit")
    single_use = body.get("single_use", False)
    if type(single_use) is not bool:
        raise invalid("invalid_field", "'single_use' must be true or false.", "single_use")
    description = body.get("description", "")
    if not isinstance(description, str) or len(description) > 100:
        raise invalid("invalid_field", "'description' must be text of at most 100 characters.",
                      "description")
    unknown = set(body) - {"spending_limit", "single_use", "description", "mode"}
    if unknown:
        name = min(unknown)
        raise invalid("invalid_field", f"Unknown field '{name}'.", name)
    return {"spending_limit": limit, "single_use": single_use,
            "description": description.strip()}


def create(merchant: Merchant, data: dict, *, key: str, livemode: bool, actor_type: str,
           actor_id: str, request_id: str = "") -> tuple[IssuedCard, dict | None]:
    """(card, secret). `secret` is {number, cvc} on the first reply only."""
    request_hash = hashlib.sha256(
        f"{merchant.public_id}|{int(livemode)}|{key}".encode()).hexdigest()
    existing = IssuedCard.objects.filter(request_hash=request_hash).first()
    if existing is not None:
        if (existing.spending_limit, existing.single_use, existing.description) != (
                data["spending_limit"], data["single_use"], data["description"]):
            raise ApiError(status.HTTP_409_CONFLICT, "idempotency_key_reused",
                           "This Idempotency-Key was already used with a different request.")
        return existing, None
    card_id = _card_id(request_hash)
    try:
        new, at_bank = bank.create_card(card_id=card_id, merchant_id=merchant.public_id,
                                        spending_limit=data["spending_limit"],
                                        single_use=data["single_use"])
    except bank.BankUnavailable:
        raise unavailable() from None
    except bank.BankRefused as exc:
        raise ApiError(exc.status, exc.code, "The card issuer refused the request.") from None
    try:
        with transaction.atomic():
            card = IssuedCard.objects.create(
                public_id=card_id, merchant=merchant, livemode=livemode,
                request_hash=request_hash, description=data["description"],
                last4=at_bank["last4"], expiry_month=at_bank["expiry_month"],
                expiry_year=at_bank["expiry_year"], spending_limit=data["spending_limit"],
                single_use=data["single_use"], status=at_bank["status"],
                spent=at_bank["spent"], created_by=actor_id)
            record("issued_card.created", actor_type=actor_type, actor_id=actor_id,
                   merchant_id=merchant.public_id, target_type="issued_card",
                   target_id=card.public_id, request_id=request_id,
                   details={"spending_limit": card.spending_limit,
                            "single_use": card.single_use, "livemode": livemode,
                            "last4": card.last4})
    except IntegrityError:  # a concurrent retry with the same key saved it first
        return IssuedCard.objects.get(request_hash=request_hash), None
    secret = {"number": at_bank["number"], "cvc": at_bank["cvc"]} if new else None
    return card, secret


def _sync(card: IssuedCard, at_bank: dict) -> None:
    changed = (card.status, card.spent) != (at_bank["status"], at_bank["spent"])
    card.status, card.spent = at_bank["status"], at_bank["spent"]
    if changed:
        card.save(update_fields=["status", "spent"])


def fetch(card: IssuedCard) -> list[dict] | None:
    """Refresh the card from the bank; its recent authorisations, or None if unreachable."""
    try:
        at_bank = bank.get_card(card.public_id)
    except (bank.BankUnavailable, bank.BankRefused):
        return None
    _sync(card, at_bank)
    return at_bank.get("authorisations", [])


def cancel(card: IssuedCard, *, actor_type: str, actor_id: str, request_id: str = ""
           ) -> IssuedCard:
    try:
        at_bank = bank.cancel_card(card.public_id)
    except (bank.BankUnavailable, bank.BankRefused):
        raise unavailable() from None
    was = card.status
    _sync(card, at_bank)
    if was != IssuedCard.Status.CANCELLED:
        record("issued_card.cancelled", actor_type=actor_type, actor_id=actor_id,
               merchant_id=card.merchant.public_id, target_type="issued_card",
               target_id=card.public_id, request_id=request_id, details={"last4": card.last4})
    return card


def to_dict(card: IssuedCard, secret: dict | None = None,
            authorisations: list[dict] | None = None) -> dict:
    data = {
        "id": card.public_id,
        "object": "issued_card",
        "livemode": card.livemode,
        "description": card.description,
        "brand": "visa",
        "last4": card.last4,
        "expiry_month": card.expiry_month,
        "expiry_year": card.expiry_year,
        "currency": "SGD",
        "spending_limit": card.spending_limit,
        "spent": card.spent,
        "remaining": max(0, card.spending_limit - card.spent),
        "single_use": card.single_use,
        "status": card.status,
        "created_at": rfc3339(card.created_at),
    }
    if secret:
        data.update(secret)
    if authorisations is not None:
        data["authorisations"] = [
            {"amount": a["amount"], "approved": a["response_code"] == "00",
             "response_code": a["response_code"], "response_text": a["response_text"],
             "captured": a.get("captured"), "reversed": a.get("reversed", False),
             "created_at": a["created_at"]}
            for a in authorisations
        ]
    return data
