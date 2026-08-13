"""Entity graph, trust and conflict candidates — thin delegates to the services.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations


from ..entity_index_service import EntityIndexService
from ..types import (
    Memory,
)


class MemoryGraphMixin:
    """Entity graph, trust and conflict candidates — thin delegates to the services."""

    async def reindex_entities(self) -> dict:
        """Rebuild the persistent entity graph from every stored memory:
        extract typed entities (person / organization / event / document /
        task) and their memory links, so graph queries ("which memories
        mention X", "which entities co-occur with X") run as a join instead of
        a full re-scan. Idempotent — clears and rebuilds."""
        return await self.entity_index.reindex_entities()

    async def list_entities_graph(
        self, entity_type: str | None = None, limit: int = 200
    ) -> list[dict]:
        """List persisted entities (optionally filtered by type), most-mentioned
        first. Distinct from ``list_people`` — this reads the persistent graph."""
        await self._ensure_derived_state()
        return await self.entity_index.list_entities_graph(entity_type=entity_type, limit=limit)

    async def get_entity(self, query: str, entity_type: str | None = None) -> dict | None:
        """Resolve a query to a persisted entity and return its profile: the
        memories that mention it (newest first) and the entities it co-occurs
        with (its graph neighbours)."""
        await self._ensure_derived_state()
        return await self.entity_index.get_entity(query, entity_type=entity_type)

    async def entity_graph_stats(self) -> dict:
        """Counts of persisted entities by type."""
        await self._ensure_derived_state()
        return await self.entity_index.entity_graph_stats()

    @staticmethod
    def _build_entity_index(memories: list[Memory]) -> dict[str, list[tuple[str, str]]]:
        """entity_id → list of (memory_id, source_type) across all memories."""
        return EntityIndexService.build_entity_index(memories)

    async def recompute_trust_scores(self) -> dict:
        """Compute and persist the provenance/trust score for every memory.

        Corroboration is drawn from the entity graph and remains independent of
        the H-score used for recall ranking.
        """
        return await self.trust_service.recompute_trust_scores()

    async def get_trust(self, memory_id: str) -> dict | None:
        """Return the stored trust breakdown, computing it on demand if absent."""
        await self._ensure_derived_state()
        return await self.trust_service.get_trust(memory_id)

    async def list_low_trust(self, threshold: float = 0.4, limit: int = 50) -> list[dict]:
        """Return stored memories below the requested trust threshold."""
        await self._ensure_derived_state()
        return await self.trust_service.list_low_trust(
            threshold=threshold,
            limit=limit,
        )

    async def list_all_trust(self, limit: int = 1_000_000) -> list[dict]:
        """Return every stored trust breakdown, best score first."""
        await self._ensure_derived_state()
        return await self.trust_service.list_all_trust(limit=limit)

    async def detect_conflict_candidates(self) -> dict:
        """Detect deterministic conflict candidates for human review."""
        return await self.conflict_service.detect_conflict_candidates()

    async def list_conflict_candidates(
        self, status: str | None = "open", limit: int = 100
    ) -> list[dict]:
        await self._ensure_derived_state()
        return await self.conflict_service.list_conflict_candidates(
            status=status,
            limit=limit,
        )

    async def review_conflict_candidate(self, conflict_id: str, action: str) -> dict:
        """Apply a human review decision to a conflict candidate."""
        return await self.conflict_service.review_conflict_candidate(
            conflict_id,
            action,
        )
