"""HTTP client for the processing simulator (contracts/SIMULATOR-CONTRACT.md).

Only a signed reply is evidence (section 7). Everything else (timeout, connection error,
5xx, 400, 401, a bad or missing signature, a record for another operation) is "no answer",
and the operation becomes Unresolved for reconciliation to settle with the same ID.
"""
import json
import logging
import threading
from dataclasses import dataclass
from enum import StrEnum

import httpx
from django.conf import settings

from apps.events import signing
from apps.payments.models import Operation, Payment

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "Simulator-Signature"
# Tests replace this with an httpx.MockTransport; None means real network calls.
TRANSPORT: httpx.BaseTransport | None = None


class AnswerKind(StrEnum):
    RECORDED = "recorded"  # signed 200: the operation record
    CONFLICT = "conflict"  # signed 409: operation_retired / operation_id_conflict + record
    NO_ANSWER = "no_answer"


@dataclass(frozen=True)
class Answer:
    kind: AnswerKind
    record: dict | None = None
    conflict_code: str | None = None
    raw_body: str = ""
    signature: str = ""
    problem: str = ""  # why there is no answer, for logs and audit (never card data)


_clients: dict[tuple, httpx.Client] = {}
_clients_lock = threading.Lock()


def _client() -> httpx.Client:
    """One long-lived client per process (and per settings), shared by its threads.

    A new client per call built a fresh TLS context (loading the CA store) and opened a new
    connection to the simulator for every payment: about a quarter of the submit time in the
    checkout profile at 20 payments/s (Codex release review, 18 Nov 2026). The shared client
    keeps connections open between calls. A connection that drops mid-call is a lost reply,
    which reconciliation already resolves (contract section 6).
    """
    rules = settings.GATEWAY_RULES
    key = (settings.SIMULATOR_URL, settings.SIMULATOR_CA_BUNDLE, settings.SIMULATOR_API_KEY,
           rules["SIMULATOR_TIMEOUT_SECONDS"], rules["SIMULATOR_CONNECT_TIMEOUT_SECONDS"],
           TRANSPORT)
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            if len(_clients) >= 8:  # settings changed (tests): drop the old clients
                for old in _clients.values():
                    old.close()
                _clients.clear()
            timeout = httpx.Timeout(rules["SIMULATOR_TIMEOUT_SECONDS"],
                                    connect=rules["SIMULATOR_CONNECT_TIMEOUT_SECONDS"])
            client = _clients[key] = httpx.Client(
                base_url=settings.SIMULATOR_URL,
                timeout=timeout,
                verify=settings.SIMULATOR_CA_BUNDLE or True,
                follow_redirects=False,
                transport=TRANSPORT,
                headers={"Authorization": f"Bearer {settings.SIMULATOR_API_KEY}",
                         "User-Agent": "SecurePay-Gateway/1.0"},
            )
        return client


def _interpret(response: httpx.Response, operation: Operation) -> Answer:
    signature = response.headers.get(SIGNATURE_HEADER, "")
    if response.status_code not in (200, 409):
        return Answer(AnswerKind.NO_ANSWER, problem=f"http_{response.status_code}")
    tolerance = settings.GATEWAY_RULES["SIMULATOR_SIGNATURE_TOLERANCE_SECONDS"]
    if not signature or not signing.verify(response.content, signature,
                                           settings.SIMULATOR_SIGNING_SECRET, tolerance):
        return Answer(AnswerKind.NO_ANSWER, problem="bad_signature")
    try:
        data = json.loads(response.content)
    except ValueError:
        return Answer(AnswerKind.NO_ANSWER, problem="invalid_json")
    raw = response.content.decode()
    if response.status_code == 409:
        record = data.get("operation") or {}
        code = (data.get("error") or {}).get("code")
        if record.get("operation_id") != operation.public_id:
            return Answer(AnswerKind.NO_ANSWER, problem="wrong_operation")
        return Answer(AnswerKind.CONFLICT, record=record, conflict_code=code, raw_body=raw,
                      signature=signature)
    if data.get("operation_id") != operation.public_id:
        return Answer(AnswerKind.NO_ANSWER, problem="wrong_operation")
    return Answer(AnswerKind.RECORDED, record=data, raw_body=raw, signature=signature)


def _call(operation: Operation, method: str, path: str, **kwargs) -> Answer:
    try:
        response = _client().request(method, path, **kwargs)
    except httpx.TimeoutException:
        return Answer(AnswerKind.NO_ANSWER, problem="timeout")
    except httpx.HTTPError as exc:
        return Answer(AnswerKind.NO_ANSWER, problem=type(exc).__name__)
    answer = _interpret(response, operation)
    if answer.kind is AnswerKind.NO_ANSWER:
        logger.warning("No usable simulator answer for %s: %s", operation.public_id,
                       answer.problem)
    return answer


def authorise(operation: Operation, payment: Payment, card,
              authentication: dict | None = None) -> Answer:
    """Send the one authorisation for this payment. `card` comes from vault.take().

    v2: `authentication` is the bank's 3-D Secure proof {three_ds_id, eci, cavv}, if the
    shopper passed its challenge (docs/v2/THREE-DS.md)."""
    body = {
        "operation_id": operation.public_id,
        "merchant_id": payment.merchant.public_id,
        "payment_id": payment.public_id,
        # v2: the simulator and the network work in SGD; the payment's own currency and
        # rate stay on the gateway (docs/v2/FX.md).
        "amount": operation.settlement_amount,
        "currency": "SGD",
        "card": {"number": card.number, "expiry_month": card.expiry_month,
                 "expiry_year": card.expiry_year, "cvc": card.cvc},
    }
    if authentication:
        body["authentication"] = authentication
    return _call(operation, "POST", "/sim/authorise", json=body)


def capture(operation: Operation, authorisation_id: str) -> Answer:
    """Capture `operation.amount` of the authorisation (section 4); the rest is released."""
    body = {"operation_id": operation.public_id, "authorisation_id": authorisation_id,
            "amount": operation.settlement_amount}
    return _call(operation, "POST", "/sim/capture", json=body)


def cancel(operation: Operation, authorisation_id: str) -> Answer:
    """Void the whole authorisation (merchant cancel or the 7-day auto-cancel)."""
    body = {"operation_id": operation.public_id, "authorisation_id": authorisation_id}
    return _call(operation, "POST", "/sim/cancel", json=body)


def refund(operation: Operation, authorisation_id: str, refund_id: str) -> Answer:
    """Refund `operation.amount` of a captured authorisation (section 4)."""
    body = {"operation_id": operation.public_id, "authorisation_id": authorisation_id,
            "refund_id": refund_id, "amount": operation.settlement_amount}
    return _call(operation, "POST", "/sim/refund", json=body)


def status_query(operation: Operation) -> Answer:
    """Ask about an operation by its original ID (section 6). Used by reconciliation."""
    return _call(operation, "GET", f"/sim/operations/{operation.public_id}",
                 params={"type": operation.type})
