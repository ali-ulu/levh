"""Passphrase-based authenticated encryption for backups (Faz 0 security).

A LEVH backup can hold a person's entire work-life memory, so an
exported file must be encryptable at rest. This wraps the well-reviewed
``cryptography`` library rather than rolling our own:

  - key derivation: PBKDF2-HMAC-SHA256 (``PBKDF2_ITERATIONS`` rounds) over a
    random 16-byte salt → 32-byte key.
  - cipher: Fernet (AES-128-CBC + HMAC-SHA256) — authenticated, so a wrong
    passphrase or a tampered file fails loudly instead of returning garbage.

Envelope layout (bytes):  MAGIC (7) | salt (16) | fernet-token (rest)

The salt is stored in the clear (standard practice — it isn't secret; it
only stops precomputed-hash attacks and makes each file's key unique).

``cryptography`` ships prebuilt wheels for every platform, so this adds no
build toolchain requirement. If it is somehow missing, ``ensure_available``
raises a clear, actionable error instead of a bare ImportError.
"""

from __future__ import annotations

import base64
import os

MAGIC = b"SMENC1\n"  # 7 bytes — identifies an encrypted LEVH backup
SALT_LEN = 16
PBKDF2_ITERATIONS = 390_000


class CryptoUnavailableError(RuntimeError):
    """Raised when the optional ``cryptography`` dependency isn't installed."""


class DecryptionError(ValueError):
    """Wrong passphrase, or a corrupted/tampered backup file."""


def is_available() -> bool:
    """True if passphrase encryption can be used in this environment."""
    try:
        import cryptography  # noqa: F401

        return True
    except Exception:
        return False


def ensure_available() -> None:
    if not is_available():
        raise CryptoUnavailableError(
            "Passphrase encryption needs the 'cryptography' package. "
            "Install it with:  pip install cryptography   (or: pip install "
            "'stackmemory[secure]'). Unencrypted backups work without it."
        )


def is_encrypted(blob: bytes) -> bool:
    """Cheap check: does this blob start with our encrypted-backup magic?"""
    return isinstance(blob, (bytes, bytearray)) and bytes(blob[: len(MAGIC)]) == MAGIC


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def encrypt(data: bytes, passphrase: str) -> bytes:
    """Encrypt ``data`` under ``passphrase`` into a self-describing envelope."""
    ensure_available()
    if not passphrase:
        raise ValueError("passphrase must not be empty")
    from cryptography.fernet import Fernet

    salt = os.urandom(SALT_LEN)
    token = Fernet(_derive_key(passphrase, salt)).encrypt(data)
    return MAGIC + salt + token


def decrypt(blob: bytes, passphrase: str) -> bytes:
    """Reverse :func:`encrypt`. Raises :class:`DecryptionError` on a wrong
    passphrase or a tampered/corrupt file."""
    ensure_available()
    if not is_encrypted(blob):
        raise DecryptionError("not an encrypted LEVH backup")
    from cryptography.fernet import Fernet, InvalidToken

    body = bytes(blob[len(MAGIC):])
    salt, token = body[:SALT_LEN], body[SALT_LEN:]
    if len(salt) < SALT_LEN or not token:
        raise DecryptionError("truncated or corrupt encrypted backup")
    try:
        return Fernet(_derive_key(passphrase, salt)).decrypt(token)
    except InvalidToken as exc:
        raise DecryptionError("wrong passphrase or corrupted backup") from exc
