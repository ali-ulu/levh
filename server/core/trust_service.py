"""Provenance and trust scoring service.

This module owns persistence and explainable trust-score orchestration. The
pure scoring primitives remain in ``server.core.trust``; this service binds
them to storage and the entity index without depending on ``MemoryEngine``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

from . import trust
from .database import Database
from .entity_index_service import EntityIndexService
from .episodic import EpisodicMemory
from .types import Memory

EventEmitter = Callable[[str, dict], None]


class TrustService:
    """Compute, persist, and query deterministic provenance trust scores."""

    def __init__(
        self,
        db: Database,
        episodic: EpisodicMemory,
        entity_index: EntityIndexService,
        emit: EventEmitter,
    ) -> None:
        self.db = db
        self.episodic = episodic
        self.entity_index = entity_index
        self._emit = emit

    def _trust_breakdown(
        self,
        memory: Memory,
        entity_index: dict,
        now_iso: str,
        conflict_status: str | None = None,
    ) -> dict:
        """Return the explainable trust breakdown for one memory."""
        entity_ids = self.entity_index.entity_ids_for(memory)
        self_type = trust.source_type(memory.source)

        corroborating_memory_ids: set[str] = set()
        source_types: set[str] = {self_type}
        for entity_id in entity_ids:
            for memory_id, source_type in entity_index.get(entity_id, []):
                if memory_id != memory.id:
                    corroborating_memory_ids.add(memory_id)
                    source_types.add(source_type)

        source = trust.source_score(memory.source, memory.pinned)
        corroboration = trust.corroboration_from_types(len(source_types))
        review = trust.review_score(memory)
        recency = trust.recency_score(memory, now_iso)
        risk = trust.risk_penalty(memory)

        conflict_extra = 0.0
        if conflict_status == "confirmed":
            conflict_extra = 0.25
        elif conflict_status == "open":
            conflict_extra = 0.15
        risk = round(min(1.0, risk + conflict_extra), 4)

        confidence = trust.confidence(source, corroboration, review, recency, risk)
        flags = trust.risk_flags(memory)
        if conflict_status in ("open", "confirmed"):
            flags = flags + [f"conflict_{conflict_status}"]
        label = trust.label_for(confidence)
        explanation = trust.build_explanation(
            self_type,
            sorted(source_types),
            len(corroborating_memory_ids),
            review,
            flags,
        )
        return {
            "memory_id": memory.id,
            "confidence": confidence,
            "label": label,
            "components": {
                "source_score": source,
                "corroboration_score": corroboration,
                "review_score": review,
                "recency_score": recency,
                "risk_penalty": risk,
            },
            "evidence": {
                "source": self_type,
                "linked_entities": entity_ids,
                "corroborating_memories": sorted(corroborating_memory_ids)[:5],
                "distinct_source_types": sorted(source_types),
                "conflict_status": conflict_status,
            },
            "explanation": explanation,
        }

    @staticmethod
    def _trust_row(breakdown: dict, now_iso: str) -> dict:
        components = breakdown["components"]
        return {
            "memory_id": breakdown["memory_id"],
            "confidence": breakdown["confidence"],
            "source_score": components["source_score"],
            "corroboration_score": components["corroboration_score"],
            "review_score": components["review_score"],
            "recency_score": components["recency_score"],
            "risk_penalty": components["risk_penalty"],
            "label": breakdown["label"],
            "computed_at": now_iso,
            "breakdown_json": json.dumps(breakdown),
        }

    async def _conflict_status_map(self) -> dict[str, str]:
        """Map each memory to its strongest active conflict status."""
        rank = {"confirmed": 2, "open": 1}
        statuses: dict[str, str] = {}
        for conflict in await self.db.list_conflicts(limit=100000):
            if conflict["status"] not in rank:
                continue
            for memory_id in (conflict["memory_id_a"], conflict["memory_id_b"]):
                if rank[conflict["status"]] > rank.get(statuses.get(memory_id, ""), 0):
                    statuses[memory_id] = conflict["status"]
        return statuses

    async def recompute_trust_scores(self) -> dict:
        """Compute and persist provenance trust scores for every memory."""
        now_iso = datetime.now(timezone.utc).isoformat()
        memories = await self.episodic.search(limit=1_000_000)
        entity_index = self.entity_index.build_entity_index(memories)
        conflict_map = await self._conflict_status_map()

        await self.db.clear_trust()
        by_label: dict[str, int] = {}
        for memory in memories:
            breakdown = self._trust_breakdown(
                memory,
                entity_index,
                now_iso,
                conflict_map.get(memory.id),
            )
            await self.db.upsert_trust(self._trust_row(breakdown, now_iso))
            label = breakdown["label"]
            by_label[label] = by_label.get(label, 0) + 1
        await self.db.commit()

        self._emit("trust_recomputed", {"scored": len(memories)})
        return {"scored": len(memories), "by_label": by_label}

    async def get_trust(self, memory_id: str) -> dict | None:
        """Return a stored trust breakdown, computing it on demand if absent."""
        row = await self.db.get_trust(memory_id)
        if row:
            self._emit("trust_viewed", {"memory_id": memory_id})
            return json.loads(row["breakdown_json"])

        memory = await self.episodic.get(memory_id)
        if not memory:
            return None
        now_iso = datetime.now(timezone.utc).isoformat()
        memories = await self.episodic.search(limit=1_000_000)
        entity_index = self.entity_index.build_entity_index(memories)
        conflict_map = await self._conflict_status_map()
        breakdown = self._trust_breakdown(
            memory,
            entity_index,
            now_iso,
            conflict_map.get(memory_id),
        )
        await self.db.upsert_trust(self._trust_row(breakdown, now_iso))
        await self.db.commit()
        self._emit("trust_viewed", {"memory_id": memory_id})
        return breakdown

    async def list_low_trust(
        self,
        threshold: float = 0.4,
        limit: int = 50,
    ) -> list[dict]:
        """Return stored memories below the requested trust threshold."""
        rows = await self.db.list_low_trust(
            threshold=threshold,
            limit=min(max(limit, 1), 500),
        )
        output = []
        for row in rows:
            try:
                output.append(json.loads(row["breakdown_json"]))
            except Exception:
                output.append(
                    {
                        "memory_id": row["memory_id"],
                        "confidence": row["confidence"],
                        "label": row["label"],
                    }
                )
        return output
