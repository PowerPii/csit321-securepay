"""Hosted checkout flow (UC-01, screens C3 to C7).

The checkout session ID in the URL is the only thing the browser holds. Merchant, amount
and currency come from the payment the merchant created and can never change here
(FR-CHK-03). Card details go straight to the vault; this module only sees the summary,
apart from handing the vault's one-time card to the simulator client on submit.
"""
import re
from email.utils import parseaddr
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.audit.models import AuditEvent
from apps.audit.models import record as audit
from apps.checkout import threeds, verification
from apps.checkout.models import VerificationChallenge
from apps.common.errors import ApiError, invalid, not_found
from apps.common.timefmt import rfc3339
from apps.customers.service import link_payment
from apps.events.models import EventType
from apps.events.outbox import record_payment_event
from apps.passkeys import rp as passkey_rp
from apps.passkeys.models import Passkey
from apps.passkeys.models import WebAuthnChallenge as _Challenge
from apps.payments import processor
from apps.payments.models import Operation, OutcomeReason, Payment
from apps.payments.processing import apply_authorise_answer
from apps.payments.states import PaymentStatus, check_transition
from apps.risk.alerts import raise_fraud_alert
from apps.risk.models import ScreeningOutcome
from apps.risk.screening import screen_payment
from apps.vault import service as vault

SESSION_ID = re.compile(r"^cs_[0-9A-Za-z]{24}$")
CARD_FIELDS = {"number", "expiry_month", "expiry_year", "cvc", "email", "privacy_consent"}

# What the customer is told (C5 to C7). Never a fraud reason (FR-MER-07 is for staff only);
# a block for "screening unavailable" is shown as "unavailable" so the customer retries.
RESULTS = {
    PaymentStatus.CREATED: "open",
    PaymentStatus.SCREENING: "processing",
    PaymentStatus.PROCESSING: "processing",
    PaymentStatus.VERIFICATION_REQUIRED: "verification_required",
    PaymentStatus.AUTHORISED: "approved",
    PaymentStatus.CAPTURED: "approved",
    PaymentStatus.DECLINED: "declined",
    PaymentStatus.BLOCKED: "not_completed",
    PaymentStatus.UNRESOLVED: "pending",
    PaymentStatus.CANCELLED: "cancelled",
    PaymentStatus.EXPIRED: "expired",
}


def _actor(payment):
    return {"actor_type": AuditEvent.ActorType.CUSTOMER, "actor_id": payment.checkout_session_id,
            "merchant_id": payment.merchant.public_id, "target_type": "payment",
            "target_id": payment.public_id}


def load(session_id: str) -> Payment:
    if not SESSION_ID.match(session_id or ""):
        raise not_found()
    payment = (Payment.objects.select_related("merchant")
               .filter(checkout_session_id=session_id).first())
    if payment is None:
        raise not_found()
    return expire_if_due(payment)


def expire_if_due(payment: Payment, request_id: str = "") -> Payment:
    """End a checkout session that passed its hour (FR-CHK-16, FR-CHK-18).

    Not submitted: payment.expired. Still waiting for a verification code: Blocked with
    verification_expired. The worker also does this on a schedule (apps.payments.jobs); doing
    it on access means the page never offers a session that has ended.
    """
    waiting = (PaymentStatus.CREATED, PaymentStatus.VERIFICATION_REQUIRED)
    if payment.status not in waiting or timezone.now() < payment.checkout_expires_at:
        return payment
    with transaction.atomic():
        payment = Payment.objects.select_for_update(of=("self",)).select_related("merchant").get(
            pk=payment.pk)
        if payment.status == PaymentStatus.VERIFICATION_REQUIRED:
            return _end_verification_if_due(payment, request_id)
        if payment.status == PaymentStatus.CREATED and \
                timezone.now() >= payment.checkout_expires_at:
            check_transition(PaymentStatus.CREATED, PaymentStatus.EXPIRED)
            vault.discard(payment.card_token)
            payment.status = PaymentStatus.EXPIRED
            payment.outcome_reason = OutcomeReason.CHECKOUT_EXPIRED
            record_payment_event(payment, EventType.PAYMENT_EXPIRED)
            audit("payment.expired", **{**_actor(payment), "actor_type":
                                        AuditEvent.ActorType.SYSTEM, "actor_id": "checkout"},
                  request_id=request_id)
    return payment


