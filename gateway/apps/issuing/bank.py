"""SecurePay Bank's card-management API, behind the simulator's /sim/issuing.

Uses the gateway's one simulator client and key. Only a reply signed with the simulator
secret is believed. Anything else (timeout, error, bad signature) is BankUnavailable, which
the API answers as 503 issuer_unavailable: no card is made on our side without the bank.
"""
import json
import logging

import httpx
from django.conf import settings

from apps.events import signing
from apps.payments import processor

logger = logging.getLogger(__name__)


class BankUnavailable(Exception):
    pass


class BankRefused(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def _call(method: str, path: str, **kwargs) -> tuple[int, dict]:
    try:
        response = processor._client().request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("Issuing bank call failed: %s", type(exc).__name__)
        raise BankUnavailable from None
    signature = response.headers.get(processor.SIGNATURE_HEADER, "")
    tolerance = settings.GATEWAY_RULES["SIMULATOR_SIGNATURE_TOLERANCE_SECONDS"]
    if response.status_code in (400, 404, 409):
        try:
            code = json.loads(response.content)["error"]["code"]
        except (ValueError, KeyError, TypeError):
            code = "bank_refused"
        raise BankRefused(response.status_code, code)
    if response.status_code not in (200, 201) or not signature or not signing.verify(
            response.content, signature, settings.SIMULATOR_SIGNING_SECRET, tolerance):
        logger.warning("Issuing bank gave no usable answer: http_%s", response.status_code)
        raise BankUnavailable
    try:
        return response.status_code, json.loads(response.content)
    except ValueError:
        raise BankUnavailable from None


def create_card(*, card_id: str, merchant_id: str, spending_limit: int, single_use: bool
                ) -> tuple[bool, dict]:
    """(new, card). Only a new card carries `number` and `cvc`."""
    status, data = _call("POST", "/sim/issuing/cards", json={
        "card_id": card_id, "merchant_id": merchant_id, "spending_limit": spending_limit,
        "single_use": single_use})
    if data.get("card_id") != card_id:
        raise BankUnavailable
    return status == 201, data


def get_card(card_id: str) -> dict:
    return _check(card_id, _call("GET", f"/sim/issuing/cards/{card_id}")[1])


def cancel_card(card_id: str) -> dict:
    return _check(card_id, _call("POST", f"/sim/issuing/cards/{card_id}/cancel", json={})[1])


def _check(card_id: str, data: dict) -> dict:
    if data.get("card_id") != card_id:
        raise BankUnavailable
    return data
