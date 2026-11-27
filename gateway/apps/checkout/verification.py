"""Verification codes: issue, check and email (FR-CHK-06).

The 6-digit code comes from `secrets`, is stored only as an HMAC keyed with the Django
secret and the payment ID, lives for VERIFICATION_CODE_TTL_MINUTES, and is emailed to the
checkout email through the email outbox, in its encrypted body only. The plain code never
reaches a log, an audit row, an email subject or a stored plain-text column.
"""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.checkout.models import VerificationChallenge
from apps.fx.currencies import format_amount
from apps.payments.models import Payment

logger = logging.getLogger(__name__)
RULES = settings.GATEWAY_RULES


def _hash(payment: Payment, code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"verify:{payment.public_id}:{code}".encode(),
                    hashlib.sha256).hexdigest()


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def issue(payment: Payment, challenge: VerificationChallenge | None = None) -> tuple[
        VerificationChallenge, str]:
    """Create the challenge, or replace its code. Caller holds the payment lock."""
    code, now = new_code(), timezone.now()
    expires = now + timedelta(minutes=RULES["VERIFICATION_CODE_TTL_MINUTES"])
    if challenge is None:
        challenge = VerificationChallenge.objects.create(
            payment=payment, code_hash=_hash(payment, code), code_sent_at=now,
            code_expires_at=expires)
    else:
        challenge.code_hash = _hash(payment, code)
        challenge.code_sent_at, challenge.code_expires_at = now, expires
        challenge.codes_sent += 1
        challenge.save()
    return challenge, code


def matches(challenge: VerificationChallenge, code: str) -> bool:
    return hmac.compare_digest(challenge.code_hash, _hash(challenge.payment, code))


def attempts_remaining(challenge: VerificationChallenge) -> int:
    return max(0, RULES["VERIFICATION_MAX_ATTEMPTS"] - challenge.wrong_attempts)


def resend_available_at(challenge: VerificationChallenge):
    if challenge.codes_sent >= RULES["VERIFICATION_MAX_CODES"]:
        return None
    return challenge.code_sent_at + timedelta(
        seconds=RULES["VERIFICATION_RESEND_COOLDOWN_SECONDS"])


def send_code(payment: Payment, code: str) -> None:
    """Queue the code email in the caller's transaction; the worker sends it (one route per
    recipient, apps/common/mail_routing.py) and stops trying once the code has expired. The
    code is only in the encrypted body, which is wiped once sent: never in the subject."""
    from apps.events.emails import queue
    from apps.events.models import OutboundEmail

    minutes = RULES["VERIFICATION_CODE_TTL_MINUTES"]
    amount = format_amount(payment.amount, payment.currency)
    body = (
        f"Your SecurePay verification code is {code}\n\n"
        f"Enter it on the payment page to confirm your payment of {amount} to "
        f"{payment.merchant.name} (reference {payment.reference}). "
        f"It expires in {minutes} minutes.\n\n"
        "If you did not try to pay, ignore this email; nothing will be charged.\n"
        "SecurePay will never ask you for this code by phone or message.\n"
    )
    queue(OutboundEmail.Kind.VERIFICATION_CODE, payment, payment.customer_email,
          "Your SecurePay verification code", body,
          discard_after=timezone.now() + timedelta(minutes=minutes))