def _session_closed(payment: Payment) -> ApiError:
    if payment.status == PaymentStatus.EXPIRED:
        return ApiError(410, "checkout_expired", "This checkout session has ended. Return to "
                        "the shop to start again.")
    return ApiError(409, "payment_already_submitted", "This payment was already submitted.")


def mask_email(email: str) -> str | None:
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    return f"{local[0]}•••@{domain}"


def return_url_for(payment: Payment) -> str:
    """`return_url?payment_id=pay_...` (API-CONTRACT.md section 3). Navigation only."""
    parts = urlsplit(payment.return_url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k != "payment_id"]
    query.append(("payment_id", payment.public_id))
    return urlunsplit(parts._replace(query=urlencode(query)))


def to_dict(payment: Payment) -> dict:
    """What the hosted checkout page may know about its own payment."""
    status = PaymentStatus(payment.status)
    submitted = status not in (PaymentStatus.CREATED, PaymentStatus.EXPIRED)
    card = ({"brand": payment.card_brand, "last4": payment.card_last4}
            if payment.card_last4 else None)
    result = RESULTS[status]
    if payment.outcome_reason == OutcomeReason.SCREENING_UNAVAILABLE:
        result = "unavailable"  # a technical stop, not a fraud finding: "try again later"
    elif payment.outcome_reason == OutcomeReason.NOT_PROCESSED:
        result = "not_completed"  # the bank never got it: not a decline, nothing was taken
    elif payment.outcome_reason in (OutcomeReason.VERIFICATION_FAILED,
                                    OutcomeReason.VERIFICATION_EXPIRED):
        # The customer already knows the code was not confirmed; saying so is clearer than
        # "couldn't complete" and tells them nothing about fraud scores or rules.
        result = "verification_failed"
    return {
        "session_id": payment.checkout_session_id,
        "status": status.value,
        "result": result,
        "merchant": {"name": payment.merchant.name},
        "livemode": payment.livemode,
        "amount": payment.amount,
        "currency": payment.currency,
        "order_reference": payment.order_reference,
        "description": payment.description or None,
        "line_items": payment.line_items or None,
        "reference": payment.reference if submitted else None,
        "email_required": not payment.customer_email,
        "customer_email_masked": mask_email(payment.customer_email),
        "card": card,
        "expires_at": rfc3339(payment.checkout_expires_at),
        "submitted_at": rfc3339(payment.submitted_at),
        "authorised_at": rfc3339(payment.authorised_at),
        "return_url": return_url_for(payment),
        "verification": verification_to_dict(payment),
    }


def _clean_email(value) -> str:
    if not isinstance(value, str) or len(value) > 254 or parseaddr(value)[1] != value:
        raise invalid("invalid_email", "Enter a valid email address.", param="email")
    try:
        EmailValidator()(value)
    except ValidationError:
        raise invalid("invalid_email", "Enter a valid email address.", param="email") from None
    return value


def attach_card(payment: Payment, body: dict, request_id: str) -> Payment:
    """Card entry (C3): vault the card, keep only token, brand, last four and fingerprint."""
    if payment.status != PaymentStatus.CREATED:
        raise _session_closed(payment)
    for name in body:
        if name not in CARD_FIELDS:
            raise invalid("invalid_field", f"Unknown field '{name}'.", param=name)
    # FR-ACC-12: the page shows the privacy notice; nothing is collected without consent.
    if body.get("privacy_consent") is not True:
        raise invalid("privacy_consent_required", "Agree to the privacy notice to pay.",
                      param="privacy_consent")
    email = None
    if not payment.customer_email:
        email = _clean_email(body.get("email"))
    try:
        summary = vault.tokenise(body.get("number"), body.get("expiry_month"),
                                 body.get("expiry_year"), body.get("cvc"),
                                 expires_at=payment.checkout_expires_at)
    except vault.CardRejected as exc:
        raise invalid(exc.code, exc.message, param=exc.field) from None

    with transaction.atomic():
        locked = Payment.objects.select_for_update(of=("self",)).select_related("merchant").get(
            pk=payment.pk)
        if locked.status != PaymentStatus.CREATED:
            vault.discard(summary.token)
            raise _session_closed(locked)
        previous = locked.card_token
        locked.card_token = summary.token
        locked.card_brand = summary.brand
        locked.card_last4 = summary.last4
        locked.card_fingerprint = summary.fingerprint
        locked.card_bin = summary.bin
        if email:
            locked.customer_email = email
        locked.privacy_notice_version = settings.GATEWAY_RULES["PRIVACY_NOTICE_VERSION"]
        locked.save()
        audit("checkout.card_added", **_actor(locked), request_id=request_id,
              details={"brand": summary.brand, "last4": summary.last4})
    vault.discard(previous)
    return locked


