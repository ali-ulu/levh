"""Tests for the offline signed federation envelope (#338).

Offline and deterministic: no transport, no model key, no dataset. The
ed25519 cases skip cleanly when ``cryptography`` is absent, matching how the
rest of the suite treats that optional dependency.
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.federation import (
    EnvelopeError,
    sign_envelope,
    verify_envelope,
)


def _bundle() -> dict:
    return {
        "format": "levh-full-export",
        "version": 1,
        "created_at": "2026-10-02T00:00:00+00:00",
        "counts": {"memories": 2, "entities": 0, "trust_scores": 0, "conflicts": 0},
        "memories": [
            {"id": "m1", "content": "Atlas uses PostgreSQL in production", "metadata": {}},
            {"id": "m2", "content": "The release is on Friday", "metadata": {}},
        ],
        "entities": [],
        "entity_stats": {"by_type": {}},
        "trust": [],
        "conflicts": [],
    }


def _write_key(text: str) -> str:
    fd, path = tempfile.mkstemp()
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_hmac_envelope_round_trips():
    key = _write_key("shared-secret")
    try:
        envelope = sign_envelope(_bundle(), node_id="laptop", key_path=key)
        assert envelope["format"] == "levh-federation-envelope"
        assert envelope["node_id"] == "laptop"
        assert envelope["algorithm"] == "hmac-sha256"
        # The envelope must survive a JSON round-trip before verification, or a
        # receiver could never check what the sender signed.
        transported = json.loads(json.dumps(envelope))
        bundle = verify_envelope(transported, key_path=key)
        assert bundle["counts"]["memories"] == 2
        assert bundle["memories"][0]["content"] == "Atlas uses PostgreSQL in production"
    finally:
        os.unlink(key)


def test_tampered_bundle_is_rejected_loudly():
    key = _write_key("shared-secret")
    try:
        envelope = sign_envelope(_bundle(), node_id="laptop", key_path=key)
        envelope["bundle"]["memories"].append({"id": "m3", "content": "injected"})
        with pytest.raises(EnvelopeError):
            verify_envelope(envelope, key_path=key)
    finally:
        os.unlink(key)


def test_wrong_key_is_rejected():
    key = _write_key("shared-secret")
    other = _write_key("a-different-secret")
    try:
        envelope = sign_envelope(_bundle(), node_id="laptop", key_path=key)
        with pytest.raises(EnvelopeError):
            verify_envelope(envelope, key_path=other)
    finally:
        os.unlink(key)
        os.unlink(other)


def test_expected_node_id_mismatch_is_rejected():
    key = _write_key("shared-secret")
    try:
        envelope = sign_envelope(_bundle(), node_id="laptop", key_path=key)
        with pytest.raises(EnvelopeError):
            verify_envelope(envelope, key_path=key, expected_node_id="workstation")
    finally:
        os.unlink(key)


def test_missing_signature_is_rejected():
    key = _write_key("shared-secret")
    try:
        envelope = sign_envelope(_bundle(), node_id="laptop", key_path=key)
        del envelope["signature"]
        with pytest.raises(EnvelopeError):
            verify_envelope(envelope, key_path=key)
    finally:
        os.unlink(key)


def test_non_envelope_input_is_rejected():
    key = _write_key("shared-secret")
    try:
        with pytest.raises(EnvelopeError):
            verify_envelope({"format": "levh-full-export"}, key_path=key)
    finally:
        os.unlink(key)


def _ed25519_keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    priv_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    pub_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return priv_pem, pub_pem


def test_ed25519_envelope_verifies_with_public_key_only():
    pytest.importorskip("cryptography")
    priv_pem, pub_pem = _ed25519_keypair()
    priv = _write_key(priv_pem)
    pub = _write_key(pub_pem)
    try:
        envelope = sign_envelope(
            _bundle(), node_id="workstation", key_path=priv, algorithm="ed25519"
        )
        assert envelope["algorithm"] == "ed25519"
        transported = json.loads(json.dumps(envelope))
        bundle = verify_envelope(transported, key_path=pub)
        assert bundle["counts"]["memories"] == 2
    finally:
        os.unlink(priv)
        os.unlink(pub)


def test_ed25519_tamper_is_rejected():
    pytest.importorskip("cryptography")
    priv_pem, pub_pem = _ed25519_keypair()
    priv = _write_key(priv_pem)
    pub = _write_key(pub_pem)
    try:
        envelope = sign_envelope(
            _bundle(), node_id="workstation", key_path=priv, algorithm="ed25519"
        )
        envelope["node_id"] = "impostor"
        with pytest.raises(EnvelopeError):
            verify_envelope(envelope, key_path=pub)
    finally:
        os.unlink(priv)
        os.unlink(pub)
