"""Deterministic conflict-candidate orchestration service.

The pure text comparison primitives remain in ``server.core.conflict``. This
service binds them to storage, entity overlap, review side effects, and event
emission without depending on ``MemoryEngine``.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from . import conflict, trust
from .database import Database
from .entity_index_service import EntityIndexService
from .episodic import EpisodicMemory
from .types import Memory

EventEmitter = Callable[[str, dict], None]
FeedbackMemory = Callable[[str, bool], Awaitable[Memory | None]]
DirtyMarker = Callable[[], None]


class ConflictService:
    """Detect, list, and review deterministic conflict candidates."""

    def __init__(
        self,
        db: Database,
        episodic: EpisodicMemory,
        entity_index: EntityIndexService,
        emit: EventEmitter,
        feedback_memory: FeedbackMemory,
        mark_derived_dirty: DirtyMarker,
    ) -> None:
        self.db = db
        self.episodic = episodic
        self.entity_index = entity_index
        self._emit = emit
        self._feedback_memory = feedback_memory
        self._mark_derived_dirty = mark_derived_dirty

    async def detect_conflict_candidates(self) -> dict:
        """Detect review candidates without deciding truth or deleting memory."""
        now_iso = datetime.now(timezone.utc).isoformat()
        memories = await self.episodic.search(limit=1_000_000)
        by_id = {memory.id: memory for memory in memories}

        entity_to_memories: dict[str, set[str]] = {}
        for memory in memories:
            for entity_id in self.entity_index.entity_ids_for(memory):
                entity_to_memories.setdefault(entity_id, set()).add(memory.id)

        pair_entities: dict[tuple[str, str], set[str]] = {}
        for entity_id, memory_ids in entity_to_memories.items():
            ordered_ids = sorted(memory_ids)
            for left in range(len(ordered_ids)):
                for right in range(left + 1, len(ordered_ids)):
                    pair_entities.setdefault(
                        (ordered_ids[left], ordered_ids[right]),
                        set(),
                    ).add(entity_id)

        trust_rows = {
            row["memory_id"]: row
            for row in await self.db.list_low_trust(
                threshold=2.0,
                limit=100000,
            )
        }
        new_count = 0
        current_candidate_ids: set[str] = set()
        for (memory_id_a, memory_id_b), shared_entities in pair_entities.items():
            memory_a = by_id[memory_id_a]
            memory_b = by_id[memory_id_b]
            signal = conflict.opposing_signal(
                memory_a.content or "",
                memory_b.content or "",
            )
            if not signal:
                continue
            signal_type, detail = signal
            distinct_source_types = len(
                {
                    trust.source_type(memory_a.source),
                    trust.source_type(memory_b.source),
                }
            )
            confidence = conflict.candidate_confidence(
                signal_type,
                distinct_source_types,
            )
            explanation = {
                "signal_type": signal_type,
                "detail": detail,
                "shared_entities": sorted(shared_entities),
                "a_preview": (memory_a.content or "").split("\n", 1)[0][:100],
                "b_preview": (memory_b.content or "").split("\n", 1)[0][:100],
                "a_source": memory_a.source,
                "b_source": memory_b.source,
                "a_trust": trust_rows.get(memory_id_a, {}).get("confidence"),
                "b_trust": trust_rows.get(memory_id_b, {}).get("confidence"),
                "note": "Conflict CANDIDATE for human review — not a verdict.",
            }
            candidate_id = f"{memory_id_a}|{memory_id_b}"
            current_candidate_ids.add(candidate_id)
            row = {
                "id": candidate_id,
                "memory_id_a": memory_id_a,
                "memory_id_b": memory_id_b,
                "shared_entities_json": json.dumps(sorted(shared_entities)),
                "signal_type": signal_type,
                "confidence": confidence,
                "status": "open",
                "explanation_json": json.dumps(explanation),
                "created_at": now_iso,
            }
            if await self.db.insert_conflict_if_absent(row):
                new_count += 1

        stale_pruned = 0
        for existing in await self.db.list_conflicts(status="open", limit=100000):
            if existing["id"] not in current_candidate_ids:
                if await self.db.delete_conflict(existing["id"]):
                    stale_pruned += 1
        await self.db.commit()

        open_total = len(
            await self.db.list_conflicts(status="open", limit=100000)
        )
        self._emit("conflicts_detected", {"new": new_count, "open": open_total})
        return {
            "new_candidates": new_count,
            "pairs_examined": len(pair_entities),
            "open_total": open_total,
            "stale_pruned": stale_pruned,
        }

    @staticmethod
    def _conflict_out(row: dict) -> dict:
        return {
            "id": row["id"],
            "memory_id_a": row["memory_id_a"],
            "memory_id_b": row["memory_id_b"],
            "signal_type": row["signal_type"],
            "confidence": row["confidence"],
            "status": row["status"],
            "shared_entities": json.loads(row.get("shared_entities_json") or "[]"),
            "explanation": json.loads(row.get("explanation_json") or "{}"),
            "created_at": row.get("created_at"),
            "reviewed_at": row.get("reviewed_at"),
        }

    async def list_conflict_candidates(
        self,
        status: str | None = "open",
        limit: int = 100,
    ) -> list[dict]:
        rows = await self.db.list_conflicts(
            status=status,
            limit=min(max(limit, 1), 1000),
        )
        return [self._conflict_out(row) for row in rows]

    async def review_conflict_candidate(
        self,
        conflict_id: str,
        action: str,
    ) -> dict:
        """Apply a human review decision without auto-deleting either memory."""
        valid = {
            "dismiss": "dismissed",
            "confirm": "confirmed",
            "resolve_keep_a": "resolved",
            "resolve_keep_b": "resolved",
            "mark_both_valid": "resolved",
            "human_review": "open",
        }
        if action not in valid:
            raise ValueError(
                f"invalid conflict action '{action}'; expected one of {sorted(valid)}"
            )

        row = await self.db.get_conflict(conflict_id)
        if not row:
            return {"ok": False, "error": "not found", "conflict_id": conflict_id}

        now_iso = datetime.now(timezone.utc).isoformat()
        if action == "resolve_keep_a":
            await self._feedback_memory(row["memory_id_b"], False)
        elif action == "resolve_keep_b":
            await self._feedback_memory(row["memory_id_a"], False)

        await self.db.update_conflict_status(conflict_id, valid[action], now_iso)
        self._mark_derived_dirty()
        updated = await self.db.get_conflict(conflict_id)
        self._emit("conflict_reviewed", {"id": conflict_id, "action": action})
        return {
            "ok": True,
            "action": action,
            "conflict": self._conflict_out(updated),
        }