def submit(payment: Payment, request_id: str, ip: str | None = None,
           customer_token: str | None = None) -> Payment:
    """Pay (C3 -> C4): screen, then allow (simulator), ask for a code (C5) or block (C7).

    Submitting twice is harmless: the second call returns the payment as it is. A customer
    signed in on this browser (`customer_token`, the sp_customer cookie) gets the payment in
    their history (FR-ACC-04).
    """
    operation = card = None
    with transaction.atomic():
        # of=("self",): lock the payment row only. Without it Postgres also locked the joined
        # merchant row, so every checkout of one shop queued behind the one being screened:
        # about 17 payments/s at most whatever the number of workers (checkout profile after
        # the Codex release review, 18 Nov 2026). The same applies to every lock in this file.
        payment = Payment.objects.select_for_update(of=("self",)).select_related("merchant").get(
            pk=payment.pk)
        if payment.status == PaymentStatus.EXPIRED:
            raise _session_closed(payment)
        if payment.status != PaymentStatus.CREATED:
            return payment  # a double click or a reload; the first submission owns it
        if not payment.card_token:
            raise invalid("card_required", "Enter your card details first.")
        if not payment.customer_email:
            raise invalid("invalid_email", "Enter a valid email address.", param="email")
        if not vault.has(payment.card_token):  # session ended or token purged: ask again
            payment.card_token = payment.card_brand = payment.card_last4 = ""  # nosec B105
            payment.card_fingerprint = ""
            payment.save()
            card_lost = True
        else:
            card_lost = False
            if customer_token:
                link_payment(payment, customer_token)
                if payment.customer_id:
                    Payment.objects.filter(pk=payment.pk).update(customer=payment.customer)
            operation, card = _screen_and_act(payment, request_id, ip)
    if card_lost:
        raise invalid("card_required", "Please enter your card details again.")
    if operation is None:
        return payment  # verification required, or blocked
    return _authorise(operation, payment, card, request_id)


def _screen_and_act(payment: Payment, request_id: str, ip: str | None):
    """created -> screening -> processing | verification_required | blocked (FR-CHK-04)."""
    check_transition(PaymentStatus.CREATED, PaymentStatus.SCREENING)
    payment.status = PaymentStatus.SCREENING
    payment.submitted_at = timezone.now()
    screening = screen_payment(payment, ip=ip)
    payment.screening_decision = screening.outcome.value
    raise_fraud_alert(screening.event)  # Block or Verify only (FR-FRD-08)
    details = _screening_details(screening)
    if screening.outcome == ScreeningOutcome.ALLOW:
        card = vault.take(payment.card_token)  # deleted from the vault from here on
        if card is None:  # purged between the check and now: undo everything, ask again
            raise invalid("card_required", "Please enter your card details again.")
        return _start_processing(payment, PaymentStatus.SCREENING, request_id, details), card
    if screening.outcome == ScreeningOutcome.VERIFY:
        _ask_for_code(payment, request_id, details)
    else:
        reason = (OutcomeReason.SCREENING_UNAVAILABLE if screening.decision is None
                  else OutcomeReason(screening.decision.block_reason))
        _block(payment, reason, request_id, details)
    return None, None


def _authorise(operation: Operation, payment: Payment, card, request_id: str,
               authentication: dict | None = None) -> Payment:
    try:
        answer = processor.authorise(operation, payment, card, authentication)
    finally:
        del card
    return apply_authorise_answer(operation.pk, answer, request_id)


def _screening_details(screening) -> dict:
    """What the audit trail keeps about a decision (no scores: those stay in the record)."""
    event = screening.event
    return {"screening": screening.outcome.value, "screening_id": event.public_id,
            "reasons": event.reasons, "model_version": event.model_version or None,
            "policy_version": event.policy_version or None,
            "rules_version": event.rules_version or None}


