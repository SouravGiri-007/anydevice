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

IV_LEN: int = 12

_B64_CHARS: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def new_key() -> str:
    """Generate a fresh random 256-bit AES key, serialized as base64url.

    Returns:
        Base64url-encoded 256-bit key string
    """
    return _to_b64url(os.urandom(32))


def _from_b64url(text: str) -> bytes:
    """Decode a base64url string to raw bytes.

    Args:
        text: Base64url-encoded string

    Returns:
        Raw bytes

    Raises:
        binascii.Error: If the string is not valid base64url
    """
    raw = text.replace("-", "+").replace("_", "/")
    raw += "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw)


def _to_b64url(raw: bytes) -> str:
    """Encode raw bytes to base64url string.

    Args:
        raw: Raw bytes to encode

    Returns:
        Base64url-encoded string (without padding)
    """
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def encrypt_bytes(data: bytes, key_b64: str) -> bytes:
    """Encrypt plaintext bytes using AES-256-GCM.

    Generates a random 12-byte IV and concatenates it with the ciphertext.

    Args:
        data: Plaintext bytes to encrypt
        key_b64: Base64url-encoded AES-256 key

    Returns:
        Encrypted payload as [12-byte IV][ciphertext]

    Raises:
        ValueError: If the key is invalid or malformed
    """
    key = _from_b64url(key_b64)
    iv = os.urandom(IV_LEN)
    ct = AESGCM(key).encrypt(iv, data, None)
    return iv + ct


def decrypt_bytes(payload: bytes, key_b64: str) -> bytes:
    """Decrypt AES-256-GCM encrypted payload.

    Expects payload format: [12-byte IV][ciphertext]

    Args:
        payload: Encrypted payload with IV prepended
        key_b64: Base64url-encoded AES-256 key

    Returns:
        Decrypted plaintext bytes

    Raises:
        ValueError: If payload is corrupt or too short
        cryptography.hazmat.primitives.ciphers.aead.InvalidTag: If decryption fails
    """
    if len(payload) <= IV_LEN:
        raise ValueError("corrupt encrypted payload")
    key = _from_b64url(key_b64)
    return AESGCM(key).decrypt(payload[:IV_LEN], payload[IV_LEN:], None)
