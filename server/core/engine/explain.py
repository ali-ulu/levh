"""Explaining a score: the H(x,psi) breakdown behind a recall.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations


from ..types import (
    ScoreBreakdown,
)


class MemoryExplainMixin:
    """Explaining a score: the H(x,psi) breakdown behind a recall."""

    async def score_breakdown(
        self, memory_id: str, query: str
    ) -> ScoreBreakdown | None:
        """Get individual H(x,ψ) components for a memory vs query."""
        memory = await self.episodic.get(memory_id)
        if not memory or not memory.embedding:
            return None

        query_embedding = await self.embedder.embed(query)
        import numpy as np

        vec_mem = np.array(memory.embedding, dtype=np.float32)
        vec_q = np.array(query_embedding, dtype=np.float32)
        cosine = float(
            np.dot(vec_mem, vec_q)
            / (np.linalg.norm(vec_mem) * np.linalg.norm(vec_q) + 1e-8)
        )

        decay = (
            1.0
            if memory.pinned
            else self.scorer.compute_decay(memory.accessed_at, half_life_hours=memory.stability_hours)
        )
        bd = self.scorer.breakdown(
            similarity=cosine,
            decay_factor=decay,
            importance=memory.importance,
            frequency=memory.frequency,
        )

        total = self.scorer.compute(
            similarity=cosine,
            decay_factor=decay,
            importance=memory.importance,
            frequency=memory.frequency,
        )

        return ScoreBreakdown(
            memory_id=memory_id,
            content_snippet=memory.content[:100],
            total_hscore=total,
            alpha_component=bd["alpha_component"],
            beta_component=bd["beta_component"],
            gamma_component=bd["gamma_component"],
            delta_component=bd["delta_component"],
        )
