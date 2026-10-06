"""Federation recall-quality proof (#488, Phase B2).

B0 proved a signed envelope carries verified provenance and B1 proved the
pull-first transport refuses everything unverified. This file proves the
remaining quality claim across two throwaway instances: the imported peer
memory is recallable on the receiver, it decays on the receiver's clock
(sender lifecycle is reset, not carried over), and a pinned local memory
is not displaced by the import. The metrics are content-free, so the same
proof feeds the offline evaluation report without leaking memory content.
All offline: hash embedder, no LLM, no network.
"""

from __future__ import annotations

import json

import pytest

from server.core.evaluation import run_evaluation
from server.core.federation_quality import (
    LOCAL_PINNED_FACT,
    PEER_FACT,
    prove_federation_quality,
    run_federation_quality,
)


@pytest.mark.asyncio
async def test_imported_peer_memory_is_recallable_on_the_receiver(tmp_path):
    proof = await prove_federation_quality(tmp_path)
    assert proof["metrics"]["imported"] == 1
    assert proof["metrics"]["imported_recallable"] is True


@pytest.mark.asyncio
async def test_receiver_lifecycle_is_authoritative_not_the_sender(tmp_path):
    """Pin, recall history and decay clock must not cross the peer boundary."""
    proof = await prove_federation_quality(tmp_path)
    assert proof["evidence"]["sender_pinned"] is True
    assert proof["evidence"]["sender_recall_count"] > 0
    assert proof["metrics"]["lifecycle_reset"] is True


@pytest.mark.asyncio
async def test_pinned_local_memory_survives_the_import(tmp_path):
    proof = await prove_federation_quality(tmp_path)
    assert proof["metrics"]["pinned_preserved"] is True


@pytest.mark.asyncio
async def test_the_proof_is_deterministic(tmp_path):
    first = await run_federation_quality()
    second = await run_federation_quality()
    assert first["all_passed"] is True
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


@pytest.mark.asyncio
async def test_evaluation_report_exposes_federation_without_content():
    """The offline report carries the B2 result as counts/booleans only."""
    report = await run_evaluation()
    section = report["federation"]
    assert section["scenario"] == "federation-b2-two-instance"
    assert section["all_passed"] is True
    serialized = json.dumps(report).lower()
    assert PEER_FACT.lower() not in serialized
    assert LOCAL_PINNED_FACT.lower() not in serialized
