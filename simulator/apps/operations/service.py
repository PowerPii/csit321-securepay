"""Recording operations under the one-record-per-operation_id rule (SIMULATOR-CONTRACT.md 3, 6)."""
import hashlib
import json
import re
from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.http import rfc3339
from apps.common.ids import new_id
from apps.common.validation import invalid, is_integer, json_object
from apps.ledger.models import post_capture, post_hold, post_refund, post_release
from apps.operations.models import Authorisation, OperationRecord
from apps.operations.test_cards import brand_for, digits_only, luhn_ok

OPERATION_ID = re.compile(r"^op_[0-9A-Za-z]{24}$")
MERCHANT_ID = re.compile(r"^mer_[0-9A-Za-z]{24}$")
PAYMENT_ID = re.compile(r"^pay_[0-9A-Za-z]{24}$")
CARD_NUMBER = re.compile(r"^[0-9]{13,19}$")
CVC = re.compile(r"^[0-9]{3,4}$")
AUTHORISE_FIELDS = {"operation_id", "merchant_id", "payment_id", "amount", "currency", "card"}
CARD_FIELDS = {"number", "expiry_month", "expiry_year", "cvc"}


def to_dict(record: OperationRecord) -> dict:
    """The operation as contracts/schemas/simulator-operation.schema.json describes it."""
    card = ({"brand": record.card_brand, "last4": record.card_last4}
            if record.card_last4 else None)
    authorisation = record.authorisation.to_dict() if record.authorisation_id else None
    return {
        "operation_id": record.operation_id,
        "object": "operation",
        "type": record.type,
        "status": record.status,
        "decline_reason": record.decline_reason or None,
        "merchant_id": record.merchant_id,
        "payment_id": record.payment_id,
        "authorisation_id": record.authorisation_ref,
        "refund_id": record.refund_id,
        "amount": record.amount,
        "currency": record.currency,
        "card": card,
        "simulator_reference": record.simulator_reference,
        "authorisation": authorisation,
        "network": record.network,
        "recorded_at": rfc3339(record.recorded_at),
    }


def parse_authorise(raw: bytes) -> dict:
    body = json_object(raw)
    for name in AUTHORISE_FIELDS:
        if name not in body:
            raise invalid("missing_field", f"'{name}' is required.", name)
    for name in body:
        if name not in AUTHORISE_FIELDS:
            raise invalid("invalid_field", f"Unknown field '{name}'.", name)
    if not isinstance(body["operation_id"], str) or not OPERATION_ID.match(body["operation_id"]):
        raise invalid("invalid_field", "'operation_id' must be op_ + 24 base62 characters.",
                   "operation_id")
    if not isinstance(body["merchant_id"], str) or not MERCHANT_ID.match(body["merchant_id"]):
        raise invalid("invalid_field", "'merchant_id' is not a merchant ID.", "merchant_id")
    if not isinstance(body["payment_id"], str) or not PAYMENT_ID.match(body["payment_id"]):
        raise invalid("invalid_field", "'payment_id' is not a payment ID.", "payment_id")
    if not is_integer(body["amount"]) or not 1 <= body["amount"] <= 1_000_000:
        raise invalid("invalid_field", "'amount' must be 1 to 1000000 cents.", "amount")
    if body["currency"] != "SGD":
        raise invalid("currency_not_supported", "Only SGD is supported.", "currency")
    card = body["card"]
    if not isinstance(card, dict) or set(card) != CARD_FIELDS:
        raise invalid("invalid_field", "'card' needs number, expiry_month, expiry_year and cvc.",
                   "card")
    if not isinstance(card["number"], str) or not CARD_NUMBER.match(card["number"]):
        raise invalid("invalid_field", "'card.number' must be 13 to 19 digits.", "card.number")
    if not luhn_ok(card["number"]):
        raise invalid("invalid_card", "The card number fails the Luhn check.", "card.number")
    if not is_integer(card["expiry_month"]) or not 1 <= card["expiry_month"] <= 12:
        raise invalid("invalid_field", "'card.expiry_month' must be 1 to 12.", "card.expiry_month")
    if not is_integer(card["expiry_year"]) or not 2000 <= card["expiry_year"] <= 2099:
        raise invalid("invalid_field", "'card.expiry_year' must be 2000 to 2099.",
                   "card.expiry_year")
    if not isinstance(card["cvc"], str) or not CVC.match(card["cvc"]):
        raise invalid("invalid_field", "'card.cvc' must be 3 or 4 digits.", "card.cvc")
    return body


