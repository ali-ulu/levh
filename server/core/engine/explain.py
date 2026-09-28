"""Explaining a score: the H(x,psi) breakdown behind a recall.

Part of :class:`server.core.memory_engine.MemoryEngine`, split out to keep
each file readable. Mixins rather than separate services: the methods use the
engine's own state throughout, and moving the bodies unchanged is what makes
the split verifiable.
"""

from __future__ import annotations

import numpy as np

from ..hscore import SUPERSEDED_PENALTY
from ..types import (
    ScoreBreakdown,
)


class MemoryExplainMixin:
    """Explaining a score: the H(x,psi) breakdown behind a recall."""

    async def score_breakdown(
        self, memory_id: str, query: str
    ) -> ScoreBreakdown | None:
        """Get individual H(x,ψ) components for a memory vs query.

        Single source of truth for the breakdown math — the REST route
        (GET /api/memories/{id}/score-breakdown) delegates here so cosine/
        decay/scoring cannot drift between the two surfaces.

        Raises:
            ValueError: the stored embedding's dimension differs from the
                active embedder's output (e.g. embedder mode changed since
                storage). Callers surface this as a client error.
        """
        memory = await self.episodic.get(memory_id)
        if not memory or not memory.embedding:
            return None

        query_embedding = await self.embedder.embed(query)

        vec_mem = np.asarray(memory.embedding, dtype=np.float64)
        vec_q = np.asarray(query_embedding, dtype=np.float64)
        if vec_mem.shape != vec_q.shape:
            raise ValueError(
                "embedding dimension mismatch (embedder mode changed since storage)"
            )
        norm_mem = np.linalg.norm(vec_mem)
        norm_q = np.linalg.norm(vec_q)
        cosine = float(np.dot(vec_mem, vec_q) / max(norm_mem * norm_q, 1e-9))
        cosine = max(0.0, min(1.0, cosine))

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
        # Same demotion recall applies, so this route reports the score the
        # memory actually ranks with rather than the pre-penalty one.
        superseded_penalty = (
            SUPERSEDED_PENALTY
            if (memory.metadata or {}).get("superseded_by")
            else 0.0
        )
        total = min(1.0, total + superseded_penalty)

        return ScoreBreakdown(
            memory_id=memory_id,
            content_snippet=memory.content[:100],
            total_hscore=total,
            alpha_component=bd["alpha_component"],
            beta_component=bd["beta_component"],
            gamma_component=bd["gamma_component"],
            delta_component=bd["delta_component"],
            superseded_penalty=superseded_penalty,
        )
