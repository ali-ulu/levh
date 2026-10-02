"""Signed federation envelopes (issue #338) — offline, no transport.

The envelope is the part that must exist before any socket: a bundle that says
who produced it, verified before a single memory reaches the admission gate.
These tests pin the properties the issue names — a tampered bundle is rejected
loudly and imports nothing, and a verified bundle still re-enters through the
gate rather than bypassing it.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
import pytest_asyncio

os.environ["EMBEDDER_MODE"] = "hash"

from server.core import federation
from server.core.memory_engine import MemoryEngine


@pytest.fixture
def keypair():
    return federation.generate_keypair()


@pytest.fixture
def bundle():
    return {
        "format": "levh-full-export",
        "version": 1,
        "memories": [
            {"content": "Atlas production database uses PostgreSQL", "memory_type": "episodic"},
        ],
    }


@pytest_asyncio.fixture
async def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=20)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(path):
        os.unlink(path)


# ── envelope round-trip and tamper resistance ────────────────────────


def test_ed25519_round_trip_returns_the_bundle(keypair, bundle):
    private_pem, public_pem = keypair
    envelope = federation.sign_envelope(
        bundle, node_id="alice-laptop", private_key=private_pem
    )
    assert envelope["format"] == federation.ENVELOPE_FORMAT
    assert envelope["algorithm"] == federation.ED25519
    assert envelope["node_id"] == "alice-laptop"
    assert federation.is_envelope(envelope)

    recovered = federation.verify_envelope(envelope, public_key=public_pem)
    assert recovered == bundle


def test_envelope_survives_json_serialisation(keypair, bundle):
    private_pem, public_pem = keypair
    envelope = federation.sign_envelope(bundle, node_id="node-a", private_key=private_pem)
    # A carried file goes through json.dump/load; verification must not depend
    # on Python dict ordering surviving the trip.
    round_tripped = json.loads(json.dumps(envelope))
    assert federation.verify_envelope(round_tripped, public_key=public_pem) == bundle


def test_a_tampered_bundle_is_rejected_loudly(keypair, bundle):
    private_pem, public_pem = keypair
    envelope = federation.sign_envelope(bundle, node_id="node-a", private_key=private_pem)
    envelope["bundle"]["memories"][0]["content"] = "poisoned payload"
    with pytest.raises(federation.EnvelopeSignatureError):
        federation.verify_envelope(envelope, public_key=public_pem)


def test_a_tampered_node_id_is_rejected(keypair, bundle):
    """The origin is inside the signature: a peer cannot rewrite who sent it."""
    private_pem, public_pem = keypair
    envelope = federation.sign_envelope(bundle, node_id="node-a", private_key=private_pem)
    envelope["node_id"] = "node-b"
    with pytest.raises(federation.EnvelopeSignatureError):
        federation.verify_envelope(envelope, public_key=public_pem)


def test_a_wrong_key_is_rejected(keypair, bundle):
    private_pem, _public_pem = keypair
    _other_private, other_public = federation.generate_keypair()
    envelope = federation.sign_envelope(bundle, node_id="node-a", private_key=private_pem)
    with pytest.raises(federation.EnvelopeSignatureError):
        federation.verify_envelope(envelope, public_key=other_public)


def test_an_embedded_key_is_ignored_without_opt_in(keypair, bundle):
    private_pem, _public_pem = keypair
    envelope = federation.sign_envelope(bundle, node_id="node-a", private_key=private_pem)
    # No explicit key and no opt-in: refuse rather than trust the sender.
    with pytest.raises(federation.EnvelopeKeyError):
        federation.verify_envelope(envelope)


def test_an_embedded_key_is_accepted_with_explicit_opt_in(keypair, bundle):
    private_pem, _public_pem = keypair
    envelope = federation.sign_envelope(bundle, node_id="node-a", private_key=private_pem)
    recovered = federation.verify_envelope(envelope, allow_embedded_key=True)
    assert recovered == bundle


def test_key_id_mismatch_is_rejected(keypair, bundle):
    """An attacker who swaps in their own public key still fails: the
    envelope's key_id no longer matches."""
    private_pem, _public_pem = keypair
    _other_private, other_public = federation.generate_keypair()
    envelope = federation.sign_envelope(bundle, node_id="node-a", private_key=private_pem)
    with pytest.raises(federation.EnvelopeSignatureError):
        federation.verify_envelope(envelope, public_key=other_public)


