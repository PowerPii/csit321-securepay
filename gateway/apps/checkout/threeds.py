"""3-D Secure with SecurePay Bank (v2, docs/v2/THREE-DS.md): the bank challenges the shopper.

When screening says Verify, the gateway first asks the card's bank to authenticate the
payment (`/sim/3ds/authenticate`). If the card is enrolled, the bank texts the cardholder a
code and the checkout shows the bank's challenge; the code goes back to the bank through
`challenge`. A right code returns the bank's proof (ECI and CAVV), which the authorisation
carries to the issuer. If the card is not enrolled, or the bank does not answer in time,
the gateway falls back to its own check: the email code, or the shopper's passkey.

Only a reply signed with the simulator secret is believed. The card number is sent once,
read from the vault without taking it (`vault.peek`); the payment's own authorisation
takes it later.
"""
import json
import logging

import httpx
from django.conf import settings

from apps.events import signing
from apps.payments import processor

logger = logging.getLogger(__name__)
TIMEOUT_SECONDS = 2.0  # inside the checkout's submit: a slow bank falls back to the email code
STATUSES = {"not_enrolled", "pending", "authenticated", "failed", "expired"}


class BankUnavailable(Exception):
    pass


def _call(method: str, path: str, **kwargs) -> dict:
    try:
        response = processor._client().request(method, path, timeout=TIMEOUT_SECONDS, **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("3-D Secure call failed: %s", type(exc).__name__)
        raise BankUnavailable from None
    signature = response.headers.get(processor.SIGNATURE_HEADER, "")
    tolerance = settings.GATEWAY_RULES["SIMULATOR_SIGNATURE_TOLERANCE_SECONDS"]
    if response.status_code not in (200, 201) or not signature or not signing.verify(
            response.content, signature, settings.SIMULATOR_SIGNING_SECRET, tolerance):
        logger.warning("3-D Secure gave no usable answer: http_%s", response.status_code)
        raise BankUnavailable
    try:
        data = json.loads(response.content)
    except ValueError:
        raise BankUnavailable from None
    if not isinstance(data, dict) or data.get("status") not in STATUSES:
        raise BankUnavailable
    return data


def authenticate(payment, card) -> dict:
    """The bank's answer: not_enrolled, or a pending challenge. Raises BankUnavailable."""
    data = _call("POST", "/sim/3ds/authenticate", json={
        "payment_id": payment.public_id, "merchant_id": payment.merchant.public_id,
        "amount": payment.settlement_amount,
        "card": {"number": card.number, "expiry_month": card.expiry_month,
                 "expiry_year": card.expiry_year}})
    if data.get("payment_id") != payment.public_id:
        raise BankUnavailable
    return data


def challenge(three_ds_id: str, code: str) -> dict:
    """The cardholder's code, relayed to the bank. Raises BankUnavailable."""
    data = _call("POST", f"/sim/3ds/{three_ds_id}/challenge", json={"code": code})
    if data.get("three_ds_id") != three_ds_id:
        raise BankUnavailable
    return data