def fingerprint(body: dict) -> str:
    """Hash of the request without the card number and security code (section 3)."""
    safe = {k: v for k, v in body.items() if k != "card"}
    card = body.get("card") or {}
    safe["card"] = {"expiry_month": card.get("expiry_month"),
                    "expiry_year": card.get("expiry_year")}
    canonical = json.dumps(safe, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def card_expired(month: int, year: int, today: date | None = None) -> bool:
    today = today or timezone.now().date()
    return (year, month) < (today.year, today.month)


class Existing(Exception):
    """The operation_id already has a record; carries what to reply."""

    def __init__(self, record, conflict_code=None):
        super().__init__(record.operation_id)
        self.record, self.conflict_code = record, conflict_code


def check_existing(operation_id: str, request_fingerprint: str) -> None:
    record = (OperationRecord.objects.select_related("authorisation")
              .filter(operation_id=operation_id).first())
    if record is None:
        return
    if record.status == OperationRecord.Status.NO_RECORD:
        raise Existing(record, "operation_retired")
    if record.request_fingerprint != request_fingerprint:
        raise Existing(record, "operation_id_conflict")
    raise Existing(record)


def record_authorise(body: dict, decision, request_fingerprint: str) -> OperationRecord:
    """Insert the record (and the authorisation and ledger hold if approved) atomically.

    `decision` is the issuer's answer through the network (apps.network.acquirer.Decision).
    Raises Existing if the ID was recorded or retired in the meantime.
    """
    card = body["card"]
    pan = digits_only(card["number"])
    approved, decline_reason = decision.approved, decision.decline_reason

    record = OperationRecord(
        operation_id=body["operation_id"],
        type=OperationRecord.Type.AUTHORISE,
        status=OperationRecord.Status.APPROVED if approved else OperationRecord.Status.DECLINED,
        decline_reason=decline_reason,
        merchant_id=body["merchant_id"],
        payment_id=body["payment_id"],
        amount=body["amount"],
        currency="SGD",
        card_brand=brand_for(pan),
        card_last4=pan[-4:],
        simulator_reference=new_id("sim"),
        request_fingerprint=request_fingerprint,
        network=decision.network,
        recorded_at=timezone.now(),
    )
    try:
        with transaction.atomic():
            if approved:
                record.authorisation = Authorisation.objects.create(
                    operation_id=record.operation_id, merchant_id=record.merchant_id,
                    payment_id=record.payment_id, authorised_amount=record.amount)
            record.save(force_insert=True)
            if approved:
                post_hold(record.operation_id, record.operation_id, record.amount)
    except IntegrityError:
        # A status query retired the ID, or a duplicate won the race. Report what is there.
        check_existing(body["operation_id"], request_fingerprint)
        raise
    return record


def status_query(operation_id: str, op_type: str | None) -> OperationRecord:
    """Return the record for the ID, retiring it as no_record if there is none (section 6)."""
    for _ in range(3):
        record = (OperationRecord.objects.select_related("authorisation")
                  .filter(operation_id=operation_id).first())
        if record is not None:
            return record
        try:
            with transaction.atomic():
                return OperationRecord.objects.create(
                    operation_id=operation_id,
                    type=op_type or OperationRecord.Type.AUTHORISE,
                    status=OperationRecord.Status.NO_RECORD,
                    recorded_at=timezone.now(),
                )
        except IntegrityError:
            continue  # the operation (or another query) committed first; read it
    raise RuntimeError("could not read or retire the operation")


CAPTURE_FIELDS = {"operation_id", "authorisation_id", "amount"}
CANCEL_FIELDS = {"operation_id", "authorisation_id"}
REFUND_FIELDS = {"operation_id", "authorisation_id", "refund_id", "amount"}
REFUND_ID = re.compile(r"^rfd_[0-9A-Za-z]{24}$")


def _parse_object(raw: bytes, fields: set[str]) -> dict:
    body = json_object(raw)
    for name in sorted(fields):
        if name not in body:
            raise invalid("missing_field", f"'{name}' is required.", name)
    for name in body:
        if name not in fields:
            raise invalid("invalid_field", f"Unknown field '{name}'.", name)
    for name in ("operation_id", "authorisation_id"):
        if not isinstance(body[name], str) or not OPERATION_ID.match(body[name]):
            raise invalid("invalid_field", f"'{name}' must be op_ + 24 base62 characters.", name)
    if body["operation_id"] == body["authorisation_id"]:
        raise invalid("invalid_field", "'operation_id' must be a new ID, not the authorisation's.",
                   "operation_id")
    if "amount" in fields and (not is_integer(body["amount"])
                               or not 1 <= body["amount"] <= 1_000_000):
        raise invalid("invalid_field", "'amount' must be 1 to 1000000 cents.", "amount")
    return body


def parse_capture(raw: bytes) -> dict:
    return _parse_object(raw, CAPTURE_FIELDS)


def parse_cancel(raw: bytes) -> dict:
    return _parse_object(raw, CANCEL_FIELDS)


def parse_refund(raw: bytes) -> dict:
    body = _parse_object(raw, REFUND_FIELDS)
    if not isinstance(body["refund_id"], str) or not REFUND_ID.match(body["refund_id"]):
        raise invalid("invalid_field", "'refund_id' must be rfd_ + 24 base62 characters.",
                   "refund_id")
    return body


def record_settlement(body: dict, op_type: str, request_fingerprint: str) -> OperationRecord:
    """Record a capture or cancel against an authorisation (SIMULATOR-CONTRACT.md section 4).

    The authorisation row is locked, so a capture and a cancel at the same time cannot both
    succeed. A rule failure is recorded as a decline (a 200), never an error. Raises Existing
    if the operation_id was recorded or retired in the meantime.
    """
    is_capture = op_type == OperationRecord.Type.CAPTURE
    try:
        with transaction.atomic():
            auth = (Authorisation.objects.select_for_update()
                    .filter(operation_id=body["authorisation_id"]).first())
            decline_reason = ""
            if auth is None:
                decline_reason = "authorisation_not_found"
            elif auth.state != Authorisation.State.OPEN:
                decline_reason = "authorisation_not_open"
            elif is_capture and body["amount"] > auth.authorised_amount:
                decline_reason = "capture_exceeds_authorised"
            approved = not decline_reason
            amount = body["amount"] if is_capture else (auth.authorised_amount if auth else 0)
            record = OperationRecord(
                operation_id=body["operation_id"],
                type=op_type,
                status=(OperationRecord.Status.APPROVED if approved
                        else OperationRecord.Status.DECLINED),
                decline_reason=decline_reason,
                merchant_id=auth.merchant_id if auth else None,
                payment_id=auth.payment_id if auth else None,
                authorisation_ref=body["authorisation_id"],
                authorisation=auth,
                amount=amount,
                currency="SGD",
                simulator_reference=new_id("sim"),
                request_fingerprint=request_fingerprint,
                recorded_at=timezone.now(),
            )
            if approved and is_capture:
                auth.state = Authorisation.State.CAPTURED
                auth.captured_amount = body["amount"]
                auth.save(update_fields=["state", "captured_amount", "updated_at"])
                post_capture(record.operation_id, auth.operation_id, auth.authorised_amount,
                             body["amount"])
            elif approved:
                auth.state = Authorisation.State.CANCELLED
                auth.save(update_fields=["state", "updated_at"])
                post_release(record.operation_id, auth.operation_id, auth.authorised_amount)
            record.save(force_insert=True)
    except IntegrityError:
        check_existing(body["operation_id"], request_fingerprint)
        raise
    return record


def record_refund(body: dict, request_fingerprint: str) -> OperationRecord:
    """Record a refund against a captured authorisation (SIMULATOR-CONTRACT.md section 4).

    The simulator's own check, independent of the gateway's reservation: approved refunds
    plus this amount must stay within the captured amount. The authorisation row is locked,
    so two refunds at once are checked one after the other. A rule failure is a decline.
    """
    try:
        with transaction.atomic():
            auth = (Authorisation.objects.select_for_update()
                    .filter(operation_id=body["authorisation_id"]).first())
            decline_reason = ""
            if auth is None:
                decline_reason = "authorisation_not_found"
            elif auth.state != Authorisation.State.CAPTURED:
                decline_reason = "authorisation_not_captured"
            elif auth.refunded_amount + body["amount"] > auth.captured_amount:
                decline_reason = "refund_exceeds_captured"
            approved = not decline_reason
            record = OperationRecord(
                operation_id=body["operation_id"],
                type=OperationRecord.Type.REFUND,
                status=(OperationRecord.Status.APPROVED if approved
                        else OperationRecord.Status.DECLINED),
                decline_reason=decline_reason,
                merchant_id=auth.merchant_id if auth else None,
                payment_id=auth.payment_id if auth else None,
                refund_id=body["refund_id"],
                authorisation_ref=body["authorisation_id"],
                authorisation=auth,
                amount=body["amount"],
                currency="SGD",
                simulator_reference=new_id("sim"),
                request_fingerprint=request_fingerprint,
                recorded_at=timezone.now(),
            )
            if approved:
                auth.refunded_amount += body["amount"]
                auth.save(update_fields=["refunded_amount", "updated_at"])
                post_refund(record.operation_id, auth.operation_id, body["amount"])
            record.save(force_insert=True)
    except IntegrityError:
        check_existing(body["operation_id"], request_fingerprint)
        raise
    return record