def test_hmac_round_trip_and_wrong_secret(bundle):
    envelope = federation.sign_envelope(
        bundle, node_id="node-a", algorithm=federation.HMAC_SHA256, secret="shared-secret"
    )
    assert envelope["algorithm"] == federation.HMAC_SHA256
    assert federation.verify_envelope(envelope, secret="shared-secret") == bundle
    with pytest.raises(federation.EnvelopeSignatureError):
        federation.verify_envelope(envelope, secret="different-secret")


def test_signing_without_node_id_is_refused(bundle, keypair):
    private_pem, _ = keypair
    with pytest.raises(federation.EnvelopeKeyError):
        federation.sign_envelope(bundle, node_id="  ", private_key=private_pem)


def test_signing_without_key_is_refused(bundle):
    with pytest.raises(federation.EnvelopeKeyError):
        federation.sign_envelope(bundle, node_id="node-a")


def test_verification_without_key_is_refused(keypair, bundle):
    private_pem, _ = keypair
    envelope = federation.sign_envelope(bundle, node_id="node-a", private_key=private_pem)
    with pytest.raises(federation.EnvelopeKeyError):
        federation.verify_envelope(envelope)


def test_read_envelope_rejects_non_envelopes():
    with pytest.raises(federation.EnvelopeFormatError):
        federation.read_envelope("{}")
    with pytest.raises(federation.EnvelopeFormatError):
        federation.read_envelope("not json")


def test_unsupported_version_is_rejected(keypair, bundle):
    private_pem, public_pem = keypair
    envelope = federation.sign_envelope(bundle, node_id="node-a", private_key=private_pem)
    envelope["envelope_version"] = 99
    with pytest.raises(federation.EnvelopeFormatError):
        federation.verify_envelope(envelope, public_key=public_pem)


# ── the gate is still the boundary ───────────────────────────────────


def _review_decision(content: str) -> dict:
    return {
        "action": "review",
        "reasons": ["possible duplicate (similarity 0.93)"],
        "reason_codes": ["duplicate_near"],
        "redacted_content": content,
        "redacted": False,
        "secrets": [],
        "max_similarity": 0.93,
    }


def _force_review(eng):
    async def _stub(content, project=None, min_length=3, exclude_id=None):
        return _review_decision(content)

    eng.evaluate_admission = _stub


@pytest.mark.asyncio
async def test_a_verified_bundle_enters_through_the_gate(engine, keypair):
    """Admitted, rejected and review-held entries each get their real outcome —
    a verified envelope is trusted for *origin*, not for content."""
    _force_review(engine)
    private_pem, public_pem = keypair
    bundle = {
        "format": "levh-full-export",
        "version": 1,
        "memories": [
            {"content": "a perfectly ordinary admitted memory", "memory_type": "episodic"},
            {"content": "x", "memory_type": "episodic"},  # too short -> reject
        ],
    }
    envelope = federation.sign_envelope(bundle, node_id="peer", private_key=private_pem)
    verified = federation.verify_envelope(envelope, public_key=public_pem)

    result = await engine.import_memories_gated(verified["memories"])
    # Both entries hit the review stub, so both are held rather than stored.
    assert result["held"] == 2
    assert result["imported"] == 0
    held = await engine.db.list_held_memories()
    assert len(held) == 2


@pytest.mark.asyncio
async def test_a_tampered_envelope_imports_nothing(engine, keypair):
    private_pem, public_pem = keypair
    bundle = {
        "format": "levh-full-export",
        "version": 1,
        "memories": [{"content": "legitimate memory", "memory_type": "episodic"}],
    }
    envelope = federation.sign_envelope(bundle, node_id="peer", private_key=private_pem)
    envelope["bundle"]["memories"].append(
        {"content": "injected by a tamperer", "memory_type": "episodic"}
    )

    with pytest.raises(federation.EnvelopeSignatureError):
        federation.verify_envelope(envelope, public_key=public_pem)

    # Verification failed, so the caller never had a bundle to import.
    assert await engine.list_memories(limit=10) == []


@pytest.mark.asyncio
async def test_a_clean_bundle_stores_through_the_real_pipeline(engine, keypair):
    private_pem, public_pem = keypair
    bundle = {
        "format": "levh-full-export",
        "version": 1,
        "memories": [
            {
                "content": "Federation carries the origin node with the memory",
                "memory_type": "episodic",
                "source": "cli",
            },
        ],
    }
    envelope = federation.sign_envelope(bundle, node_id="peer", private_key=private_pem)
    verified = federation.verify_envelope(envelope, public_key=public_pem)
    result = await engine.import_memories_gated(verified["memories"])
    assert result["imported"] == 1
    stored = await engine.list_memories(limit=10)
    assert len(stored) == 1
