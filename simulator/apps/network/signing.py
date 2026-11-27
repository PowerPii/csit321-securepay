"""Signatures on messages between the network nodes (contracts/NETWORK-CONTRACT.md section 3).

Same scheme as the simulator's replies: `Network-Signature: t=<unix seconds>,v1=<hex
HMAC-SHA256>` over `"<t>." + raw body`. The key is derived from the simulator signing
secret, so the three nodes share it without a new secret file, and a signed simulator reply
can never pass as a network message (or the other way round).
"""
import hashlib
import hmac
import time

from django.conf import settings

from apps.common.http import sign as _sign

SIGNATURE_HEADER = "Network-Signature"
TOLERANCE_SECONDS = 300


def network_key() -> str:
    if not settings.SIMULATOR_SIGNING_SECRET:
        raise RuntimeError("SIMULATOR_SIGNING_SECRET is not set; the network cannot sign.")
    return hashlib.sha256(f"network|{settings.SIMULATOR_SIGNING_SECRET}".encode()).hexdigest()


def sign(body: bytes, timestamp: int | None = None) -> str:
    return _sign(body, network_key(), timestamp)


def verify(body: bytes, header: str | None, now: int | None = None) -> bool:
    if not header:
        return False
    parts = dict(p.partition("=")[::2] for p in header.split(","))
    try:
        ts = int(parts.get("t", ""))
    except ValueError:
        return False
    now = int(time.time()) if now is None else now
    if abs(now - ts) > TOLERANCE_SECONDS:
        return False
    expected = sign(body, ts).partition(",v1=")[2]
    return hmac.compare_digest(expected, parts.get("v1", ""))