def _start_processing(payment: Payment, current: PaymentStatus, request_id: str,
                      details: dict) -> Operation:
    """screening|verification_required -> processing, and the operation row, in the caller's
    transaction (committed before the simulator call, SIMULATOR-CONTRACT.md 3.1)."""
    check_transition(current, PaymentStatus.PROCESSING)
    payment.status = PaymentStatus.PROCESSING
    record_payment_event(payment, EventType.PAYMENT_PROCESSING)
    operation = Operation.objects.create(payment=payment, type=Operation.Type.AUTHORISE,
                                         amount=payment.amount,
                                         settlement_amount=payment.settlement_amount)
    audit("payment.submitted", **_actor(payment), request_id=request_id,
          details={**details, "operation_id": operation.public_id})
    return operation


def _ask_for_code(payment: Payment, request_id: str, details: dict) -> None:
    check_transition(PaymentStatus.SCREENING, PaymentStatus.VERIFICATION_REQUIRED)
    payment.status = PaymentStatus.VERIFICATION_REQUIRED
    record_payment_event(payment, EventType.PAYMENT_VERIFICATION_REQUIRED)
    # v2 3-D Secure: the card's bank challenges the shopper if the card is enrolled
    # (docs/v2/THREE-DS.md). Otherwise, or if the bank does not answer, our own check.
    issuer, three_ds = _issuer_challenge(payment)
    if issuer is not None:
        audit("payment.verification_required", **_actor(payment), request_id=request_id,
              details={**details, "method": "three_ds", "three_ds_id": issuer.three_ds_id})
        return
    # v2: a shopper signed in with a passkey approves with it, and the email code is only
    # sent if they ask for it (docs/v2/PASSKEYS.md).
    by_passkey = passkey_available(payment)
    _, code = verification.issue(payment, send=not by_passkey)
    audit("payment.verification_required", **_actor(payment), request_id=request_id,
          details={**details, "method": "passkey" if by_passkey else "email_code",
                   "three_ds": three_ds})
    if not by_passkey:
        verification.send_code(payment, code)  # queued in this transaction


def _issuer_challenge(payment: Payment) -> tuple[VerificationChallenge | None, str]:
    """(challenge, outcome): ask the card's bank to authenticate the payment (3-D Secure).

    The outcome is `challenge`, `not_enrolled` or `unavailable`; only a challenge makes a
    VerificationChallenge here. Caller holds the payment lock."""
    card = vault.peek(payment.card_token)
    if card is None:
        return None, "unavailable"
    try:
        answer = threeds.authenticate(payment, card)
    except threeds.BankUnavailable:
        return None, "unavailable"
    finally:
        del card
    three_ds_id = answer.get("three_ds_id")
    if answer["status"] == "not_enrolled":
        return None, "not_enrolled"
    if answer["status"] != "pending" or not isinstance(three_ds_id, str) or \
            not THREE_DS_ID.match(three_ds_id):
        return None, "unavailable"
    now = timezone.now()
    expires = parse_datetime(str(answer.get("expires_at") or "")) or now
    return VerificationChallenge.objects.create(
        payment=payment, code_hash="", code_sent_at=now,
        code_expires_at=min(expires, payment.checkout_expires_at), codes_sent=0,
        three_ds_id=three_ds_id, three_ds_status="pending",
        three_ds_phone_hint=str(answer.get("phone_hint") or "")[:16],
        three_ds_test_code=str(answer.get("test_code") or "")[:6]), "challenge"


def _block(payment: Payment, reason: OutcomeReason, request_id: str, details: dict) -> None:
    """Blocked before any financial effect; the card leaves the vault once committed."""
    check_transition(PaymentStatus(payment.status), PaymentStatus.BLOCKED)
    payment.status = PaymentStatus.BLOCKED
    payment.outcome_reason = reason
    record_payment_event(payment, EventType.PAYMENT_BLOCKED)
    audit("payment.blocked", **_actor(payment), request_id=request_id,
          details={**details, "outcome_reason": reason.value})
    token = payment.card_token
    transaction.on_commit(lambda: vault.discard(token))


# --- simulated verification (C5) -------------------------------------------------------------

CODE = re.compile(r"^[0-9]{6}$")
THREE_DS_ID = re.compile(r"^3ds_[0-9A-Za-z]{24}$")
CAVV = re.compile(r"^[A-Za-z0-9+/=]{28}$")


def _locked_challenge(payment: Payment) -> VerificationChallenge:
    return (VerificationChallenge.objects.select_for_update().select_related("payment")
            .get(payment=payment))


