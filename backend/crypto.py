"""Server-side at-rest encryption for AnyDevice.

Every share gets a random 256-bit AES key (per share). Item content is
wrapped with that key before being written to the database / blob store, so
the raw plaintext never touches disk. The key lives only in the share's row
and is never returned to clients.

Ciphertext layout is the same as the browser's AES-GCM format:
    [12-byte random IV][AES-GCM ciphertext]
Text items are stored as base64url of that payload; file blobs are stored as
the raw payload bytes.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

IV_LEN = 12

_B64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def new_key() -> str:
    """Fresh random 256-bit key, serialized as base64url (server-held)."""
    return _to_b64url(os.urandom(32))


def _from_b64url(text: str) -> bytes:
    raw = text.replace("-", "+").replace("_", "/")
    raw += "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw)


def _to_b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def encrypt_bytes(data: bytes, key_b64: str) -> bytes:
    """Wrap plaintext bytes → [iv][ciphertext] with the given server key."""
    key = _from_b64url(key_b64)
    iv = os.urandom(IV_LEN)
    ct = AESGCM(key).encrypt(iv, data, None)
    return iv + ct


def decrypt_bytes(payload: bytes, key_b64: str) -> bytes:
    """Unwrap [iv][ciphertext] → plaintext. Raises on a wrong/corrupt key."""
    if len(payload) <= IV_LEN:
        raise ValueError("corrupt encrypted payload")
    key = _from_b64url(key_b64)
    return AESGCM(key).decrypt(payload[:IV_LEN], payload[IV_LEN:], None)
