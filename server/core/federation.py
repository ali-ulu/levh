"""Signed federation envelope — offline, provenance-verified memory transfer.

Issue #338 asks for the *envelope* before any transport. A ``full_export``
bundle is unsigned today, so a file that travels between two machines says
nothing about which node produced it. This module wraps that bundle in a
signature the receiver verifies before a single memory reaches the store.

Design decisions (the open questions in #338, settled here):

- **Signature, not encryption.** ``crypto.py`` already gives confidentiality
  at rest. This is authenticity: *who* produced the bundle. The two compose —
  sign the JSON, then encrypt the blob if the operator wants both.
- **The operator owns the key.** Ed25519 is the default; the private key never
  leaves the machine. A shared-secret HMAC mode exists for operators who
  cannot manage key pairs, at the cost of symmetric trust.
- **Fail closed.** A tampered bundle is rejected loudly and *nothing* is
  imported. Verification happens on the whole envelope (metadata included), so
  a peer cannot rewrite the claimed origin either.
- **Embedded keys are opt-in.** The envelope carries the sender's public key
  for convenience, but ``verify_envelope`` ignores it unless the caller passes
  ``allow_embedded_key=True`` (trust-on-first-use). The strict default is an
  explicit key.

Envelope layout (JSON object)::

    {
      "format": "levh-federation-envelope",
      "envelope_version": 1,
      "algorithm": "ed25519" | "hmac-sha256",
      "node_id": "alice-laptop",
      "key_id": "<fingerprint>",
      "created_at": "<ISO-8601>",
      "public_key": "<PEM>",          # ed25519 only, convenience
      "bundle": { ...full_export... },
      "signature": "<base64>"
    }

The signature covers every field except ``signature`` itself, serialised
canonically (sorted keys, no whitespace) so the sender and receiver hash the
same bytes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

ENVELOPE_FORMAT = "levh-federation-envelope"
ENVELOPE_VERSION = 1
ED25519 = "ed25519"
HMAC_SHA256 = "hmac-sha256"
SUPPORTED_ALGORITHMS = (ED25519, HMAC_SHA256)


class EnvelopeError(ValueError):
    """Base class: a bundle that cannot be trusted."""


class EnvelopeFormatError(EnvelopeError):
    """The envelope is malformed — missing fields, wrong format/version."""


class EnvelopeSignatureError(EnvelopeError):
    """The signature does not verify: tampered, wrong key, or wrong secret."""


class EnvelopeKeyError(EnvelopeError):
    """The key material is missing, malformed, or of the wrong kind."""


def is_envelope(obj: Any) -> bool:
    """Cheap shape check: is this a federation envelope rather than a raw bundle?"""
    return isinstance(obj, dict) and obj.get("format") == ENVELOPE_FORMAT


def _canonical(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _signing_payload(envelope: dict) -> bytes:
    """Every envelope field except the signature, in canonical form."""
    return _canonical({k: v for k, v in envelope.items() if k != "signature"})


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value: str, what: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:  # any decode failure is a malformed envelope
        raise EnvelopeFormatError(f"{what} is not valid base64") from exc


# ── key material ─────────────────────────────────────────────────────


def generate_keypair() -> tuple[str, str]:
    """Return ``(private_pem, public_pem)`` for a fresh Ed25519 key pair."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private_pem, public_pem


def _load_ed25519_private(private_pem: str):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        private = serialization.load_pem_private_key(
            private_pem.encode("utf-8"), password=None
        )
    except Exception as exc:  # a bad PEM is a key error, not a crash
        raise EnvelopeKeyError("private key is not a valid PEM") from exc
    if not isinstance(private, Ed25519PrivateKey):
        raise EnvelopeKeyError("private key is not an Ed25519 key")
    return private


def _load_ed25519_public(public_pem: str):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        public = serialization.load_pem_public_key(public_pem.encode("utf-8"))
    except Exception as exc:  # a bad PEM is a key error, not a crash
        raise EnvelopeKeyError("public key is not a valid PEM") from exc
    if not isinstance(public, Ed25519PublicKey):
        raise EnvelopeKeyError("public key is not an Ed25519 key")
    return public


def public_from_private(private_pem: str) -> str:
    """Derive the PEM public key from an Ed25519 private key PEM."""
    from cryptography.hazmat.primitives import serialization

    private = _load_ed25519_private(private_pem)
    return private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def key_fingerprint(public_pem: str) -> str:
    """Short, stable id for a public key: sha256(DER) truncated to 16 hex."""
    from cryptography.hazmat.primitives import serialization

    public = _load_ed25519_public(public_pem)
    der = public.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()[:16]


def _secret_fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


# ── signing / verification ───────────────────────────────────────────