def verify(payment: Payment, body: dict, request_id: str) -> Payment:
    """POST {code}. Right code: on to the simulator. Wrong: 400 code_incorrect, and the
    third wrong code blocks the attempt (FR-CHK-18). Expired: 400 code_expired."""
    code = body.get("code")
    if set(body) - {"code"}:
        raise invalid("invalid_field", "Only 'code' may be sent.")
    if not isinstance(code, str) or not CODE.match(code.strip()):
        raise invalid("invalid_code", "Enter the 6-digit code from the email.", param="code")
    code = code.strip()
    error = operation = card = authentication = None
    with transaction.atomic():
        payment = Payment.objects.select_for_update(of=("self",)).select_related("merchant").get(
            pk=payment.pk)
        payment = _end_verification_if_due(payment, request_id)
        if payment.status != PaymentStatus.VERIFICATION_REQUIRED:
            return payment  # already verified, blocked or ended: show it as it is
        challenge = _locked_challenge(payment)
        if challenge.three_ds_id:  # v2: the code is the bank's, so the bank checks it
            error, operation, card, authentication = _verify_with_bank(
                payment, challenge, code, request_id)
        elif challenge.codes_sent == 0:
            raise invalid("code_not_sent", "Approve with your passkey, or ask for an email "
                          "code first.", param="code")
        elif timezone.now() >= challenge.code_expires_at:
            raise ApiError(400, "code_expired", "This code has expired. Ask for a new code.",
                           param="code")
        elif not verification.matches(challenge, code):
            challenge.wrong_attempts += 1
            challenge.save(update_fields=["wrong_attempts"])
            left = verification.attempts_remaining(challenge)
            audit("payment.verification_failed_attempt", **_actor(payment),
                  request_id=request_id, details={"attempts_remaining": left})
            if left == 0:
                _block(payment, OutcomeReason.VERIFICATION_FAILED, request_id,
                       {"wrong_codes": challenge.wrong_attempts})
            else:
                error = ApiError(400, "code_incorrect",
                                 f"That code isn't right. {left} attempt"
                                 f"{'s' if left != 1 else ''} left.", param="code")
        else:
            operation, card = _verified(payment, challenge, request_id, {})
    if error is not None:
        raise error
    if operation is None:
        return payment
    return _authorise(operation, payment, card, request_id, authentication)


def _verify_with_bank(payment: Payment, challenge: VerificationChallenge, code: str,
                      request_id: str):
    """(error, operation, card, authentication) once the bank has checked the code.

    Right code: the bank's proof goes with the authorisation. Wrong: attempts left, as the
    bank counts them; the bank fails the challenge on the third, and the payment is blocked.
    Caller holds the payment lock."""
    try:
        answer = threeds.challenge(challenge.three_ds_id, code)
    except threeds.BankUnavailable:
        return (ApiError(503, "issuer_unavailable", "Your bank did not answer. Enter the "
                         "code again.", param="code", retry_after=2), None, None, None)
    status = answer["status"]
    left = answer.get("attempts_remaining")
    if isinstance(left, int) and not isinstance(left, bool):
        maximum = settings.GATEWAY_RULES["VERIFICATION_MAX_ATTEMPTS"]
        challenge.wrong_attempts = max(challenge.wrong_attempts, maximum - max(0, left))
    challenge.three_ds_status = status
    if status == "authenticated":
        eci, cavv = answer.get("eci"), answer.get("cavv")
        if not (isinstance(eci, str) and re.fullmatch(r"[0-9]{2}", eci)
                and isinstance(cavv, str) and CAVV.match(cavv)):
            return (ApiError(503, "issuer_unavailable", "Your bank did not answer. Enter the "
                             "code again.", param="code", retry_after=2), None, None, None)
        challenge.three_ds_eci, challenge.three_ds_cavv = eci, cavv
        challenge.three_ds_test_code = ""
        challenge.save(update_fields=["wrong_attempts", "three_ds_status", "three_ds_eci",
                                      "three_ds_cavv", "three_ds_test_code"])
        operation, card = _verified(payment, challenge, request_id,
                                    {"method": "three_ds", "three_ds_id": challenge.three_ds_id,
                                     "eci": eci})
        return None, operation, card, {"three_ds_id": challenge.three_ds_id, "eci": eci,
                                       "cavv": cavv}
    challenge.three_ds_test_code = "" if status != "pending" else challenge.three_ds_test_code
    challenge.save(update_fields=["wrong_attempts", "three_ds_status", "three_ds_test_code"])
    if status == "expired":
        _block(payment, OutcomeReason.VERIFICATION_EXPIRED, request_id, {"method": "three_ds"})
        return None, None, None, None
    left = verification.attempts_remaining(challenge)
    audit("payment.verification_failed_attempt", **_actor(payment), request_id=request_id,
          details={"attempts_remaining": left, "method": "three_ds"})
    if status == "failed" or left == 0:
        _block(payment, OutcomeReason.VERIFICATION_FAILED, request_id,
               {"wrong_codes": challenge.wrong_attempts, "method": "three_ds"})
        return None, None, None, None
    return (ApiError(400, "code_incorrect", f"That code isn't right. {left} attempt"
                     f"{'s' if left != 1 else ''} left.", param="code"), None, None, None)


