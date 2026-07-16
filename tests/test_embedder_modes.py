"""Embedder-mode behavior that must stay clear for public users."""

from __future__ import annotations

from server.core.embedder import Embedder


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
        assert "stackmemory[local]" in embedder.fallback_reason
