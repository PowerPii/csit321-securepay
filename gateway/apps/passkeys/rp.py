"""The gateway as a WebAuthn relying party (docs/v2/PASSKEYS.md).

Everything the WebAuthn checks need lives here, so the account pages and the checkout ask
the same questions the same way:

* the relying party is the host the pages are served from (`WEBAUTHN_RP_ID`), and only
  `WEBAUTHN_ORIGIN` may answer;
* user verification (fingerprint, face or device PIN) is required every time;
* a challenge is random, lives PASSKEY_CHALLENGE_SECONDS, is stored only as its SHA-256 and
  is used at most once, for the purpose (and payment) it was issued for;
* a signature counter that goes backwards means a cloned authenticator: refused.

Signature and attestation checks are done by the `webauthn` library (py_webauthn).
"""
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import timedelta

import webauthn
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from webauthn.helpers import base64url_to_bytes, options_to_json
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from apps.common.errors import ApiError
from apps.passkeys.models import Passkey, WebAuthnChallenge

logger = logging.getLogger(__name__)
Purpose = WebAuthnChallenge.Purpose
RP_NAME = "SecurePay"
MAX_CREDENTIAL_BYTES = 16_384  # a real answer is about 1 to 2 KB


def _rules():
    return settings.GATEWAY_RULES


def rp_id() -> str:
    return settings.WEBAUTHN_RP_ID


def origin() -> str:
    return settings.WEBAUTHN_ORIGIN


def _hash(challenge: bytes) -> str:
    return hashlib.sha256(challenge).hexdigest()


def passkey_failed(message: str = "We couldn't check that passkey. Try again, or use "
                   "another way.") -> ApiError:
    return ApiError(400, "passkey_failed", message)


def _owner_fields(owner) -> dict:
    """A CustomerAccount or a StaffAccount, as the matching foreign key."""
    from apps.accounts.models import StaffAccount

    if owner is None:
        return {}
    return {"staff": owner} if isinstance(owner, StaffAccount) else {"customer": owner}


def _same_owner(row, owner) -> bool:
    from apps.accounts.models import StaffAccount

    if isinstance(owner, StaffAccount):
        return row.staff_id == owner.pk and row.customer_id is None
    return row.customer_id == owner.pk and row.staff_id is None


def _new_challenge(purpose: str, owner=None, payment=None) -> bytes:
    challenge = webauthn.helpers.generate_challenge()
    WebAuthnChallenge.objects.create(
        challenge_hash=_hash(challenge), purpose=purpose, payment=payment,
        **_owner_fields(owner),
        expires_at=timezone.now() + timedelta(seconds=_rules()["PASSKEY_CHALLENGE_SECONDS"]))
    return challenge


def _descriptor(passkey: Passkey) -> PublicKeyCredentialDescriptor:
    transports = []
    for name in passkey.transports or []:
        try:
            transports.append(AuthenticatorTransport(name))
        except ValueError:
            continue  # a transport this library version does not know
    return PublicKeyCredentialDescriptor(id=bytes(passkey.credential_id),
                                         transports=transports or None)


def _options(options) -> dict:
    return json.loads(options_to_json(options))


def registration_options(owner) -> dict:
    """Options for navigator.credentials.create(): a discoverable passkey with user
    verification, no attestation (we trust the device to keep its key, not its maker)."""
    options = webauthn.generate_registration_options(
        rp_id=rp_id(), rp_name=RP_NAME, user_id=owner.public_id.encode(),
        user_name=owner.email,
        user_display_name=getattr(owner, "full_name", "") or getattr(
            owner, "display_name", "") or owner.email,
        challenge=_new_challenge(Purpose.REGISTER, owner=owner),
        timeout=_rules()["PASSKEY_CHALLENGE_SECONDS"] * 1000,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[_descriptor(p) for p in owner.passkeys.all()])
    return _options(options)


def assertion_options(purpose: str, owner=None, payment=None) -> dict:
    """Options for navigator.credentials.get(). With an owner, only their passkeys are
    offered; without one (shopper sign-in), the browser shows whichever passkeys it has."""
    allow = None
    if owner is not None:
        allow = [_descriptor(p) for p in owner.passkeys.all()]
    options = webauthn.generate_authentication_options(
        rp_id=rp_id(), challenge=_new_challenge(purpose, owner=owner, payment=payment),
        timeout=_rules()["PASSKEY_CHALLENGE_SECONDS"] * 1000, allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED)
    return _options(options)