def _verified(payment: Payment, challenge: VerificationChallenge, request_id: str,
              details: dict):
    """The shopper proved it is them: on to the simulator. Caller holds the payment lock."""
    challenge.verified_at = timezone.now()
    challenge.save(update_fields=["verified_at"])
    card = vault.take(payment.card_token)
    if card is None:  # only if the session ended in the meantime
        _block(payment, OutcomeReason.VERIFICATION_EXPIRED, request_id, {})
        return None, None
    audit("payment.verified", **_actor(payment), request_id=request_id, details=details)
    operation = _start_processing(payment, PaymentStatus.VERIFICATION_REQUIRED, request_id,
                                  {"verified": True, **details})
    return operation, card


PasskeyPurpose = _Challenge.Purpose


# --- v2: approve with a passkey instead of the email code (docs/v2/PASSKEYS.md) ----------

def passkey_available(payment: Payment) -> bool:
    """The payment belongs to a signed-in shopper who has at least one passkey."""
    return payment.customer_id is not None and Passkey.objects.filter(
        customer_id=payment.customer_id).exists()


def _passkey_customer(payment: Payment, customer_token: str | None):
    """The passkey must belong to the account the payment was made from, and that account
    must still be signed in on this browser."""
    from apps.customers.service import current_session

    if VerificationChallenge.objects.filter(payment=payment).exclude(three_ds_id="").exists():
        raise ApiError(409, "passkey_not_available", "Enter the code your bank sent to "
                       "approve this payment.")
    session = current_session(customer_token)
    if (not passkey_available(payment) or session is None
            or session.customer_id != payment.customer_id):
        raise ApiError(409, "passkey_not_available", "Sign in to your account on this "
                       "device to approve with a passkey, or use the email code.")
    return session.customer


def passkey_options(payment: Payment, customer_token: str | None) -> dict:
    with transaction.atomic():
        payment = Payment.objects.select_for_update(of=("self",)).get(pk=payment.pk)
        payment = _end_verification_if_due(payment)
        if payment.status != PaymentStatus.VERIFICATION_REQUIRED:
            raise ApiError(409, "verification_not_required", "This payment is no longer "
                           "waiting for approval.")
        customer = _passkey_customer(payment, customer_token)
        options = passkey_rp.assertion_options(PasskeyPurpose.APPROVE_PAYMENT,
                                               owner=customer, payment=payment)
    return {"options": options}


