"""How the acquirer and the switch reach the next node (contracts/NETWORK-CONTRACT.md 4).

With SIM_ROLE=all (the default, and every test) the three nodes run in one process and
call each other directly. In Compose each node is its own container (SIM_ROLE=acquirer,
switch or issuer), and SIM_SWITCH_URL / SIM_ISSUER_URL say where the next one listens; the
messages then go over the private simulator network as signed JSON.
"""
import json

import httpx
from django.conf import settings

from apps.network import nodes, signing

# Tests replace this with an httpx.MockTransport.
TRANSPORT: httpx.BaseTransport | None = None


def _post(url: str, message: dict) -> dict:
    body = json.dumps(message, separators=(",", ":")).encode()
    try:
        with httpx.Client(timeout=settings.SIM_NETWORK_TIMEOUT, transport=TRANSPORT) as client:
            reply = client.post(url, content=body, headers={
                "Content-Type": "application/json",
                signing.SIGNATURE_HEADER: signing.sign(body)})
    except httpx.HTTPError as exc:
        raise nodes.NetworkError(str(exc)) from exc
    if reply.status_code != 200 or not signing.verify(
            reply.content, reply.headers.get(signing.SIGNATURE_HEADER)):
        raise nodes.NetworkError(f"{url}: {reply.status_code} or bad signature")
    try:
        data = json.loads(reply.content)
    except ValueError as exc:
        raise nodes.NetworkError(f"{url}: not JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("message"), dict):
        raise nodes.NetworkError(f"{url}: not a network reply")
    return data


def to_issuer(message: dict) -> dict:
    if settings.SIM_ISSUER_URL:
        return _post(settings.SIM_ISSUER_URL, message)
    return nodes.issuer(message)


def to_switch(message: dict) -> dict:
    if settings.SIM_SWITCH_URL:
        return _post(settings.SIM_SWITCH_URL, message)
    return nodes.switch(message, to_issuer)
