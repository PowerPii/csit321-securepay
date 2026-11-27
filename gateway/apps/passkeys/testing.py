"""A software authenticator for tests: makes the same answers a phone or laptop would.

It holds one P-256 key per passkey and answers navigator.credentials.create() and .get()
options with "none" attestation, so the real `webauthn` checks run on real signatures.
Used only by tests; it never runs in the gateway.
"""
import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

UP, UV, BE, BS, AT = 0x01, 0x04, 0x08, 0x10, 0x40


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftAuthenticator:
    def __init__(self, rp_id: str, origin: str, *, user_verified: bool = True,
                 synced: bool = True):
        self.rp_id, self.origin = rp_id, origin
        self.user_verified, self.synced = user_verified, synced
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.counter = 0
        self.user_handle = b""

    def _flags(self, extra: int = 0) -> int:
        flags = UP | extra
        if self.user_verified:
            flags |= UV
        if self.synced:
            flags |= BE | BS
        return flags

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge,
                           "origin": origin or self.origin, "crossOrigin": False}).encode()

    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"),
                            -3: numbers.y.to_bytes(32, "big")})

    def create(self, options: dict, origin: str | None = None) -> dict:
        """The answer to registration options (the `options` the gateway sent)."""
        self.user_handle = unb64(options["user"]["id"])
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        auth_data = (hashlib.sha256(self.rp_id.encode()).digest()
                     + bytes([self._flags(AT)]) + struct.pack(">I", self.counter)
                     + bytes(16) + struct.pack(">H", len(self.credential_id))
                     + self.credential_id + self._cose_key())
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {"id": b64(self.credential_id), "rawId": b64(self.credential_id),
                "type": "public-key", "clientExtensionResults": {},
                "authenticatorAttachment": "platform",
                "response": {"clientDataJSON": b64(client_data),
                             "attestationObject": b64(attestation),
                             "transports": ["internal", "hybrid"]}}

    def get(self, options: dict, origin: str | None = None, counter: int | None = None) -> dict:
        """The answer to assertion options."""
        self.counter = self.counter + 1 if counter is None else counter
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        auth_data = (hashlib.sha256(self.rp_id.encode()).digest() + bytes([self._flags()])
                     + struct.pack(">I", self.counter))
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(),
                                  ec.ECDSA(hashes.SHA256()))
        return {"id": b64(self.credential_id), "rawId": b64(self.credential_id),
                "type": "public-key", "clientExtensionResults": {},
                "authenticatorAttachment": "platform",
                "response": {"clientDataJSON": b64(client_data), "authenticatorData":
                             b64(auth_data), "signature": b64(signature),
                             "userHandle": b64(self.user_handle) if self.user_handle
                             else None}}