def verify_passkey(payment: Payment, body: dict, request_id: str,
                   customer_token: str | None) -> Payment:
    """POST {credential}. A good passkey answer approves the payment like a right code; a
    bad one counts as a wrong attempt (FR-CHK-18 still blocks after the third)."""
    if not isinstance(body, dict) or set(body) - {"credential"}:
        raise invalid("invalid_field", "Only 'credential' may be sent.")
    with transaction.atomic():
        locked = Payment.objects.select_for_update(of=("self",)).get(pk=payment.pk)
        locked = _end_verification_if_due(locked, request_id)
        if locked.status != PaymentStatus.VERIFICATION_REQUIRED:
            return locked
        _passkey_customer(locked, customer_token)
    error = operation = card = None
    try:
        passkey, _ = passkey_rp.finish_assertion(body, PasskeyPurpose.APPROVE_PAYMENT,
                                                 payment=payment)
    except ApiError as exc:
        error, passkey = exc, None
    with transaction.atomic():
        payment = Payment.objects.select_for_update(of=("self",)).select_related("merchant").get(
            pk=payment.pk)
        payment = _end_verification_if_due(payment, request_id)
        if payment.status != PaymentStatus.VERIFICATION_REQUIRED:
            return payment
        challenge = _locked_challenge(payment)
        if error is not None or passkey.customer_id != payment.customer_id:
            challenge.wrong_attempts += 1
            challenge.save(update_fields=["wrong_attempts"])
            left = verification.attempts_remaining(challenge)
            audit("payment.verification_failed_attempt", **_actor(payment),
                  request_id=request_id, details={"attempts_remaining": left,
                                                  "method": "passkey"})
            if left == 0:
                _block(payment, OutcomeReason.VERIFICATION_FAILED, request_id,
                       {"wrong_codes": challenge.wrong_attempts, "method": "passkey"})
                return payment
            error = error or passkey_rp.passkey_failed()
        else:
            operation, card = _verified(payment, challenge, request_id,
                                        {"method": "passkey", "passkey_id": passkey.public_id})
    if error is not None:
        raise error  # after the commit, so the wrong attempt is counted
    if operation is None:
        return payment
    return _authorise(operation, payment, card, request_id)


def resend(payment: Payment, request_id: str) -> Payment:
    """A new code that replaces the old one (FR-CHK-06), with a short cooldown."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update(of=("self",)).select_related("merchant").get(
            pk=payment.pk)
        payment = _end_verification_if_due(payment, request_id)
        if payment.status != PaymentStatus.VERIFICATION_REQUIRED:
            return payment
        challenge = _locked_challenge(payment)
        if challenge.three_ds_id:
            raise ApiError(409, "code_from_bank", "Your bank sent this code by text message. "
                           "Enter it to continue.")
        available = verification.resend_available_at(challenge)
        if available is None:
            raise ApiError(429, "too_many_codes", "You've asked for the most codes we can send "
                           "for this payment. Use the latest code or start again from the shop.")
        now = timezone.now()
        if now < available:
            wait = max(1, int((available - now).total_seconds() + 0.999))
            raise ApiError(429, "resend_too_soon", f"Please wait {wait} seconds before asking "
                           "for another code.", retry_after=wait)
        _, code = verification.issue(payment, challenge)
        audit("payment.verification_code_resent", **_actor(payment), request_id=request_id,
              details={"codes_sent": challenge.codes_sent})
        verification.send_code(payment, code)  # queued in this transaction
    return payment


def _end_verification_if_due(payment: Payment, request_id: str = "") -> Payment:
    """The checkout session ended before a correct code: Blocked (FR-CHK-18).

    Caller holds the payment lock."""
    if payment.status == PaymentStatus.VERIFICATION_REQUIRED and \
            timezone.now() >= payment.checkout_expires_at:
        _block(payment, OutcomeReason.VERIFICATION_EXPIRED, request_id, {"session_ended": True})
    return payment


def verification_to_dict(payment: Payment) -> dict | None:
    if payment.status != PaymentStatus.VERIFICATION_REQUIRED:
        return None
    challenge = VerificationChallenge.objects.filter(payment=payment).first()
    if challenge is None:
        return None
    if challenge.three_ds_id:  # v2: the bank's challenge (docs/v2/THREE-DS.md)
        return {
            "method": "three_ds", "passkey": False, "code_sent": True, "sent_to": None,
            "code_expires_at": rfc3339(challenge.code_expires_at),
            "attempts_remaining": verification.attempts_remaining(challenge),
            "resend_available_at": None,
            "issuer_challenge": {
                "bank": "SecurePay Bank", "card_last4": payment.card_last4,
                "phone_hint": challenge.three_ds_phone_hint,
                # No phone in the simulator: the checkout shows the bank's text message.
                "simulated_text_code": challenge.three_ds_test_code or None,
            },
        }
    by_passkey = passkey_available(payment)
    return {
        "method": "passkey" if by_passkey and challenge.codes_sent == 0 else "email_code",
        "issuer_challenge": None,
        "passkey": by_passkey,  # v2: approve with a passkey instead
        "code_sent": challenge.codes_sent > 0,
        "sent_to": mask_email(payment.customer_email) if challenge.codes_sent else None,
        "code_expires_at": rfc3339(challenge.code_expires_at),
        "attempts_remaining": verification.attempts_remaining(challenge),
        "resend_available_at": rfc3339(verification.resend_available_at(challenge)),
    }