def sign_envelope(
    bundle: dict,
    *,
    node_id: str,
    algorithm: str = ED25519,
    private_key: str | None = None,
    secret: str | None = None,
    created_at: str | None = None,
) -> dict:
    """Wrap ``bundle`` in a signed envelope.

    ``private_key`` is an Ed25519 PEM and is required for the default
    algorithm. ``secret`` is the shared secret for ``hmac-sha256``. The
    resulting envelope is a plain JSON-serialisable dict.
    """
    if not node_id or not str(node_id).strip():
        raise EnvelopeKeyError("node_id is required so the receiver knows the origin")
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise EnvelopeKeyError(
            f"unsupported algorithm {algorithm!r}; use one of {SUPPORTED_ALGORITHMS}"
        )
    if not isinstance(bundle, dict):
        raise EnvelopeFormatError("bundle must be a JSON object")

    envelope: dict[str, Any] = {
        "format": ENVELOPE_FORMAT,
        "envelope_version": ENVELOPE_VERSION,
        "algorithm": algorithm,
        "node_id": str(node_id).strip(),
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "bundle": bundle,
    }

    if algorithm == ED25519:
        if not private_key:
            raise EnvelopeKeyError("ed25519 signing needs a private key PEM")
        private = _load_ed25519_private(private_key)
        public_pem = public_from_private(private_key)
        envelope["key_id"] = key_fingerprint(public_pem)
        envelope["public_key"] = public_pem
        signature = private.sign(_signing_payload(envelope))
    else:
        if not secret:
            raise EnvelopeKeyError("hmac-sha256 signing needs a shared secret")
        envelope["key_id"] = _secret_fingerprint(secret)
        signature = hmac.new(
            secret.encode("utf-8"), _signing_payload(envelope), hashlib.sha256
        ).digest()

    envelope["signature"] = _b64(signature)
    return envelope


def verify_envelope(
    envelope: dict,
    *,
    public_key: str | None = None,
    secret: str | None = None,
    allow_embedded_key: bool = False,
) -> dict:
    """Verify an envelope and return its ``bundle``.

    Raises :class:`EnvelopeSignatureError` when the signature does not match,
    :class:`EnvelopeFormatError` for a malformed envelope, and
    :class:`EnvelopeKeyError` when no usable key was supplied. A caller that
    has not verified must not import anything: the failure is raised before
    the bundle is returned, so a partial import is impossible.
    """
    if not is_envelope(envelope):
        raise EnvelopeFormatError("not a LEVH federation envelope")
    if envelope.get("envelope_version") != ENVELOPE_VERSION:
        raise EnvelopeFormatError(
            f"unsupported envelope_version {envelope.get('envelope_version')!r}"
        )
    algorithm = envelope.get("algorithm")
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise EnvelopeFormatError(f"unsupported algorithm {algorithm!r}")
    if not envelope.get("node_id"):
        raise EnvelopeFormatError("envelope is missing node_id")
    bundle = envelope.get("bundle")
    if not isinstance(bundle, dict):
        raise EnvelopeFormatError("envelope is missing its bundle")
    signature_b64 = envelope.get("signature")
    if not isinstance(signature_b64, str) or not signature_b64:
        raise EnvelopeFormatError("envelope is missing its signature")
    signature = _unb64(signature_b64, "signature")
    payload = _signing_payload(envelope)

    if algorithm == ED25519:
        from cryptography.exceptions import InvalidSignature

        key_pem = public_key
        if key_pem is None:
            if allow_embedded_key:
                key_pem = envelope.get("public_key")
            if not key_pem:
                raise EnvelopeKeyError(
                    "no public key supplied; pass one, or opt in to the embedded key"
                )
        public = _load_ed25519_public(key_pem)
        expected_key_id = envelope.get("key_id")
        if expected_key_id and key_fingerprint(key_pem) != expected_key_id:
            raise EnvelopeSignatureError(
                "public key does not match the envelope's key_id"
            )
        try:
            public.verify(signature, payload)
        except InvalidSignature as exc:
            raise EnvelopeSignatureError(
                "signature does not verify: the envelope was tampered with or "
                "signed by a different key"
            ) from exc
    else:
        if not secret:
            raise EnvelopeKeyError("hmac-sha256 verification needs the shared secret")
        expected = hmac.new(
            secret.encode("utf-8"), payload, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, signature):
            raise EnvelopeSignatureError(
                "signature does not verify: the envelope was tampered with or "
                "signed with a different secret"
            )

    return bundle


def read_envelope(raw: bytes | str) -> dict:
    """Parse JSON bytes/str into an envelope dict (shape-checked, not verified)."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise EnvelopeFormatError("envelope is not valid JSON") from exc
    if not is_envelope(obj):
        raise EnvelopeFormatError("not a LEVH federation envelope")
    return obj
