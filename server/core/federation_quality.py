"""Two-instance federation quality proof (#488, Phase B2).

B0 proved a signed envelope carries verified provenance; B1 proved the
pull-first transport refuses everything unverified. What neither proved is
the quality claim: that a memory imported from a peer is actually usable on
the receiver — recallable, decaying on the receiver's clock, and never
displacing a pinned local memory.

This module runs that scenario across two throwaway ``MemoryEngine``
instances and returns content-free metrics, so the result can be exposed in
the offline evaluation report without leaking raw memory content. Every
value in the report is a count or a boolean: no ids (random per run, which
would break report determinism), no timestamps, no content.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from server.core.federation import import_verified_envelope, sign_envelope
from server.core.full_export import build_full_export
from server.core.hscore import DEFAULT_HALF_LIFE_HOURS
from server.core.memory_engine import MemoryEngine

#: Distinctive, benign wording shared by the scenario and its tests. Fixed
#: strings keep the proof deterministic; the admission gate only rejects
#: exact duplicates, and each database in the scenario starts empty.
PEER_FACT = "The partner warehouse cycle count happens every third Tuesday"
LOCAL_PINNED_FACT = "The local oncall rotation starts on Monday morning"
PEER_NODE_ID = "peer-b2"


async def _open(db_path: str, embedder_mode: str) -> MemoryEngine:
    engine = MemoryEngine(
        db_path=db_path, embedder_mode=embedder_mode, short_term_max=10
    )
    await engine.initialize()
    return engine


def _row_by_content(rows: list, content: str):
    return next(m for m in rows if m.content == content)


async def prove_federation_quality(
    workdir: str | os.PathLike, embedder_mode: str = "hash"
) -> dict:
    """Run the B2 scenario and return metrics plus in-run evidence.

    The ``metrics`` mapping is the content-free, deterministic part suitable
    for the evaluation report. The ``evidence`` mapping carries the row-level
    detail the acceptance tests assert on (ids are only meaningful inside
    the run that produced them).
    """
    workdir = Path(workdir)
    sender = await _open(str(workdir / "sender.db"), embedder_mode)
    receiver = await _open(str(workdir / "receiver.db"), embedder_mode)
    try:
        # Receiver first: a pinned local memory the import must not displace.
        await receiver.store(
            LOCAL_PINNED_FACT,
            memory_type="episodic",
            importance=0.9,
            pinned=True,
        )

        # Sender owns the peer fact — pinned, recalled, lived-in. The import
        # must reset all of that receiver-side authority, not carry it over.
        await sender.store(
            PEER_FACT, memory_type="episodic", importance=0.7, pinned=True
        )
        await sender.recall(PEER_FACT)
        sender_rows = await sender.list_memories(limit=50)
        sender_fact = _row_by_content(sender_rows, PEER_FACT)

        key_path = workdir / "peer.key"
        key_path.write_text("b2-operator-secret", encoding="utf-8")
        bundle = await build_full_export(sender)
        envelope = sign_envelope(
            bundle,
            node_id=PEER_NODE_ID,
            key_path=str(key_path),
            algorithm="hmac-sha256",
        )

        import_started_at = datetime.now(timezone.utc).isoformat()
        result = await import_verified_envelope(
            receiver,
            envelope,
            key_path=str(key_path),
            expected_node_id=PEER_NODE_ID,
        )

        receiver_rows = await receiver.list_memories(limit=50)
        imported = _row_by_content(receiver_rows, PEER_FACT)
        local = _row_by_content(receiver_rows, LOCAL_PINNED_FACT)

        recalled_ids = [
            m.id
            for m in (
                await receiver.recall(PEER_FACT, top_k=5, reinforce=False)
            ).memories
        ]
        local_recalled_ids = [
            m.id
            for m in (
                await receiver.recall(LOCAL_PINNED_FACT, top_k=5, reinforce=False)
            ).memories
        ]

        imported_recallable = imported.id in recalled_ids
        lifecycle_reset = (
            sender_fact.pinned is True
            and sender_fact.recall_count > 0
            and imported.pinned is False
            and imported.recall_count == 0
            and imported.hscore is None
            and imported.decay_factor == 1.0
            and imported.frequency == 1
            and imported.stability_hours == DEFAULT_HALF_LIFE_HOURS
            and imported.accessed_at >= import_started_at
            and imported.content == sender_fact.content
            and imported.importance == sender_fact.importance
            and (imported.metadata or {}).get("federation", {}).get("verified")
            is True
            and (imported.metadata or {}).get("federation", {}).get("node_id")
            == PEER_NODE_ID
        )
        pinned_preserved = (
            local.pinned is True and local.id in local_recalled_ids
        )

        metrics = {
            "scenario": "federation-b2-two-instance",
            "peer_memories": 1,
            "imported": int(result.get("imported", 0)),
            "imported_recallable": bool(imported_recallable),
            "lifecycle_reset": bool(lifecycle_reset),
            "pinned_preserved": bool(pinned_preserved),
        }
        metrics["all_passed"] = all(
            [
                metrics["imported"] == 1,
                metrics["imported_recallable"],
                metrics["lifecycle_reset"],
                metrics["pinned_preserved"],
            ]
        )
        return {
            "metrics": metrics,
            "evidence": {
                "imported_id": imported.id,
                "local_id": local.id,
                "sender_pinned": sender_fact.pinned,
                "sender_recall_count": sender_fact.recall_count,
            },
        }
    finally:
        await sender.shutdown()
        await receiver.shutdown()


async def run_federation_quality(embedder_mode: str = "hash") -> dict:
    """Content-free B2 metrics for the offline evaluation report (#488).

    Throwaway databases in a temp dir; nothing stored, nothing returned but
    counts and booleans. Deterministic for a fixed code tree.
    """
    with tempfile.TemporaryDirectory(prefix="levh-fed-quality-") as tmp:
        proof = await prove_federation_quality(tmp, embedder_mode)
    return proof["metrics"]
