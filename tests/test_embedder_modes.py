"""Embedder-mode behavior that must stay clear for public users."""

from __future__ import annotations

import httpx
import pytest

from server.core import embedder as embedder_module
from server.core.embedder import Embedder, probe_embedder_status


def test_hash_embedder_is_deterministic():
    embedder = Embedder("hash")
    a = embedder.hash_embed("same text")
    b = embedder.hash_embed("same text")
    assert a == b
    assert len(a) == 384


def test_local_missing_dependency_falls_back_with_actionable_reason():
    embedder = Embedder("local")
    if embedder.mode == "hash":
        assert embedder.fallback_reason
        assert "levh[local]" in embedder.fallback_reason


# ── Fallback visibility (issue #519) ────────────────────────────────
#
# A semantic request that resolved to hash changed ranking behaviour
# silently. These tests pin the three surfaces that must carry the fact:
# the instance (``fallback``), a model-free probe (``probe_embedder_status``)
# for code that runs before the lazily-created embedder exists, and the
# warning emitted when a provider degrades at runtime.


def test_explicit_hash_request_is_a_choice_not_a_fallback():
    embedder = Embedder("hash")

    # Even if some path recorded a reason, an explicit hash request never
    # reports as degraded: the operator asked for what is running.
    embedder.fallback_reason = "leftover reason"
    assert embedder.fallback is False


def test_auto_resolution_without_local_reports_fallback(monkeypatch):
    monkeypatch.setattr(embedder_module, "local_embedder_available", lambda: False)

    status = probe_embedder_status("auto")

    assert status["requested_mode"] == "auto"
    assert status["effective_mode"] == "hash"
    assert status["fallback"] is True
    # A fallback must never carry a null reason — that was the whole bug.
    assert status["fallback_reason"]
    assert "levh[local]" in status["fallback_reason"]


def test_explicit_hash_probe_reports_no_fallback(monkeypatch):
    monkeypatch.setattr(embedder_module, "local_embedder_available", lambda: False)

    status = probe_embedder_status("hash")

    assert status["effective_mode"] == "hash"
    assert status["fallback"] is False
    assert status["fallback_reason"] is None


def test_probe_warns_once_per_reason_not_per_poll(monkeypatch, caplog):
    """Readiness polls every 30s; the warning must not become a log flood.

    One line per distinct fallback reason per process is what issue #519
    asked for — the next operator reading ``serve.err.log`` finds it
    without digging through the healthcheck's repetitions.
    """
    monkeypatch.setattr(embedder_module, "local_embedder_available", lambda: False)
    monkeypatch.setattr(embedder_module, "_PROBE_WARNED", set())

    with caplog.at_level("WARNING", logger="levh.embedder"):
        for _ in range(5):
            probe_embedder_status("auto")
        probe_embedder_status("local")

    fallback_warnings = [
        r for r in caplog.records if "non-semantic hash" in r.message
    ]
    # "auto" and "local" resolve to the same reason string, so the second
    # probe must reuse the warning the first one emitted.
    assert len(fallback_warnings) == 1


def test_probe_matches_instance_resolution_for_auto(monkeypatch):
    """The probe must not disagree with what the instance will actually do."""
    monkeypatch.setattr(embedder_module, "local_embedder_available", lambda: False)

    status = probe_embedder_status("auto")
    embedder = Embedder("auto")

    if embedder.mode == "hash":
        # No sentence-transformers in this environment: the probe must have
        # predicted the instance's fallback.
        assert status["effective_mode"] == "hash"
        assert embedder.fallback is True
    else:
        # Local model present: the probe must not have cried fallback.
        assert status["effective_mode"] == "local"
        assert status["fallback"] is False


class _ExplodingClient:
    """An httpx stand-in whose every request fails like a down endpoint."""

    async def post(self, *_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_ollama_fallback_records_reason_and_warns(monkeypatch, caplog):
    """A runtime degrade must record *why* and say so once, not vanish."""
    embedder = Embedder("ollama")
    monkeypatch.setattr(embedder, "_client", lambda: _ExplodingClient())

    with caplog.at_level("WARNING", logger="levh.embedder"):
        vector = await embedder._ollama_embed("some text")

    assert embedder.mode == "hash"
    assert embedder.fallback_reason
    assert "Ollama" in embedder.fallback_reason
    assert embedder.fallback is True
    assert len(vector) == 384
    assert any("degrading to hash" in record.message for record in caplog.records)