def _credential(body) -> dict:
    credential = body.get("credential") if isinstance(body, dict) else None
    if not isinstance(credential, dict) or len(json.dumps(credential)) > MAX_CREDENTIAL_BYTES:
        raise ApiError(400, "invalid_field", "Send the passkey answer as 'credential'.",
                       param="credential")
    return credential


def _take_challenge(credential: dict, purpose: str, payment=None) -> tuple[WebAuthnChallenge,
                                                                            bytes]:
    """Find the challenge the answer signed and use it up. Caller is inside a transaction."""
    try:
        client_data = json.loads(base64url_to_bytes(
            credential["response"]["clientDataJSON"]))
        challenge = base64url_to_bytes(client_data["challenge"])
    except (KeyError, TypeError, ValueError):
        raise passkey_failed() from None
    row = (WebAuthnChallenge.objects.select_for_update()
           .filter(challenge_hash=_hash(challenge), purpose=purpose).first())
    now = timezone.now()
    if row is None or row.used_at is not None or now >= row.expires_at:
        raise passkey_failed("That passkey request has expired. Try again.")
    if payment is not None and row.payment_id != payment.pk:
        raise passkey_failed("That passkey request has expired. Try again.")
    row.used_at = now
    row.save(update_fields=["used_at"])
    return row, challenge


@dataclass(frozen=True)
class NewPasskey:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    aaguid: str
    backed_up: bool
    transports: list


def finish_registration(owner, body) -> NewPasskey:
    """Check the answer to registration_options(). Raises passkey_failed."""
    credential = _credential(body)
    with transaction.atomic():
        row, challenge = _take_challenge(credential, Purpose.REGISTER)
    if not _same_owner(row, owner):
        raise passkey_failed("That passkey request has expired. Try again.")
    try:
        verified = webauthn.verify_registration_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=rp_id(),
            expected_origin=origin(), require_user_verification=True)
    except WebAuthnException as exc:
        logger.info("Passkey registration refused for %s: %s", owner.public_id, exc)
        raise passkey_failed() from None
    transports = credential.get("response", {}).get("transports") or []
    if not isinstance(transports, list):
        transports = []
    return NewPasskey(
        credential_id=verified.credential_id, public_key=verified.credential_public_key,
        sign_count=verified.sign_count, aaguid=str(verified.aaguid or ""),
        backed_up=bool(verified.credential_backed_up),
        transports=[str(t)[:20] for t in transports[:8]])


def finish_assertion(body, purpose: str, payment=None) -> tuple[Passkey, WebAuthnChallenge]:
    """Check an answer to assertion_options() and return the passkey that signed it, with
    its counter and last use updated. Raises passkey_failed."""
    credential = _credential(body)
    with transaction.atomic():  # used up even when the answer turns out to be wrong
        row, challenge = _take_challenge(credential, purpose, payment)
    with transaction.atomic():
        try:
            raw_id = base64url_to_bytes(credential.get("rawId") or credential.get("id") or "")
        except (TypeError, ValueError):
            raise passkey_failed() from None
        passkey = (Passkey.objects.select_for_update(of=("self",)).select_related("customer", "staff")
                   .filter(credential_id=raw_id).first())
        if passkey is None or not _matches(row, passkey):
            raise passkey_failed("That passkey isn't registered with this account.")
        try:
            verified = webauthn.verify_authentication_response(
                credential=credential, expected_challenge=challenge, expected_rp_id=rp_id(),
                expected_origin=origin(), credential_public_key=bytes(passkey.public_key),
                credential_current_sign_count=passkey.sign_count,
                require_user_verification=True)
        except WebAuthnException as exc:
            logger.info("Passkey %s refused: %s", passkey.public_id, exc)
            raise passkey_failed() from None
        passkey.sign_count = verified.new_sign_count
        passkey.last_used_at = timezone.now()
        passkey.backed_up = bool(verified.credential_backed_up)
        passkey.save(update_fields=["sign_count", "last_used_at", "backed_up"])
    return passkey, row


def _matches(row: WebAuthnChallenge, passkey: Passkey) -> bool:
    """A challenge made for an owner only takes that owner's passkeys. Shopper sign-in
    names nobody, but takes shopper passkeys only: a staff passkey never signs in a shopper."""
    if row.staff_id is not None:
        return passkey.staff_id == row.staff_id
    if row.customer_id is not None:
        return passkey.customer_id == row.customer_id
    return passkey.customer_id is not None


def to_dict(passkey: Passkey) -> dict:
    from apps.common.timefmt import rfc3339

    return {"id": passkey.public_id, "object": "passkey", "name": passkey.name,
            "synced": passkey.backed_up, "created_at": rfc3339(passkey.created_at),
            "last_used_at": rfc3339(passkey.last_used_at)}

