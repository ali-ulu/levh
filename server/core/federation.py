"""Signed memory federation envelope — offline provenance-verified transfer.

The offline slice of the federation workstream (``docs/internal/ROADMAP.md``,
Phase B; issue #338). It signs a :func:`server.core.full_export.build_full_export`
bundle so a receiving machine can tell a peer's bundle from its own writes.
There is deliberately no transport here: an envelope first, a socket later.

Envelope shape::

    {
      "format": "levh-federation-envelope",
      "version": 1,
      "node_id": "<operator-chosen node name>",
      "created_at": "<ISO-8601 UTC>",
      "algorithm": "hmac-sha256" | "ed25519",
      "bundle": { ...the full export bundle... },
      "signature": "<base64>"
    }

The signature covers the canonical JSON (sorted keys, no whitespace) of
``{node_id, created_at, algorithm, bundle}``. Any change to the payload — a
flipped byte, a swapped node id, an extra memory — invalidates it, and
:func:`verify_envelope` rejects the whole envelope rather than importing part
of it.

Two algorithms, both from the already-required ``cryptography`` dependency:

- ``hmac-sha256``: symmetric, keyed by a shared secret file. Enough when both
  machines belong to the same operator.
- ``ed25519``: asymmetric, keyed by an operator-held PEM private key; the
  receiver verifies with the public key alone. This is the shape the
  *Portable Agent Memory* provenance argument points at — the operator owns
  the key, not the platform. Generate a pair with
  ``openssl genpkey -algorithm ED25519 -out priv.pem`` and
  ``openssl pkey -in priv.pem -pubout -out pub.pem``.

The envelope carries a bundle but does not import it. Re-entry is still through
``import_memories_gated``, the admission gate; a peer's bundle is untrusted
input and the gate remains the boundary (see ``SECURITY.md``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ENVELOPE_FORMAT = "levh-federation-envelope"
ENVELOPE_VERSION = 1
ALGORITHMS = ("hmac-sha256", "ed25519")


class EnvelopeError(ValueError):
    """A malformed, unsigned, tampered, or wrong-key federation envelope."""


def _canonical(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _signing_payload(node_id: str, created_at: str, algorithm: str, bundle: dict) -> dict:
    return {
        "node_id": node_id,
        "created_at": created_at,
        "algorithm": algorithm,
        "bundle": bundle,
    }


def _json_safe(value: Any) -> Any:
    """Round-trip a value through JSON so signing and verification canonicalise
    the same bytes. The receiver parses JSON, so the bundle has to be what
    survives that parse, not the in-memory objects that produced it."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _read_key(path: str) -> bytes:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise EnvelopeError(f"cannot read key file {path!r}: {exc}") from exc
    if not data.strip():
        raise EnvelopeError(f"key file {path!r} is empty")
    return data


def _load_ed25519_private(path: str):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    data = _read_key(path)
    try:
        return load_pem_private_key(data, password=None)
    except (ValueError, TypeError) as exc:
        raise EnvelopeError(f"{path!r} is not an unencrypted PEM private key") from exc


def _load_ed25519_public(path: str):
    from cryptography.hazmat.primitives.serialization import (
        load_pem_private_key,
        load_pem_public_key,
    )

    data = _read_key(path)
    try:
        return load_pem_public_key(data)
    except ValueError:
        pass
    # A receiver may hold the same private key the sender used; deriving the
    # public half keeps verification working without a second file.
    try:
        private = load_pem_private_key(data, password=None)
    except (ValueError, TypeError) as exc:
        raise EnvelopeError(
            f"{path!r} is neither an unencrypted PEM public nor private key"
        ) from exc
    public = getattr(private, "public_key", None)
    if public is None:
        raise EnvelopeError(f"{path!r} does not carry an ed25519 public key")
    return public


def _ensure_crypto() -> None:
    from .crypto import ensure_available

    ensure_available()


def sign_envelope(
    bundle: dict,
    *,
    node_id: str,
    key_path: str,
    algorithm: str = "hmac-sha256",
) -> dict:
    """Sign ``bundle`` into a self-describing federation envelope."""
    if algorithm not in ALGORITHMS:
        raise EnvelopeError(
            f"unsupported algorithm {algorithm!r}; choose one of {', '.join(ALGORITHMS)}"
        )
    if not node_id or not node_id.strip():
        raise EnvelopeError("node_id must not be empty")
    if not isinstance(bundle, dict):
        raise EnvelopeError("bundle must be a JSON object")
    _ensure_crypto()

    created_at = datetime.now(timezone.utc).isoformat()
    safe_bundle = _json_safe(bundle)
    payload = _signing_payload(node_id, created_at, algorithm, safe_bundle)
    message = _canonical(payload)

    if algorithm == "hmac-sha256":
        secret = _read_key(key_path).strip()
        signature = hmac.new(secret, message, hashlib.sha256).digest()
    else:
        private = _load_ed25519_private(key_path)
        signature = private.sign(message)

    return {
        "format": ENVELOPE_FORMAT,
        "version": ENVELOPE_VERSION,
        "node_id": node_id,
        "created_at": created_at,
        "algorithm": algorithm,
        "bundle": safe_bundle,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def verify_envelope(
    envelope: dict,
    *,
    key_path: str,
    expected_node_id: str | None = None,
) -> dict:
    """Verify an envelope and return its bundle.

    Raises :class:`EnvelopeError` for anything that is not an intact envelope
    signed by the holder of ``key_path``. The bundle is never returned on a
    failed check, so a caller cannot import a partially trusted bundle.
    """
    if not isinstance(envelope, dict):
        raise EnvelopeError("envelope must be a JSON object")
    if envelope.get("format") != ENVELOPE_FORMAT:
        raise EnvelopeError("not a LEVH federation envelope")
    if envelope.get("version") != ENVELOPE_VERSION:
        raise EnvelopeError(
            f"unsupported envelope version {envelope.get('version')!r}"
        )

    node_id = envelope.get("node_id")
    created_at = envelope.get("created_at")
    algorithm = envelope.get("algorithm")
    bundle = envelope.get("bundle")
    signature_b64 = envelope.get("signature")

    if not isinstance(node_id, str) or not node_id.strip():
        raise EnvelopeError("envelope is missing a node_id")
    if not isinstance(created_at, str) or not created_at:
        raise EnvelopeError("envelope is missing created_at")
    if algorithm not in ALGORITHMS:
        raise EnvelopeError(f"unsupported algorithm {algorithm!r}")
    if not isinstance(bundle, dict):
        raise EnvelopeError("envelope is missing its bundle")
    if not isinstance(signature_b64, str) or not signature_b64:
        raise EnvelopeError("envelope is missing its signature")
    if expected_node_id is not None and node_id != expected_node_id:
        raise EnvelopeError(
            f"envelope is from node {node_id!r}, expected {expected_node_id!r}"
        )

    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise EnvelopeError("signature is not valid base64") from exc

    message = _canonical(_signing_payload(node_id, created_at, algorithm, bundle))

    if algorithm == "hmac-sha256":
        secret = _read_key(key_path).strip()
        expected = hmac.new(secret, message, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            raise EnvelopeError("signature does not verify (wrong key or tampered envelope)")
    else:
        from cryptography.exceptions import InvalidSignature

        public = _load_ed25519_public(key_path)
        try:
            public.verify(signature, message)
        except InvalidSignature as exc:
            raise EnvelopeError(
                "signature does not verify (wrong key or tampered envelope)"
            ) from exc

    return bundle
